#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스마트 분리수거기 통합 제어기 (라즈베리파이 + Picamera2).

파이썬 3.10+ / GPIO Zero / Picamera2 / NumPy / Pillow / LiteRT or TFLite Runtime
순서: 서보 원위치 강제 신호 -> IR/투입 초음파 OR 안정 감지 -> 금속 우선 검사
    -> (비금속만) 카메라 5프레임·수분 검증 -> 분류판 위치 선정 -> 측정판 배출
    -> 서보 복귀 -> 리워드 적립 -> 1회 분류 자동 종료 시 초음파 3개 통 각각 1회 검사

보안/기계 안전: 서보에는 위치 피드백이 없고 초음파는 실제 충만도를 직접 보증하지 않는다.
최초 운전 전 --check-config, --self-test 및 무부하 각도/센서 실측이 필수다.
"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import logging
import math
import re
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from typing import Any

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("sorter")
KOREAN = {"plastic": "플라스틱", "can": "캔", "paper": "종이"}
ALIASES = {"plastic": "plastic", "플라스틱": "plastic",
           "can": "can", "캔": "can", "paper": "paper", "종이": "paper"}

# ===================== 사용자 설정 (BCM GPIO 번호) =====================
MODEL_PATH = BASE / "model_unquant.tflite"
LABELS_PATH = BASE / "labels.txt"
MODEL_CONFIDENCE = 0.70
PAPER_PLASTIC_AMBIGUITY = 0.30  # '실제 혼합' 검출이 아닌 모델의 판정 불확실성
CLASSIFY_FRAMES = 5
SETTLE_SECONDS = 1.0
CAMERA_SIZE = (640, 480)
CAMERA_FPS = 7
CAMERA_START_TIMEOUT = 12.0

# 측정판: BCM12(물리 32), 분류판: BCM18(물리 12). 기존 파일의 번호 유지.
TRAY_GPIO = 12
TRAY_HOME = -10        # 2026-11-25 실물 보정: 측정판 수평
TRAY_LEFT = 50        # 실제 왼쪽 배출은 +50도
TRAY_RIGHT = -75      # 실제 오른쪽 배출은 -75도
DIVERTER_GPIO = 18
# 2026-11-25 실제 서보 탐색 결과: 분류판은 60°(원위치) / 90°(오른쪽) 두 상태만 사용.
DIVERTER_HOME = 60
DIVERTER_RIGHT = 90
SERVO_MIN_PULSE = 0.0005
SERVO_MAX_PULSE = 0.0025
SERVO_MOVE_SECONDS = 1.0
DIVERTER_SETTLE_SECONDS = 1.0
DROP_SECONDS = 1.5
HOME_SETTLE_SECONDS = 0.5
STARTUP_HOME_RETRIES = 2    # 정상 실행 시작 시 두 판에 각각 원위치 명령을 두 번 강제 전송
STARTUP_HOME_PAUSE = 0.4    # 1차 명령 후 서보 전원/기구 안정화 시간

# 초음파: (TRIG BCM, ECHO BCM), 각 수거함 상단에 설치
ULTRASONIC_PINS = {"plastic": (14, 9), "can": (21, 4), "paper": (13, 19)}
# 이번 실측 기준으로 최초 일반 실행에 자동으로 3통의 빈 통 거리(0%)를 보정한다.
# 저장된 파일이 있으면 다음 실행부터는 그 기준을 그대로 쓰므로 재시작해도 0%로 재설정되지 않는다.
# 기존 버전의 임시 30cm 보정 파일은 무시하고 새 파일에 기록 (실측값으로 완전히 교체).
EMPTY_CM = {"plastic": 30.0, "can": 30.0, "paper": 30.0}  # 최초 실행 전 유효성 검사용 값
EMPTY_CALIBRATION_PATH = BASE / "empty_bins_zero_reference.json"
FULL_CM = {"plastic": 5.0, "can": 5.0, "paper": 5.0}  # 임시 100% 기준: 반드시 통별 실물 보정
FILL_WARNING_PERCENT = 80.0
FILL_BLOCK_PERCENT = 95.0  # 시작 검사 결과가 95% 이상이면 해당 통 투입 차단
ULTRA_SAMPLES = 5  # 통별 1회 측정 = 5개 펄스 중앙값 (시작 1회 + 종료 1회)
ULTRA_MIN_GOOD = 3
ULTRA_ECHO_TIMEOUT = 0.03
ULTRA_SAMPLE_PAUSE = 0.07
ULTRA_MIN_CM, ULTRA_MAX_CM = 2.0, 400.0

# 투입 감지 센서 2개를 동시에 사용: IR 또는 4번째 초음파 중 하나라도 감지하면 1회 분류.
# 양쪽 모두 연결된 상태를 기본으로 하며, 단독 확인용 "ir"/"ultrasonic"도 지원.
ENTRY_MODE = "both"  # "both"(IR OR 초음파) / "ultrasonic" / "ir" / "manual"
# GPIO23은 실제로 RGB1 초록에 사용하므로 투입용 초음파 TRIG를 GPIO5로 변경.
# 중요: 투입 초음파의 TRIG 선도 실제로 BCM23에서 BCM5(물리 29)로 옮겨야 합니다.
ENTRY_TRIG_GPIO, ENTRY_ECHO_GPIO = 5, 24
ENTRY_DETECT_MAX_CM = 35.0    # 최대 감지 거리. 더 멀리 감지하려면 이 값 조절
ENTRY_RELEASE_CM = ENTRY_DETECT_MAX_CM + 5.0  # 감지 범위를 늘려도 해제 거리는 자동으로 5cm 더 크게
ENTRY_BACKGROUND_DELTA_CM = 2.5  # 빈 측정판보다 최소 2.5cm 가까워야 물체로 판정
ENTRY_CLEAR_DELTA_CM = 1.2       # 물체가 사라졌다고 판단하는 기준
ENTRY_BASELINE_SAMPLES = 7       # 시작 시 비어 있는 측정판 거리 보정 횟수
ENTRY_SAMPLE_GAP_S = 0.07       # 초음파 발사 사이 간격(센서 보호·반사 간섭 완화)
ENTRY_STABLE_SECONDS = 0.15     # 물체가 일정 시간 연속 감지되어야 투입 확정
ENTRY_LOG_INTERVAL_S = 2.0     # 대기 중 거리 정보 출력 간격
IR_GPIO = 17
IR_ACTIVE_LOW = True
IR_STABLE_SECONDS = 0.15
IR_POLL_SECONDS = 0.04
METAL_GPIO = 26
METAL_ACTIVE_LOW = True
METAL_VOTES = 7
# 요청사항: 금속 신호가 한 번이라도 검출되면 캔으로 확정 (AI 검사 생략).
# 순간적인 잡음에도 캔으로 분류될 수 있으므로 실측 보정 시 2~3으로 높일 수 있음.
METAL_MIN_HITS = 1
METAL_SAMPLE_SECONDS = 0.02

# 이전 코드의 MCP3208 배선 설정은 보존. 미연결 장비는 False로 유지.
MOISTURE_ENABLED = False
MOISTURE_ADC = {"channel": 0, "max_voltage": 3.3,
                "clock_pin": 11, "mosi_pin": 10,
                "miso_pin": 25, "select_pin": 8}
MOISTURE_SAMPLES = 5
MOISTURE_WET_RAW = 2000  # 실측한 건조/젖음 값으로 보정 필수
MOISTURE_WET_WHEN_HIGH = False

# 2026-11-25 사용자 실물 점등 검증 완료: 4핀 RGB(-), 공통 캐소드(GND).
# RGB1(분류): 빨강 GPIO22 / 초록 GPIO23 / 파랑 GPIO7
STATUS_RGB_PINS = {"red": 22, "green": 23, "blue": 7}
# RGB2(적재량): 빨강 GPIO2 / 초록 GPIO3 / 파랑 미연결
# 따라서 정상 상태는 파랑 대신 초록으로 표시하고, 실측 적재량 80% 이상일 때만 빨강 표시.
# 센서 측정 실패 자체로 빨강을 켜지 않으며, 마지막 유효 측정값을 보존해 판단한다.
FILL_RGB_PINS = {"red": 2, "green": 3}
STATUS_RGB_ACTIVE_HIGH = True
FILL_RGB_ACTIVE_HIGH = True
# 현재 요청대로 적재량 측정은 시작/종료에만 수행한다.
# 종료 시 검사 결과를 눈으로 확인할 수 있게 표시하는 시간(이후 GPIO 해제 시 소등).
FILL_FINAL_DISPLAY_SECONDS = 5.0
ALERT_GPIO = None  # 별도 부저/경고등을 연결했을 때만 번호 지정
RESULT_SECONDS = 3.0

# 브라우저 실시간 확인: http://<라즈베리파이-IP>:8000
WEB_ENABLED = True
WEB_BIND = "0.0.0.0"  # 같은 네트워크에서도 볼 수 있음. 인터넷에 포트 공개 금지
WEB_PORT = 8000

# 리워드 적립: campus-recycle 저장소를 clone한 위치의 recycle-web 폴더 (data.py가 있는 곳)
REWARD_DIR = Path.home() / "campus-recycle" / "recycle-web"


# ===================== 공통 함수 =====================
class SafetyHold(Exception):
    """판정을 확정할 수 없거나 배출하면 안 되는 상태 (비고장)."""


def record_reward(kind: str) -> None:
    """배출이 끝난 뒤, QR로 연결된 학생에게 포인트를 적립한다.

    네트워크·DB 오류가 나도 분류기 동작에는 영향을 주지 않도록 모든 예외를 기록만 한다.
    """
    try:
        if str(REWARD_DIR) not in sys.path:
            sys.path.insert(0, str(REWARD_DIR))
        from data import active_student, save_record
        student_id = active_student()
        if student_id is None:
            LOG.info("[리워드] QR로 연결된 학생이 없어 적립하지 않음")
            return
        points = save_record(student_id, KOREAN[kind])
        LOG.info("[리워드] %s %s", KOREAN[kind],
                 f"+{points}P 적립 완료" if points else "오늘 적립 한도 초과 (포인트 없음)")
    except Exception:
        LOG.exception("[리워드] 적립 실패 (분류 동작에는 영향 없음)")


def fmt_scores(scores: dict[str, float]) -> str:
    return ", ".join(f"{KOREAN[k]} {scores[k]:.1%}" for k in KOREAN)


def validate_settings() -> None:
    assert 0 < MODEL_CONFIDENCE <= 1
    assert 0 <= PAPER_PLASTIC_AMBIGUITY < 0.5
    assert CLASSIFY_FRAMES >= 3 and CAMERA_FPS >= 2
    assert 0 <= FILL_WARNING_PERCENT < FILL_BLOCK_PERCENT <= 100
    assert 1 <= ULTRA_MIN_GOOD <= ULTRA_SAMPLES
    assert DROP_SECONDS > 0 and SERVO_MOVE_SECONDS > 0
    assert STARTUP_HOME_RETRIES >= 1 and STARTUP_HOME_PAUSE >= 0
    if ENTRY_MODE not in ("both", "ultrasonic", "ir", "manual"):
        raise ValueError("ENTRY_MODE는 both, ultrasonic, ir, manual 중 하나여야 합니다")
    if not (5 <= ENTRY_DETECT_MAX_CM < ENTRY_RELEASE_CM <= ULTRA_MAX_CM):
        raise ValueError("투입 초음파 거리: 5 <= 감지 최대 거리 < 해제 거리 <= 400 이어야 합니다")
    if not (0 < ENTRY_CLEAR_DELTA_CM < ENTRY_BACKGROUND_DELTA_CM < ENTRY_DETECT_MAX_CM):
        raise ValueError("투입 거리 차이(배경/해제) 설정이 잘못되었습니다")
    if ENTRY_BASELINE_SAMPLES < 3 or ENTRY_SAMPLE_GAP_S < 0.06:
        raise ValueError("투입 초음파 초기 샘플 수/펄스 간격을 확인하세요")
    if not 1 <= METAL_MIN_HITS <= METAL_VOTES:
        raise ValueError("METAL_MIN_HITS는 1 이상 METAL_VOTES 이하여야 합니다")
    for angle in (TRAY_HOME, TRAY_LEFT, TRAY_RIGHT, DIVERTER_HOME, DIVERTER_RIGHT):
        if not -90 <= angle <= 90:
            raise ValueError(f"서보 각도 범위 초과: {angle}")
    if len({TRAY_HOME, TRAY_LEFT, TRAY_RIGHT}) != 3:
        raise ValueError("측정판 홈/왼쪽/오른쪽 각도는 모두 달라야 합니다")
    if DIVERTER_HOME == DIVERTER_RIGHT:
        raise ValueError("분류판 원위치와 오른쪽 기울임 각도가 같습니다")
    if set(ULTRASONIC_PINS) != set(KOREAN) or set(EMPTY_CM) != set(KOREAN) or set(FULL_CM) != set(KOREAN):
        raise ValueError("3개 통의 초음파/거리 설정이 모두 있어야 합니다")
    for name in KOREAN:
        if EMPTY_CM[name] <= FULL_CM[name] or FULL_CM[name] <= 0:
            raise ValueError(f"{name}: EMPTY_CM은 FULL_CM보다 커야 합니다")
    assignments = [("측정판", TRAY_GPIO), ("분류판", DIVERTER_GPIO)]
    for name, (trig, echo) in ULTRASONIC_PINS.items():
        assignments += [(f"{name} TRIG", trig), (f"{name} ECHO", echo)]
    if ENTRY_MODE in ("both", "ultrasonic"):
        assignments += [("투입 초음파 TRIG", ENTRY_TRIG_GPIO), ("투입 초음파 ECHO", ENTRY_ECHO_GPIO)]
    for name, pin in (("IR", IR_GPIO if ENTRY_MODE in ("both", "ir") else None), ("금속", METAL_GPIO),
                      ("만재 경고", ALERT_GPIO)):
        if pin is not None:
            assignments.append((name, pin))
    if set(STATUS_RGB_PINS) != {"red", "green", "blue"}:
        raise ValueError("RGB1에는 red, green, blue GPIO 세 개가 필요합니다")
    if set(FILL_RGB_PINS) != {"red", "green"}:
        raise ValueError("RGB2는 blue 미연결이므로 red, green GPIO만 사용합니다")
    for group, pins in (("분류 상태 RGB", STATUS_RGB_PINS), ("적재량 RGB", FILL_RGB_PINS)):
        assignments += [(f"{group} {color}", pin) for color, pin in pins.items()]
    if MOISTURE_ENABLED:
        assignments += [(f"MCP3208 {name}", MOISTURE_ADC[name])
                        for name in ("clock_pin", "mosi_pin", "miso_pin", "select_pin")]
    seen: dict[int, str] = {}
    for label, pin in assignments:
        if not isinstance(pin, int) or not 0 <= pin <= 27:
            raise ValueError(f"GPIO 번호 오류: {label}={pin!r}")
        if pin in seen:
            raise ValueError(f"GPIO{pin}가 {seen[pin]}와 {label}에 중복 지정됨")
        seen[pin] = label
    if not 1 <= WEB_PORT <= 65535:
        raise ValueError("WEB_PORT 범위 오류")



def route_plan(kind: str) -> tuple[int, int, str, str]:
    """(분류판 목표각, 측정판 목표각, 분류판 설명, 측정판 방향).

    분류판은 캔일 때만 오른쪽으로 기울어지고, 왼쪽 명령은 존재하지 않는다.
    """
    if kind == "can":
        return DIVERTER_RIGHT, TRAY_LEFT, "오른쪽 기울임", "왼쪽"
    if kind == "plastic":
        return DIVERTER_HOME, TRAY_LEFT, "원위치 유지", "왼쪽"
    if kind == "paper":
        return DIVERTER_HOME, TRAY_RIGHT, "원위치 유지", "오른쪽"
    raise ValueError(f"알 수 없는 폐기물: {kind}")


def interpret_probs(values: Any) -> list[float]:
    """TFLite float 출력(확률 또는 logits)을 3개의 유효한 확률로 정규화."""
    import numpy as np
    p = np.asarray(values, dtype=np.float64).reshape(-1)
    if len(p) != 3 or not np.all(np.isfinite(p)):
        raise RuntimeError(f"AI 출력값 비정상: {p}")
    if np.all(p >= 0) and np.all(p <= 1) and abs(float(p.sum()) - 1.0) < 0.025:
        return (p / p.sum()).tolist()
    p = p - np.max(p)
    exp = np.exp(p)
    return (exp / exp.sum()).tolist()


def decide_camera(scores: dict[str, float]) -> tuple[str, str | None]:
    best = max(scores, key=scores.get)
    if scores["paper"] >= PAPER_PLASTIC_AMBIGUITY and scores["plastic"] >= PAPER_PLASTIC_AMBIGUITY:
        return best, "종이·플라스틱 판정이 모호함 (실제 혼합 여부는 미확인)"
    if scores[best] < MODEL_CONFIDENCE:
        return best, f"AI 신뢰도 부족 ({scores[best]:.1%} < {MODEL_CONFIDENCE:.0%})"
    return best, None


def decide_final(camera_kind: str | None, camera_reason: str | None,
                 metal_found: bool | None, wet: bool | None) -> tuple[str, str | None]:
    """금속 신호 감지 시 카메라 인식 성공/실패 여부와 무관하게 캔 확정."""
    if metal_found is True:
        return "can", None
    if camera_kind is None:
        return "unknown", "금속 미감지 및 카메라 판정 없음"
    kind, reason = camera_kind, camera_reason
    if metal_found is False and kind == "can" and reason is None:
        reason = "카메라는 캔으로 판정했으나 금속이 감지되지 않음"
    if wet is True and reason is None:
        reason = "물체에서 수분 감지"
    return kind, reason


def save_empty_calibration(values: dict[str, float]) -> None:
    """세 통 전부 유효하게 측정한 뒤에만 파일을 원자적으로 저장한다."""
    if set(values) != set(KOREAN):
        raise ValueError("빈 통 기준은 세 통을 모두 측정해야 저장할 수 있습니다")
    checked = {kind: round(float(values[kind]), 2) for kind in KOREAN}
    for kind, cm in checked.items():
        if not math.isfinite(cm) or not (FULL_CM[kind] + 2 < cm <= ULTRA_MAX_CM):
            raise ValueError(f"{KOREAN[kind]} 빈 통 기준이 비정상입니다: {cm} cm")
    temp = EMPTY_CALIBRATION_PATH.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(checked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(EMPTY_CALIBRATION_PATH)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    EMPTY_CM.update(checked)
    LOG.info("[0%% 기준 저장] %s", EMPTY_CALIBRATION_PATH)


def load_empty_calibration() -> bool:
    """앞서 저장한 실제 빈 통 거리 3개를 읽음. 파일이 없으면 최초 시작 때 보정한다."""
    if not EMPTY_CALIBRATION_PATH.exists():
        LOG.info("[0% 기준] 저장된 실측값이 없습니다. 이번 최초 시작에서 세 통을 0%로 보정합니다")
        return False
    try:
        data = json.loads(EMPTY_CALIBRATION_PATH.read_text(encoding="utf-8"))
        if set(data) != set(KOREAN):
            raise ValueError("플라스틱/캔/종이 3개 거리값이 모두 필요합니다")
        values = {kind: float(value) for kind, value in data.items()}
        for kind, distance in values.items():
            if not math.isfinite(distance) or not (FULL_CM[kind] + 2 < distance <= ULTRA_MAX_CM):
                raise ValueError(f"{kind} 거리값 오류: {distance}")
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"빈 통 보정 파일 오류({EMPTY_CALIBRATION_PATH}): {exc}") from exc
    EMPTY_CM.update(values)
    LOG.info("[0%% 기준 불러오기] %s", ", ".join(f"{KOREAN[k]} {v:.2f}cm" for k,v in values.items()))
    return True


def percent_full(kind: str, distance_cm: float) -> float:
    if not math.isfinite(distance_cm):
        raise ValueError("유효하지 않은 거리값")
    p = (EMPTY_CM[kind] - distance_cm) / (EMPTY_CM[kind] - FULL_CM[kind]) * 100
    return round(max(0.0, min(100.0, p)), 1)


def wet_from_raw(raw: int) -> bool:
    return raw >= MOISTURE_WET_RAW if MOISTURE_WET_WHEN_HIGH else raw <= MOISTURE_WET_RAW


# ===================== AI =====================
def interpreter_class():
    errors = []
    for module in ("ai_edge_litert.interpreter", "tflite_runtime.interpreter", "tensorflow.lite"):
        try:
            return getattr(importlib.import_module(module), "Interpreter")
        except (ImportError, AttributeError) as exc:
            errors.append(f"{module}: {exc}")
    raise RuntimeError("TFLite/LiteRT 실행 라이브러리가 없습니다: " + "; ".join(errors))


class Classifier:
    def __init__(self) -> None:
        import numpy as np
        if not MODEL_PATH.is_file():
            raise FileNotFoundError(f"AI 모델 파일 없음: {MODEL_PATH}")
        if not LABELS_PATH.is_file():
            raise FileNotFoundError(f"AI 라벨 파일 없음: {LABELS_PATH}")
        self.np = np
        self.interpreter = interpreter_class()(model_path=str(MODEL_PATH), num_threads=2)
        self.interpreter.allocate_tensors()
        inputs = self.interpreter.get_input_details()
        outputs = self.interpreter.get_output_details()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("단일 입력/출력 TFLite 분류 모델만 지원합니다")
        self.input = inputs[0]
        self.output = outputs[0]
        shape = tuple(map(int, self.input["shape"]))
        oshape = tuple(map(int, self.output["shape"]))
        if len(shape) != 4 or shape[0] != 1 or shape[3] != 3:
            raise ValueError(f"지원하지 않는 모델 입력 크기: {shape}")
        if np.dtype(self.input["dtype"]) != np.dtype(np.float32):
            raise ValueError("현재는 model_unquant.tflite float32 입력만 지원합니다. 양자화 모델은 전처리 조정이 필요합니다")
        if np.dtype(self.output["dtype"]) != np.dtype(np.float32):
            raise ValueError("현재는 float32 출력만 지원합니다")
        if oshape != (1, 3):
            raise ValueError(f"AI 출력 shape는 [1,3]이어야 합니다: {oshape}")
        self.size = (shape[2], shape[1])
        raw_labels = [x.strip() for x in LABELS_PATH.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        labels = [re.sub(r"^\d+\s+", "", x).lower() for x in raw_labels]
        try:
            self.kinds = [ALIASES[x] for x in labels]
        except KeyError as exc:
            raise ValueError(f"알 수 없는 라벨: {exc}") from exc
        if len(self.kinds) != 3 or set(self.kinds) != set(KOREAN):
            raise ValueError(f"labels.txt는 plastic/can/paper 각 1개여야 함: {labels}")
        LOG.info("AI 준비 완료, 입력=%s, 모델 라벨 순서=%s", shape, self.kinds)

    def predict(self, rgb_image: Any) -> dict[str, float]:
        from PIL import Image
        rgb = rgb_image.convert("RGB")
        w, h = rgb.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        image = rgb.crop((left, top, left + side, top + side))
        image = image.resize(self.size, getattr(Image, "Resampling", Image).LANCZOS)
        input_tensor = (self.np.asarray(image, dtype=self.np.float32) / 127.5 - 1.0)[None]
        self.interpreter.set_tensor(self.input["index"], input_tensor)
        self.interpreter.invoke()
        p = interpret_probs(self.interpreter.get_tensor(self.output["index"])[0])
        return {name: float(prob) for name, prob in zip(self.kinds, p)}


# ===================== 실시간 카메라 + 화면 =====================
class CameraFeed:
    """카메라를 오직 하나의 캡처 스레드가 읽어 프리뷰/AI가 프레임을 공유."""
    def __init__(self, camera: Any):
        self.camera = camera
        self.cv = threading.Condition()
        self.seq = 0
        self.frame: Any = None
        self.jpeg: bytes | None = None
        self.error: Exception | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self):
        self.thread = threading.Thread(target=self._worker, name="camera-feed", daemon=True)
        self.thread.start()
        self.next_frame(0, CAMERA_START_TIMEOUT)

    def _worker(self):
        from PIL import Image
        interval = 1.0 / CAMERA_FPS
        try:
            while not self.stop_event.is_set():
                began = time.monotonic()
                # Picamera2 BGR888는 capture_array 결과가 RGB 바이트 순서임.
                arr = self.camera.capture_array("main")
                rgb = Image.fromarray(arr, mode="RGB")
                stream = io.BytesIO()
                rgb.save(stream, format="JPEG", quality=70)
                with self.cv:
                    self.frame = rgb
                    self.jpeg = stream.getvalue()
                    self.seq += 1
                    self.cv.notify_all()
                self.stop_event.wait(max(0.0, interval - (time.monotonic() - began)))
        except Exception as exc:
            LOG.exception("카메라 캡처 스레드 고장")
            with self.cv:
                self.error = exc
                self.cv.notify_all()

    def current_sequence(self) -> int:
        with self.cv:
            return self.seq

    def next_frame(self, last_seq: int, timeout: float = 4.0) -> tuple[int, Any]:
        deadline = time.monotonic() + timeout
        with self.cv:
            while self.seq <= last_seq:
                if self.error is not None:
                    raise RuntimeError("카메라 촬영 실패") from self.error
                remain = deadline - time.monotonic()
                if remain <= 0:
                    raise TimeoutError("새 카메라 영상 프레임 수신 시간 초과")
                self.cv.wait(min(0.3, remain))
            return self.seq, self.frame.copy()

    def next_jpeg(self, last_seq: int, timeout: float = 3.0) -> tuple[int, bytes | None]:
        deadline = time.monotonic() + timeout
        with self.cv:
            while self.seq <= last_seq and not self.stop_event.is_set() and self.error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.cv.wait(min(remaining, 0.5))
            return self.seq, self.jpeg

    def close(self):
        self.stop_event.set()
        with self.cv:
            self.cv.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=5)


def classify(feed: CameraFeed, model: Classifier) -> tuple[str, str | None, dict[str, float]]:
    LOG.info("[카메라] 새로운 화면 프레임 %d개를 순서대로 분석합니다", CLASSIFY_FRAMES)
    acc = {x: 0.0 for x in KOREAN}
    seq = feed.current_sequence()  # 분류 시작 이전에 촬영된 오래된 프레임을 재사용하지 않음
    for i in range(CLASSIFY_FRAMES):
        seq, frame = feed.next_frame(seq)
        scores = model.predict(frame)
        for kind, score in scores.items():
            acc[kind] += score
        LOG.info("사진 %d/%d: %s", i + 1, CLASSIFY_FRAMES, fmt_scores(scores))
    scores = {k: v / CLASSIFY_FRAMES for k, v in acc.items()}
    kind, reason = decide_camera(scores)
    LOG.info("[AI 종합] %s", fmt_scores(scores))
    LOG.info("[AI 결과] 카메라 예상 재질=%s, 평균 최고 점수=%.1f%%, 상태=%s",
             KOREAN[kind], scores[kind] * 100, reason or "기준 통과")
    return kind, reason, scores


class SharedStatus:
    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {
            "state": "starting", "message": "장비 초기화 중", "result": "",
            "scores": {}, "fills": {k: {"distance_cm": None, "percent": 0.0,
                                                "status": "await-start"} for k in KOREAN},
            "led": None, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def patch(self, **changes: Any):
        with self.lock:
            self.data.update(changes)
            self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def set_fill(self, kind: str, cm: float | None, status: str):
        with self.lock:
            fills = dict(self.data["fills"])
            fills[kind] = {"distance_cm": round(cm, 1) if cm is not None else None,
                           "percent": percent_full(kind, cm) if cm is not None else None,
                           "status": status}
            self.data["fills"] = fills
            self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.data, ensure_ascii=False))


PAGE = """<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>AI 스마트 분리수거기</title><style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#eaf2ff;background:#0b1426}
body{margin:0 auto;padding:22px;max-width:1000px}header{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
h1{font-size:26px}.pill{background:#1a2d46;border-radius:22px;padding:9px 15px}
.grid{display:grid;grid-template-columns:1.5fr 1fr;gap:18px}.panel{background:#13233b;border:1px solid #2b4567;border-radius:16px;padding:16px}
img{width:100%;background:#080c13;aspect-ratio:4/3;object-fit:contain;border-radius:10px}
.status{font-size:20px;font-weight:700}.small{color:#a9c0db}.bar{height:10px;background:#2b3c55;border-radius:7px;overflow:hidden}
.fill{height:100%;background:#48b5ef;transition:width .4s}.bin{margin:18px 0}
@media(max-width:740px){.grid{grid-template-columns:1fr}h1{font-size:20px}}
</style></head><body><header><h1>♻ AI 스마트 분리수거기</h1><span id='updated' class='pill'>연결 중...</span></header>
<div class='grid'><div class='panel'><h3>실시간 카메라</h3><img src='/stream.mjpg' alt='실시간 분류 카메라'>
<p class='small'>영상이 보이지 않으면 카메라 및 네트워크 상태를 확인하세요.</p></div>
<div class='panel'><h3>기기 상태</h3><div id='state' class='status'>준비 중</div><p id='msg'>센서 연결 중</p>
<div id='result' class='small'></div><h3>통별 적재량</h3><div id='bins'></div>
<h3>AI 판정 점수</h3><div id='scores' class='small'>아직 분류하지 않음</div></div></div>
<script>
const names={plastic:'플라스틱',can:'캔',paper:'종이'};
async function update(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('API 오류');const s=await r.json();
document.getElementById('updated').textContent=s.updated_at;
document.getElementById('state').textContent=s.state;document.getElementById('msg').textContent=s.message;
document.getElementById('result').textContent=s.result||'';
document.getElementById('bins').innerHTML=Object.entries(s.fills).map(([k,v])=>{
const p=v.percent??0;const color=v.status==='critical'?'#f47878':v.status==='warn'?'#f2bc59':'#48b5ef';
const stateName={"await-start":"시작 측정 전","zero-calibrated":"빈 통 0% 실측 기준 저장","ok":"정상","warn":"80% 이상","critical":"95% 이상(경고)","sensor-error":"측정 실패"}[v.status]||v.status;
return `<div class='bin'><b>${names[k]}</b> ${v.percent==null?'측정 실패':p.toFixed(0)+'%'} <small class='small'>${stateName}</small><div class='bar'><div class='fill' style='width:${p}%;background:${color}'></div></div></div>`}).join('');
document.getElementById('scores').textContent=Object.entries(s.scores||{}).map(([k,v])=>`${names[k]} ${(v*100).toFixed(1)}%`).join(' / ')||'아직 분류하지 않음';
}catch(e){document.getElementById('updated').textContent='연결 끊김';}}
update();setInterval(update,1000);
</script></body></html>"""


class Dashboard:
    def __init__(self, feed: CameraFeed, status: SharedStatus):
        self.feed, self.status = feed, status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                LOG.debug("HTTP: " + fmt, *args)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/":
                    body = PAGE.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/status":
                    body = json.dumps(outer.status.snapshot(), ensure_ascii=False).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/stream.mjpg":
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    seq = -1
                    try:
                        while not outer.feed.stop_event.is_set():
                            new_seq, jpeg = outer.feed.next_jpeg(seq, timeout=2.0)
                            if new_seq == seq or jpeg is None:
                                continue
                            seq = new_seq
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                            self.wfile.write(jpeg + b"\r\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                        pass
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)

        self.server = ThreadingHTTPServer((WEB_BIND, WEB_PORT), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="web", daemon=True)

    def start(self):
        self.thread.start()
        LOG.info("카메라/상태 웹 화면: http://<라즈베리파이-IP>:%d", WEB_PORT)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


# ===================== 센서 =====================
def make_input(gpio: int | None, active_low: bool, stack: ExitStack):
    if gpio is None:
        return None
    from gpiozero import DigitalInputDevice
    # pull_up=True일 때 LOW가 active이며, GPIO Zero .value=True가 '감지' 의미.
    return stack.enter_context(DigitalInputDevice(gpio, pull_up=active_low))


def stable_read(sensor: Any, desired: bool, stable_seconds: float,
                timeout: float | None = None, healthcheck=None) -> bool:
    """원하는 값이 연속 stable_seconds 이상이어야 True. 종료는 Ctrl+C 허용."""
    started = time.monotonic()
    stable_since = None
    while True:
        if healthcheck is not None:
            healthcheck()
        now = time.monotonic()
        if bool(sensor.value) == desired:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= stable_seconds:
                return True
        else:
            stable_since = None
        if timeout is not None and now - started >= timeout:
            return False
        time.sleep(IR_POLL_SECONDS)


def entry_detected(distance_cm: float | None, background_cm: float | None,
                   previously_detected: bool = False) -> bool:
    """투입 초음파 감지: 배경이 보이면 빈 판보다 가까워진 물체만 감지.

    배경에 반사물이 전혀 없으면 절대 거리 임계값으로 판정.
    실측 가능한 거리 범위를 늘리는 것은 소프트웨어 임계값일 뿐 하드웨어 성능을 늘리지는 않음.
    """
    if distance_cm is None or not math.isfinite(distance_cm):
        return False
    if not (ULTRA_MIN_CM <= distance_cm <= ULTRA_MAX_CM):
        return False
    limit = ENTRY_RELEASE_CM if previously_detected else ENTRY_DETECT_MAX_CM
    if distance_cm > limit:
        return False
    if background_cm is None:
        return True
    required_delta = ENTRY_CLEAR_DELTA_CM if previously_detected else ENTRY_BACKGROUND_DELTA_CM
    return (background_cm - distance_cm) >= required_delta


class EntryUltrasonic:
    """전면 감지용 별도 초음파. 수거함 적재량 센서 3개와 완전히 독립된 센서."""
    def __init__(self, trig: Any, echo: Any):
        self.trig, self.echo = trig, echo
        self.background_cm: float | None = None
        self.last_cm: float | None = None
        self._detected = False
        self._last_ping = 0.0
        self._last_log = 0.0
        self._missed = 0

    def calibrate_empty(self):
        """서보 홈 복귀 뒤, 물체가 없는 상태에서 배경 거리만 1회 보정."""
        readings = []
        for _ in range(ENTRY_BASELINE_SAMPLES):
            try:
                cm = measure_once(self.trig, self.echo)
            except Exception as exc:
                LOG.warning("[투입 초음파] 초기 단일 거리 측정 실패: %s", exc)
                cm = None
            if cm is not None:
                readings.append(cm)
            time.sleep(ENTRY_SAMPLE_GAP_S)
        self.background_cm = median(readings) if len(readings) >= 3 else None
        self._detected = False
        self.last_cm = self.background_cm
        self._last_ping = 0.0
        self._missed = 0
        if self.background_cm is None:
            LOG.warning("[투입 초음파] 빈 판 배경 반사가 잡히지 않음 → %.0fcm 이내 물체로 감지. "
                        "센서 배선 및 방향 점검 필요", ENTRY_DETECT_MAX_CM)
        elif self.background_cm <= ULTRA_MIN_CM + ENTRY_BACKGROUND_DELTA_CM:
            raise RuntimeError("투입 초음파와 빈 측정판이 너무 가까워 물체 판별이 어렵습니다. 센서 위치를 조절하세요")
        else:
            LOG.info("[투입 초음파 보정] 빈 측정판 %.1fcm → %.1fcm 이내이며 배경보다 %.1fcm 가까우면 감지",
                     self.background_cm, ENTRY_DETECT_MAX_CM, ENTRY_BACKGROUND_DELTA_CM)

    @property
    def value(self) -> bool:
        """기존 IR 인터페이스처럼 True=물체 있음. 샘플은 최소 70ms 간격."""
        now = time.monotonic()
        if now - self._last_ping < ENTRY_SAMPLE_GAP_S:
            return self._detected
        try:
            cm = measure_once(self.trig, self.echo)
        except Exception as exc:
            raise RuntimeError(f"투입 초음파 센서 읽기 오류: {exc}") from exc
        self._last_ping = time.monotonic()
        if cm is not None:
            self._missed = 0
            self.last_cm = cm
            detected = entry_detected(cm, self.background_cm, self._detected)
            if detected != self._detected:
                LOG.info("[투입 초음파] %s: %.1fcm / 빈 판 기준 %s / 최대 %.0fcm",
                         "★ 물체 감지" if detected else "감지 해제", cm,
                         f"{self.background_cm:.1f}cm" if self.background_cm is not None else "반사 없음",
                         ENTRY_DETECT_MAX_CM)
            self._detected = detected
        elif self.background_cm is None:
            # 배경이 감지되지 않는 개방형 투입구: 미반사=물체 없음.
            self.last_cm = None
            self._detected = False
        else:
            # 정상적으로 배경을 읽던 센서가 연속해서 에코를 잃으면 무한 대기보다 안전 정지.
            self._missed += 1
            if self._missed >= 12:
                raise RuntimeError("투입 초음파 연속 측정 실패. TRIG/ECHO·전압·센서 방향 확인")
        if now - self._last_log >= ENTRY_LOG_INTERVAL_S:
            LOG.info("[투입 감지 대기] 거리=%s / 감지 범위=%.0fcm / 상태=%s",
                     f"{self.last_cm:.1f}cm" if self.last_cm is not None else "에코 없음",
                     ENTRY_DETECT_MAX_CM, "감지" if self._detected else "비감지")
            self._last_log = now
        return self._detected


class CombinedEntrySensor:
    """IR 장애물 센서 또는 4번째 초음파 중 한쪽만 참이어도 투입 감지(True).

    투입용 초음파에 이상이 있어도 IR 신호는 계속 감지할 수 있다.
    단, 장시간 투입 초음파 신호 이상은 터미널에 경고를 출력한다.
    """
    def __init__(self, ir: Any, ultra: EntryUltrasonic | None):
        self.ir, self.ultra = ir, ultra
        self.last_trigger = "없음"
        self.last_cm: float | None = None
        self._previous_state = False
        self._warned_ultra = False

    @property
    def value(self) -> bool:
        # 한 센서가 켜져도 다른 센서 상태를 확인해 양쪽 로그를 남김.
        ir_hit = bool(self.ir.value)
        ultra_hit = False
        if self.ultra is not None:
            try:
                ultra_hit = bool(self.ultra.value)
                self.last_cm = self.ultra.last_cm
                if self._warned_ultra:
                    LOG.info("[투입 센서 복구] 초음파 거리 읽기 정상화")
                    self._warned_ultra = False
            except Exception as exc:
                # 두 센서 OR 구조: 초음파 단독 고장 시 IR 센서로 계속 투입 감지 가능.
                if not self._warned_ultra:
                    LOG.warning("[투입 초음파 경고] %s → 장애물 IR(GPIO%d)로 감지 계속", exc, IR_GPIO)
                    self._warned_ultra = True
                self.last_cm = None
        if ir_hit and ultra_hit:
            source = "IR 장애물 + 초음파 (둘 다 감지)"
        elif ir_hit:
            source = "IR 장애물(GPIO17)"
        elif ultra_hit:
            source = f"투입 초음파(GPIO23/24, {self.last_cm:.1f}cm)" if self.last_cm is not None else "투입 초음파"
        else:
            source = "없음"
        detected = ir_hit or ultra_hit
        if detected and (not self._previous_state or source != self.last_trigger):
            LOG.info("[투입 OR 감지] ★ 감지 성공: %s → 분류 시작 조건 충족", source)
        elif self._previous_state and not detected:
            LOG.info("[투입 OR 감지] IR와 초음파 모두 해제")
        self.last_trigger = source
        self._previous_state = detected
        return detected


def make_entry_sensor(stack: ExitStack):
    """기본: IR(GPIO17) OR 투입 초음파(GPIO23/24). 단독 및 수동 모드도 지원."""
    if ENTRY_MODE == "manual":
        LOG.info("[투입 센서] 수동 Enter 투입 모드")
        return None
    ir = None
    if ENTRY_MODE in ("ir", "both"):
        ir = make_input(IR_GPIO, IR_ACTIVE_LOW, stack)
        LOG.info("[투입 센서] IR 장애물(GPIO%d) 사용 / 감지시 LOW=%s", IR_GPIO, IR_ACTIVE_LOW)
        if ENTRY_MODE == "ir":
            return ir
    from gpiozero import DigitalInputDevice, DigitalOutputDevice
    trig = stack.enter_context(DigitalOutputDevice(ENTRY_TRIG_GPIO, initial_value=False))
    echo = stack.enter_context(DigitalInputDevice(ENTRY_ECHO_GPIO, pull_up=False))
    ultra = EntryUltrasonic(trig, echo)
    # 이미 놓인 물체가 IR에 감지되면 이를 빈 측정판 거리로 잘못 학습하지 않는다.
    if ir is not None and bool(ir.value):
        LOG.warning("[투입 초음파 보정] 시작부터 IR 감지 상태 → 빈 판 거리 보정 생략. "
                    "이번 실행은 초음파의 최대 %.0fcm 절대 거리 또는 IR 감지를 사용", ENTRY_DETECT_MAX_CM)
    elif ENTRY_MODE == "both":
        try:
            ultra.calibrate_empty()
        except Exception as exc:
            LOG.warning("[투입 초음파 경고] 배경 거리 보정 실패(%s). IR 감지는 계속 사용", exc)
            ultra = None
    else:
        ultra.calibrate_empty()
    LOG.info("[투입 센서] 투입 초음파 TRIG=BCM%d, ECHO=BCM%d / 최대 %.0fcm / 모드=%s",
             ENTRY_TRIG_GPIO, ENTRY_ECHO_GPIO, ENTRY_DETECT_MAX_CM, ENTRY_MODE)
    if ENTRY_MODE == "both":
        LOG.info("[투입 센서 논리] 장애물 IR(GPIO%d) OR 초음파(GPIO%d/%d): 둘 중 하나만 감지해도 진행",
                 IR_GPIO, ENTRY_TRIG_GPIO, ENTRY_ECHO_GPIO)
        return CombinedEntrySensor(ir, ultra)
    return ultra


def wait_for_object(sensor: Any, healthcheck=None) -> bool:
    if sensor is None:
        LOG.info("[물체 감지] 수동 Enter 투입 모드")
        return input("\n물체를 측정판에 올리고 Enter (종료: q) > ").strip().lower() != "q"
    method = ("IR 장애물 또는 투입 초음파 (OR)" if ENTRY_MODE == "both" else
              "투입 초음파" if ENTRY_MODE == "ultrasonic" else f"IR(GPIO{IR_GPIO})")
    LOG.info("[물체 감지] %s 감지 대기 중…", method)
    stable_seconds = ENTRY_STABLE_SECONDS if ENTRY_MODE in ("both", "ultrasonic") else IR_STABLE_SECONDS
    stable_read(sensor, True, stable_seconds, healthcheck=healthcheck)
    if ENTRY_MODE == "both":
        LOG.info("[물체 감지] ★ 1회 투입 확정! 감지한 센서: %s (%.2f초 안정 감지)",
                 sensor.last_trigger, stable_seconds)
    elif ENTRY_MODE == "ultrasonic":
        LOG.info("[물체 감지] ★ 투입 초음파 감지 성공! 거리=%s / 최대 감지 %.0fcm",
                 f"{sensor.last_cm:.1f}cm" if sensor.last_cm is not None else "읽기 실패", ENTRY_DETECT_MAX_CM)
    else:
        LOG.info("[물체 감지] ★ 장애물 IR 감지 성공! %.2f초 연속 감지 확인 (IR=%d)",
                 IR_STABLE_SECONDS, int(bool(sensor.value)))
    return True


def wait_for_clear(ir: Any, healthcheck=None) -> None:
    if ir is None:
        LOG.info("[물체 제거 확인] 수동 입력 모드라 IR 해제 검사를 생략합니다")
        return
    if bool(ir.value):
        LOG.info("[물체 제거 확인] 투입 센서가 아직 감지 중 → 새 분류 차단")
    stable_read(ir, False, ENTRY_STABLE_SECONDS if ENTRY_MODE in ("both", "ultrasonic") else IR_STABLE_SECONDS,
                healthcheck=healthcheck)
    LOG.info("[물체 제거 확인] 투입 감지 해제됨 → 다음 물체 투입 가능")


def majority_sensor(sensor: Any) -> bool | None:
    if sensor is None:
        return None
    reads = []
    for _ in range(METAL_VOTES):
        reads.append(bool(sensor.value))
        time.sleep(METAL_SAMPLE_SECONDS)
    hits = sum(reads)
    detected = hits >= METAL_MIN_HITS
    LOG.info("[금속 측정] %d회 중 %d회 감지 (확정 기준 %d회 이상) → %s",
             len(reads), hits, METAL_MIN_HITS,
             "★ 금속 감지! AI 판정과 무관하게 캔 확정" if detected else "금속 감지 안 됨")
    return detected


def moisture_sensor(adc: Any) -> tuple[int | None, bool | None]:
    if adc is None:
        return None, None
    samples = []
    for _ in range(MOISTURE_SAMPLES):
        voltage_ratio = float(adc.value)
        if not 0 <= voltage_ratio <= 1 or not math.isfinite(voltage_ratio):
            raise RuntimeError("수분센서 ADC에서 유효하지 않은 값 수신")
        samples.append(int(round(voltage_ratio * 4095)))
        time.sleep(0.02)
    raw = int(median(samples))
    wet = wet_from_raw(raw)
    LOG.info("[수분 측정] ADC 중앙값=%d / 기준=%d / 판정=%s", raw,
             MOISTURE_WET_RAW, "젖음(보류 대상)" if wet else "마름")
    return raw, wet


def measure_once(trig: Any, echo: Any) -> float | None:
    """초음파 TRIG 10us 펄스, ECHO 왕복 시간을 cm로 환산."""
    if echo.pin.state:
        return None  # 시작부터 HIGH면 배선/센서 고장 가능성
    trig.on()
    pulse_until = time.perf_counter_ns() + 10_000
    while time.perf_counter_ns() < pulse_until:
        pass
    trig.off()
    deadline = time.monotonic() + ULTRA_ECHO_TIMEOUT
    while not echo.pin.state:
        if time.monotonic() >= deadline:
            return None
    beginning = time.perf_counter_ns()
    deadline = time.monotonic() + ULTRA_ECHO_TIMEOUT
    while echo.pin.state:
        if time.monotonic() >= deadline:
            return None
    cm = (time.perf_counter_ns() - beginning) / 1_000_000_000 * 17150
    return cm if ULTRA_MIN_CM <= cm <= ULTRA_MAX_CM else None


def read_distance(trig: Any, echo: Any) -> float | None:
    values = []
    for _ in range(ULTRA_SAMPLES):
        try:
            cm = measure_once(trig, echo)
            if cm is not None:
                values.append(cm)
        except Exception as exc:
            LOG.warning("초음파 단일 측정 실패: %s", exc)
        time.sleep(ULTRA_SAMPLE_PAUSE)
    return median(values) if len(values) >= ULTRA_MIN_GOOD else None


class FillMonitor:
    """초음파 측정은 시작 1회와 종료 1회만 수행하고, 중간에는 마지막 상태를 보존한다."""
    def __init__(self, sensors: dict[str, tuple[Any, Any]], status: SharedStatus, alert: Any,
                 indicator: StatusIndicator | None = None):
        self.sensors, self.status, self.alert = sensors, status, alert
        self.indicator = indicator
        self.started = False
        self.ran = False   # 종료 측정 1회 제한
        self.startup_result: dict[str, float | None] = {}
        # 적재량 RGB2에만 사용: 실패한 센서는 마지막으로 측정에 성공한 값 유지.
        # 센서 에러 자체는 적재량 80% 이상이라는 근거가 아니다.
        self.last_valid_percent: dict[str, float] = {}

    def _capture(self, stage: str) -> dict[str, float | None]:
        """통별 측정 1회. 특정 센서 실패도 구별할 수 있도록 None 유지."""
        readings = {}
        for kind in KOREAN:
            trig, echo = self.sensors[kind]
            try:
                cm = read_distance(trig, echo)
            except Exception:
                LOG.exception("[%s] %s 통 초음파 오류", stage, KOREAN[kind])
                cm = None
            readings[kind] = cm
            if cm is None:
                LOG.error("[%s] %s 통: 거리 측정 실패", stage, KOREAN[kind])
            else:
                LOG.info("[%s] %s 통: 실측 거리 %.2f cm", stage, KOREAN[kind], cm)
        return readings

    def _publish(self, readings: dict[str, float | None], stage: str, zero: bool = False):
        results = {}
        for kind in KOREAN:
            cm = readings[kind]
            if cm is None:
                self.status.set_fill(kind, None, "sensor-error")
                results[kind] = None
                LOG.error("[%s] %s: 측정 불가 (0%%로 임의 처리하지 않음)", stage, KOREAN[kind])
                continue
            pct = percent_full(kind, cm)
            category = ("zero-calibrated" if zero else
                        "critical" if pct >= FILL_BLOCK_PERCENT else
                        "warn" if pct >= FILL_WARNING_PERCENT else "ok")
            self.status.set_fill(kind, cm, category)
            results[kind] = pct
            self.last_valid_percent[kind] = pct
            LOG.info("[%s 적재량] %s: 현재 %.2fcm / 빈 통 기준 %.2fcm → %.1f%% %s",
                     stage, KOREAN[kind], cm, EMPTY_CM[kind], pct,
                     "(★ 이번 측정이 0%% 기준)" if zero else
                     "(95%% 이상)" if category == "critical" else
                     "(80%% 이상)" if category == "warn" else "")
        # 현재 실측값 또는 해당 센서의 마지막 유효 측정값 중 80% 이상이 하나라도 있으면 빨강.
        # 단순 측정 실패(None)는 빨강 조건에 포함하지 않는다.
        over_80 = any(p >= FILL_WARNING_PERCENT for p in self.last_valid_percent.values())
        measure_error = any(p is None for p in results.values())
        if self.alert is not None:
            # 별도 경고 출력 장치는 기존 안전 정책(측정 오류도 경고)을 유지한다.
            self.alert.value = over_80 or measure_error
        if self.indicator is not None:
            self.indicator.set_fill("red" if over_80 else "green")
        if measure_error:
            LOG.warning("[적재량 RGB] %s: 일부 통 측정 실패. 실패한 통은 이전 유효값을 유지하며, "
                        "한 번도 유효하게 측정하지 못했다면 실제 80%% 초과 여부는 알 수 없습니다", stage)
        LOG.info("[적재량 RGB] %s: %s", stage,
                 "빨강 (마지막 유효 측정값 기준 80% 이상 통 있음)" if over_80 else
                 "초록 (유효 측정값 기준 80% 이상 통 없음: RGB2 B 미연결)")
        return results

    def measure_at_startup(self) -> dict[str, float | None]:
        if self.started:
            LOG.info("[적재량 시작] 이미 측정했으므로 반복하지 않음")
            return dict(self.startup_result)
        LOG.info("[적재량 시작] 초음파 3개 통을 각각 딱 한 차례 측정합니다")
        # 저장된 0% 거리 기준이 있으면 재사용, 없으면 지금 빈 통을 0% 기준으로 저장.
        already_calibrated = load_empty_calibration()
        readings = self._capture("시작 초음파")
        if any(cm is None for cm in readings.values()):
            for kind, cm in readings.items():
                if cm is None:
                    self.status.set_fill(kind, None, "sensor-error")
            raise RuntimeError("시작 초음파 측정 실패: 3통 모두 정상 측정되어야 분류기를 실행합니다. "
                               "ECHO 전압·배선·센서 방향을 점검하세요")
        if not already_calibrated:
            LOG.warning("[0% 자동 보정] 사용자가 지금 세 통이 비어 있다고 확인함. "
                        "실측한 현재 거리 3개를 0% 기준으로 저장합니다")
            save_empty_calibration(readings)
        results = self._publish(readings, "시작", zero=not already_calibrated)
        self.startup_result = results
        self.started = True
        LOG.info("[시작 검사 완료] 이후에는 Ctrl+C 종료할 때까지 초음파를 측정하지 않습니다")
        return dict(results)

    def measure_at_shutdown(self) -> dict[str, float | None]:
        if self.ran:
            LOG.info("[적재량 종료] 이미 검사 완료. 다시 측정하지 않음")
            return {kind: self.status.snapshot()["fills"][kind]["percent"] for kind in KOREAN}
        self.ran = True
        LOG.info("[적재량 종료] 초음파 3개 통을 각각 딱 한 차례 측정합니다")
        readings = self._capture("종료 초음파")
        results = self._publish(readings, "종료")
        LOG.info("[적재량 최종] 플라스틱=%s / 캔=%s / 종이=%s",
                 "측정 실패" if results["plastic"] is None else f"{results['plastic']:.1f}%",
                 "측정 실패" if results["can"] is None else f"{results['can']:.1f}%",
                 "측정 실패" if results["paper"] is None else f"{results['paper']:.1f}%")
        LOG.info("[적재량 종료 검사 완료] 운전 중에는 초음파를 측정하지 않았습니다")
        return results


# ===================== LED / 서보 =====================
class StatusIndicator:
    """일반 4핀 RGB LED 두 개를 서로 독립적으로 색상 제어한다.

    1번: 분류 대기/진행 blue, 배출 성공 green, 배출 보류 red.
    2번: 적재량 시작/종료 검사 결과 정상 green (B 미연결), 80% 이상 또는 센서오류 red.
    LED 핀은 사용자 설정 STATUS_RGB_PINS/FILL_RGB_PINS를 따라간다.
    """
    _COLORS = {"red", "green", "blue"}

    def __init__(self, stack: ExitStack, shared: SharedStatus):
        from gpiozero import DigitalOutputDevice
        self.shared = shared
        self.lock = threading.Lock()
        self.color = "blue"
        self.fill_color = "green"
        self.stop_event = threading.Event()
        self.rgb: dict[str, Any] = {}
        self.fill_rgb: dict[str, Any] = {}
        for color, pin in STATUS_RGB_PINS.items():
            self.rgb[color] = stack.enter_context(DigitalOutputDevice(
                pin, active_high=STATUS_RGB_ACTIVE_HIGH, initial_value=False))
        for color, pin in FILL_RGB_PINS.items():
            self.fill_rgb[color] = stack.enter_context(DigitalOutputDevice(
                pin, active_high=FILL_RGB_ACTIVE_HIGH, initial_value=False))
        self.shared.patch(led=self.color, fill_led=self.fill_color)
        self.thread = threading.Thread(target=self._loop, name="two-rgb-leds", daemon=True)
        self.thread.start()
        LOG.info("[RGB 1 분류] 파랑(대기) / [RGB 2 적재량] 초록(시작 측정 전, B 미연결)")

    def set(self, color: str | None):
        # 대기 단계의 None은 LED를 끄는 것이 아니라 파랑으로 유지.
        color = color or "blue"
        if color not in self._COLORS:
            raise ValueError(f"분류 RGB 색상 오류: {color}")
        with self.lock:
            changed = self.color != color
            self.color = color
        self.shared.patch(led=color)
        if changed:
            LOG.info("[RGB 1 분류 상태] %s", {"red": "빨강(반려/오류)",
                   "green": "초록(분류 완료)", "blue": "파랑(대기/진행)"}[color])

    def set_fill(self, color: str):
        if color not in ("red", "green"):
            raise ValueError(f"적재량 RGB는 red/green만 사용(B 미연결): {color}")
        with self.lock:
            self.fill_color = color
        self.shared.patch(fill_led=color)
        LOG.info("[RGB 2 적재량] %s", "빨강(유효 측정값 기준 80% 이상)" if color == "red" else
                 "초록(현재 확인된 적재량 80% 이상 없음, B 미연결)")

    def _loop(self):
        while not self.stop_event.is_set():
            with self.lock:
                status_color, fill_color = self.color, self.fill_color
            # 각 LED에서 선택한 한 가지 색만 켬 (다른 두 색은 끔).
            for color, device in self.rgb.items():
                device.value = color == status_color
            for color, device in self.fill_rgb.items():
                device.value = color == fill_color
            self.stop_event.wait(0.05)
        for device in (*self.rgb.values(), *self.fill_rgb.values()):
            device.off()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)


class Servos:
    def __init__(self, stack: ExitStack):
        from gpiozero import AngularServo

        def create(pin: int):
            # 시작 물리 위치는 알 수 없으므로 initial_angle=None으로 시작한다.
            return stack.enter_context(AngularServo(
                pin, initial_angle=None, min_angle=-90, max_angle=90,
                min_pulse_width=SERVO_MIN_PULSE, max_pulse_width=SERVO_MAX_PULSE))

        self.tray = create(TRAY_GPIO)
        self.diverter = create(DIVERTER_GPIO)
        self.tray_target: int | None = None
        self.diverter_target: int | None = None
        LOG.info("[시작 원위치] 두 판 모두 홈 위치로 이동 시작: 측정판 BCM%d → %d°, 분류판 BCM%d → %d°",
                 TRAY_GPIO, TRAY_HOME, DIVERTER_GPIO, DIVERTER_HOME)
        self.startup_home()  # 일반 실행에서도 반드시 AI/카메라보다 먼저 실행

    def _move(self, key: str, angle: int, label: str, force: bool = False):
        if key == "diverter" and angle not in (DIVERTER_HOME, DIVERTER_RIGHT):
            raise ValueError(f"분류판에는 원위치 {DIVERTER_HOME}° 또는 오른쪽 {DIVERTER_RIGHT}°만 허용: {angle}°")
        servo = getattr(self, key)
        current = getattr(self, key + "_target")
        if current == angle and not force:
            return
        LOG.info("[%s] %s° → %s° %s", label, "미확인" if current is None else current,
                 angle, "(강제 명령)" if force else "")
        servo.angle = angle
        setattr(self, key + "_target", angle)
        time.sleep(SERVO_MOVE_SECONDS)

    def home(self, force: bool = False):
        """측정판 먼저 원위치, 다음 분류판. force면 현재 소프트웨어 위치가 같아도 다시 신호 전송."""
        self._move("tray", TRAY_HOME, "측정판 복귀", force=force)
        self._move("diverter", DIVERTER_HOME, "분류판 복귀", force=force)
        time.sleep(HOME_SETTLE_SECONDS)

    def startup_home(self):
        """일반 실행 시작 시 양쪽 서보에 무조건 홈 PWM 목표값을 전달한다.

        전원 기동 순간 명령을 놓치는 경우에 대비해 2회 전송한다.
        위치 피드백 없는 일반 서보이므로 실제 도달 여부는 알 수 없다.
        """
        for attempt in range(1, STARTUP_HOME_RETRIES + 1):
            LOG.info("[시작 원위치 %d/%d] 측정판 %d° / 분류판 %d° 강제 이동 명령",
                     attempt, STARTUP_HOME_RETRIES, TRAY_HOME, DIVERTER_HOME)
            self.home(force=True)
            if attempt < STARTUP_HOME_RETRIES:
                time.sleep(STARTUP_HOME_PAUSE)
        LOG.info("[시작 원위치 완료] 두 서보 원위치 명령 전송 완료 → 이 다음에 AI·카메라 초기화")
        LOG.warning("[중요] 실제 원위치 도달은 감지할 수 없습니다. 움직이지 않으면 GPIO·전원·각도·기계 간섭을 확인하세요")

    def drop(self, kind: str):
        diverter_target, tray_target, diverter_note, tray_direction = route_plan(kind)
        LOG.info("[배출 경로] %s → 분류판 %s(%d°) → 측정판 %s %d° → %s 수거함",
                 KOREAN[kind], diverter_note, diverter_target, tray_direction,
                 tray_target, KOREAN[kind])
        self.home()
        try:
            LOG.info("[분류판 선택] %s: %s (%d°)", KOREAN[kind], diverter_note, diverter_target)
            self._move("diverter", diverter_target, "분류판 오른쪽" if kind == "can" else "분류판 원위치")
            time.sleep(DIVERTER_SETTLE_SECONDS)
            self._move("tray", tray_target, "측정판 배출")
            LOG.info("[배출 동작] %s 배출 방향으로 기울임 완료, %.1f초 낙하 대기 (실제 낙하 미확인)",
                     KOREAN[kind], DROP_SECONDS)
            time.sleep(DROP_SECONDS)
        finally:
            LOG.info("[서보 복귀] 측정판 %d° → 분류판 %d° 순서로 원위치 복귀", TRAY_HOME, DIVERTER_HOME)
            self.home(force=True)
            LOG.info("[서보 복귀] 각도 명령 재전송 완료 (물리 위치는 미확인)")


# ===================== 통합 제어 =====================
class Sorter:
    def __init__(self, feed: CameraFeed, classifier: Classifier, servos: Servos,
                 ir: Any, metal: Any, adc: Any, fills: FillMonitor,
                 led: StatusIndicator, status: SharedStatus):
        self.feed, self.classifier, self.servos = feed, classifier, servos
        self.ir, self.metal, self.adc, self.fills, self.led, self.status = ir, metal, adc, fills, led, status
        self.counts = {k: 0 for k in KOREAN}
        self.cycle_index = 0

    def trace(self, number: int, name: str, message: str):
        """해당 물체의 진행 단계를 일관된 번호로 터미널에 출력."""
        LOG.info("[회차 %03d | %02d/09 %s] %s", max(1, self.cycle_index), number, name, message)

    def check_health(self, require_camera: bool = False):
        # 금속 검출은 카메라의 인식 실패/캡처 오류와 무관하게 수행할 수 있어야 한다.
        if require_camera and self.feed is not None and self.feed.error is not None:
            raise RuntimeError("카메라 캡처 스레드 정지") from self.feed.error
        if not self.led.thread.is_alive():
            raise RuntimeError("LED 제어 스레드 정지")

    def _state(self, state: str, message: str, color: str | None = None):
        LOG.info("[%s] %s", state, message)
        self.status.patch(state=state, message=message)
        self.led.set(color)

    def process_one(self):
        # 1. 물체 안정화. 카메라가 오류 상태라도 금속 감지를 먼저 확인한다.
        self.check_health()
        self.trace(2, "측정 준비", f"측정판 {TRAY_HOME}°·분류판 {DIVERTER_HOME}° 대기 위치 확인/복귀")
        self.servos.home()
        self._state("측정 중", "물체 안정화 및 금속 우선 검사", "blue")
        self.trace(2, "안정화", f"물체가 흔들리지 않도록 {SETTLE_SECONDS:.1f}초 대기")
        time.sleep(SETTLE_SECONDS)

        # 2. 금속 최우선: 단 한 번의 감지만 있어도 카메라 분류를 호출하지 않는다.
        self.trace(3, "금속 센서", f"GPIO{METAL_GPIO}에서 {METAL_VOTES}회 검사, "
                   f"{METAL_MIN_HITS}회 이상 감지하면 무조건 캔" if self.metal is not None
                   else "금속 센서 미연결 → AI 판정으로 진행")
        metal = majority_sensor(self.metal)
        self.trace(3, "금속 결과", "★ 금속 신호 감지! AI 인식 여부 무관 → 캔 확정" if metal is True else
                   ("금속 미감지 → AI로 종류 판정" if metal is False else
                    "금속 센서 미설치 → AI로 종류 판정"))

        camera_kind: str | None = None
        camera_reason: str | None = None
        scores: dict[str, float] = {}
        raw: int | None = None
        wet: bool | None = None
        if metal is True:
            self.trace(4, "카메라 AI", "금속이 감지되어 카메라 촬영·AI 분류 결과와 관계없이 캔 → AI 판정 생략")
            self.trace(5, "수분 센서", "금속 우선 규칙에 따라 수분 검사를 생략")
            self.status.patch(scores={})
        else:
            # 3. 비금속일 때만 AI를 실행한다. 실패 시 배출하지 않고 보류한다.
            self.trace(4, "카메라 AI", f"금속 미감지 → 새 프레임 {CLASSIFY_FRAMES}장 촬영·AI 판정")
            try:
                self.check_health(require_camera=True)
                camera_kind, camera_reason, scores = classify(self.feed, self.classifier)
            except Exception as exc:
                LOG.exception("[카메라 오류] 금속 미감지 → AI 판정 불가, 배출 보류")
                raise SafetyHold(f"금속 미감지·카메라 인식 실패: {exc}") from exc
            self.status.patch(scores=scores)
            self.trace(4, "카메라 결과", f"{KOREAN[camera_kind]} {scores[camera_kind]:.1%} → "
                       f"{camera_reason or 'AI 판정 기준 통과'}")
            if self.adc is None:
                self.trace(5, "수분 센서", "수분 센서 비활성화/미연결 → 검사 생략")
            else:
                self.trace(5, "수분 센서", f"ADC {MOISTURE_SAMPLES}회 측정하여 젖음 여부 판정")
            raw, wet = moisture_sensor(self.adc)
            if raw is not None:
                self.trace(5, "수분 결과", f"값 {raw} → {'젖음(배출 보류)' if wet else '마름'}")

        # 4. 금속 판정은 카메라/수분과 무관. 초음파 실측은 시작과 종료에만 실행.
        final_kind, reason = decide_final(camera_kind, camera_reason, metal, wet)
        camera_label = KOREAN[camera_kind] if camera_kind is not None else "생략(금속 우선)"
        if reason:
            self.trace(6, "최종 판정", f"배출 보류! 카메라={camera_label} / 사유={reason}")
            raise SafetyHold(reason)
        if metal is True:
            self.trace(6, "최종 판정", "★ 금속 감지로 '캔' 확정 (AI 점수·인식 실패·수분 모두 무관)")
        else:
            self.trace(6, "최종 판정", f"카메라={camera_label} → 최종={KOREAN[final_kind]}, 배출 허용")

        # 마지막으로 측정된 '시작 적재량'만 사용. 실제 측정은 하지 않음.
        start_fills = self.status.snapshot()["fills"]
        last_pct = start_fills[final_kind]["percent"]
        if last_pct is None:
            raise SafetyHold(f"{KOREAN[final_kind]} 통의 시작 적재량 검사 실패")
        self.trace(7, "통 적재량", f"시작 시 측정된 {KOREAN[final_kind]} 통={last_pct:.1f}%. "
                   "실행 중 추가 초음파 측정 없음 (종료 때 재측정)")
        if last_pct >= FILL_BLOCK_PERCENT:
            raise SafetyHold(f"{KOREAN[final_kind]} 통이 시작 시 {last_pct:.1f}%로 만재 상태 → 배출 차단")
        self.check_health()

        diverter, tray, diverter_note, direction = route_plan(final_kind)
        self.trace(8, "배출 경로", f"{KOREAN[final_kind]} 통 선택: "
                   f"분류판 {diverter_note} {diverter}° → 측정판 {direction} {tray}° "
                   f"→ 복귀 측정판 {TRAY_HOME}° / 분류판 {DIVERTER_HOME}°")
        self._state("배출 중", f"{KOREAN[final_kind]} 배출 동작 중", "blue")
        self.servos.drop(final_kind)
        self.counts[final_kind] += 1
        self._state("배출 동작 완료", f"{KOREAN[final_kind]} 경로로 서보 동작 완료", "green")
        self.status.patch(result=(f"카메라 {camera_label} → 최종 {KOREAN[final_kind]} / "
                                  "서보 동작 완료 (실제 낙하 확인 센서 없음)"))
        self.trace(9, "동작 완료", f"{KOREAN[final_kind]} 배출 명령·원위치 복귀 명령 완료 "
                   f"| 누적: 플라스틱 {self.counts['plastic']} / "
                   f"캔 {self.counts['can']} / 종이 {self.counts['paper']}")
        # 배출·복귀가 모두 끝난 뒤, QR로 연결된 학생에게 리워드 적립 (실패해도 분류기는 계속).
        record_reward(final_kind)

    def run(self):
        """단 1개 물체만 감지·판정한다. 성공/보류 모두 결과 표시 뒤 종료한다.

        기존 반복 실행의 진입 시 wait_for_clear가 이미 놓인 물체까지
        무한히 차단하던 문제를 제거한다. 감지 센서가 실제로 물체를 보는지는
        현장 점검이 필요하며, 센서 신호가 활성이라면 정상 입력인지 확인해야 한다.
        """
        self.servos.home(force=True)
        self._state("대기", "물체 1개 투입 대기", "blue")
        LOG.info("[장비 준비] 카메라·투입 감지(%s)·금속·서보 준비, 시작 시 원위치 명령 전송", ENTRY_MODE)
        start_fills = self.status.snapshot()["fills"]
        LOG.info("[적재량 시작값 실측] %s", " / ".join(
                 f"{KOREAN[k]} {start_fills[k]['percent']:.1f}%" for k in KOREAN))
        LOG.info("[★ 1회 분류 모드] 물체를 한 번 감지하면 한 번만 분류하고 자동 종료합니다")
        LOG.info("[출력 안내] 적재량은 시작/종료 1회씩. 금속 우선 → 비금속 AI → 배출·서보 복귀")
        # 기존의 '처음 IR가 감지 중이면 반드시 해제될 때까지 대기'를 제거한다.
        # 물체를 이미 올린 채 시작했어도 감지 신호가 안정되면 이번 1회로 처리한다.
        if self.ir is not None and bool(self.ir.value):
            LOG.warning("[투입 센서 시작 상태] 시작부터 감지 신호가 있습니다. "
                        "물체를 이미 올렸다면 1회 분류를 진행합니다. "
                        "아무것도 없다면 센서 극성/배선을 확인하고 즉시 Ctrl+C로 중단하세요")
        if not wait_for_object(self.ir, self.check_health):
            LOG.info("[1회 분류 취소] 물체 투입 전 사용자가 종료를 선택했습니다")
            return
        self.cycle_index = 1
        self.status.patch(result="", scores={})
        LOG.info("=" * 66)
        self.trace(1, "투입 감지", "물체 감지 완료 → 이번 1개만 측정합니다")
        rejected = False
        try:
            self.process_one()
        except SafetyHold as exc:
            rejected = True
            self.trace(9, "안전 보류", f"배출하지 않음: {exc}. 반려 상태로 1회 실행 종료")
            self._state("보류", str(exc), "red")
            self.status.patch(result=f"배출하지 않음: {exc}")
        except Exception:
            self.trace(9, "치명적 오류", "하드웨어/AI 오류로 이번 분류를 중단합니다")
            self._state("오류로 중지", "하드웨어 또는 AI 오류. 점검 후 다시 실행", "red")
            raise
        finally:
            # 성공·반려·오류 모두 안전한 초기 위치 명령을 다시 보낸다.
            self.servos.home(force=True)
        self.trace(9, "결과 표시", f"{'빨강(보류)' if rejected else '초록(배출 완료)'} "
                   f"{RESULT_SECONDS:.1f}초간 표시")
        time.sleep(RESULT_SECONDS)
        LOG.info("[★ 1회 분류 종료] 다른 물체를 기다리지 않습니다. "
                 "서보 원위치 및 적재량 최종 측정을 진행합니다")
        LOG.info("[분류 합계] %s", self.counts)


def create_ultrasonic(stack: ExitStack) -> dict[str, tuple[Any, Any]]:
    from gpiozero import DigitalInputDevice, DigitalOutputDevice
    sensors = {}
    for name, (trig_gpio, echo_gpio) in ULTRASONIC_PINS.items():
        trig = stack.enter_context(DigitalOutputDevice(trig_gpio, initial_value=False))
        echo = stack.enter_context(DigitalInputDevice(echo_gpio, pull_up=False))
        sensors[name] = (trig, echo)
    return sensors


def calibrate_empty_bins() -> int:
    """모든 통을 완전히 비운 뒤 현재 거리로 0% 기준을 갱신하는 명시적 명령."""
    validate_settings()
    LOG.info("[0% 기준 재설정] 세 통이 모두 비어 있는 상태에서 실행해야 합니다")
    with ExitStack() as stack:
        sensors = create_ultrasonic(stack)
        values: dict[str, float] = {}
        for kind, (trig, echo) in sensors.items():
            cm = read_distance(trig, echo)
            if cm is None:
                raise RuntimeError(f"{KOREAN[kind]} 통 초음파 측정 실패. 아무 기준도 덮어쓰지 않습니다")
            values[kind] = cm
            LOG.info("[0%% 재설정] %s 통: 현재 %.2f cm → 0%% 기준", KOREAN[kind], cm)
    save_empty_calibration(values)
    LOG.info("[0% 재설정] 완료. 다음 일반 실행부터 시작/종료 2번만 측정합니다")
    return 0


def entry_sensor_test() -> int:
    """AI/서보를 구동하지 않고 4번째 투입 센서의 거리/감지 변화만 시험한다."""
    validate_settings()
    if ENTRY_MODE == "manual":
        raise ValueError("수동 모드에서는 투입 센서를 검사할 수 없습니다")
    LOG.info("[투입 센서 단독 시험] 투입구에 물체를 치우고 시작하세요. Ctrl+C로 종료")
    with ExitStack() as stack:
        sensor = make_entry_sensor(stack)
        try:
            while True:
                _ = sensor.value
                time.sleep(IR_POLL_SECONDS)
        except KeyboardInterrupt:
            LOG.info("[투입 센서 단독 시험] 종료")
    return 0


def rgb_test() -> int:
    """분류/적재량 RGB 2개의 R/G/B 채널을 각각 실제로 점등해 배선을 확인한다.

    이 옵션은 센서, 서보, 카메라를 시작하지 않는다.
    """
    validate_settings()
    LOG.info("[RGB 단독 검사] RGB1=R/G/B, RGB2=R/G만 각각 1.2초 확인합니다")
    LOG.info("[RGB 1 분류 배선] %s", STATUS_RGB_PINS)
    LOG.info("[RGB 2 적재량 배선] %s", FILL_RGB_PINS)
    with ExitStack() as stack:
        led = StatusIndicator(stack, SharedStatus())
        stack.callback(led.close)
        for color, korean in (("red", "빨강"), ("green", "초록"), ("blue", "파랑")):
            LOG.info("[RGB 단독 검사] RGB 1 분류 → %s / RGB 2 적재량 → 초록", korean)
            led.set(color)
            led.set_fill("green")
            time.sleep(1.2)
        for color, korean in (("red", "빨강"), ("green", "초록")):
            LOG.info("[RGB 단독 검사] RGB 1 분류 → 파랑 / RGB 2 적재량 → %s", korean)
            led.set("blue")
            led.set_fill(color)
            time.sleep(1.2)
    LOG.info("[RGB 단독 검사] 완료. GPIO 해제로 두 RGB LED는 꺼집니다")
    return 0


def home_only() -> int:
    """AI/카메라/초음파 없이 서보 원위치 명령만 점검한다."""
    validate_settings()
    with ExitStack() as stack:
        LOG.info("[원위치 점검] 투입 물체를 제거하고 서보에 손을 가까이 대지 마세요")
        servos = Servos(stack)   # 생성 즉시 원위치 PWM 명령을 보냄
        servos.home(force=True)
        LOG.info("[원위치 점검] 원위치 재전송 완료: 측정판 %d°, 분류판 %d°", TRAY_HOME, DIVERTER_HOME)
    return 0


def run_hardware() -> int:
    from gpiozero import DigitalOutputDevice, MCP3208
    validate_settings()
    with ExitStack() as stack:
        status = SharedStatus()
        # 1. 가장 먼저 실제 보정 각도로 두 서보를 강제 원위치 이동.
        servos = Servos(stack)
        LOG.info("[시작 원위치] 측정판 %d° / 분류판 %d° 명령 완료", TRAY_HOME, DIVERTER_HOME)
        # 2. AI보다 먼저 초음파 3통 각각 1회 측정. 최초에는 현재 거리를 0% 기준으로 저장.
        sensor_pins = create_ultrasonic(stack)
        alert = (stack.enter_context(DigitalOutputDevice(ALERT_GPIO, initial_value=False))
                 if ALERT_GPIO is not None else None)
        # RGB를 AI·카메라 초기화 이전에 켜서 대기 표시를 확실히 보여준다.
        indicator = StatusIndicator(stack, status)
        stack.callback(indicator.close)
        monitor = FillMonitor(sensor_pins, status, alert, indicator)
        started = False
        try:
            monitor.measure_at_startup()
            started = True
            LOG.info("[시작 검사] 두 서보 원위치 완료 → 적재량 3통 시작 측정 완료 → 카메라 준비")
            if not MODEL_PATH.is_file() or not LABELS_PATH.is_file():
                raise FileNotFoundError(f"{MODEL_PATH.name}, {LABELS_PATH.name} 파일을 코드와 같은 폴더에 넣으세요")
            model = Classifier()
            from picamera2 import Picamera2
            camera = Picamera2(camera_num=0)
            stack.callback(camera.close)
            camera.configure(camera.create_preview_configuration(main={
                "size": CAMERA_SIZE, "format": "BGR888"}, buffer_count=4))
            camera.start(show_preview=False)
            stack.callback(camera.stop)
            feed = CameraFeed(camera)
            stack.callback(feed.close)
            feed.start()
            LOG.info("[장비 초기화] 카메라 프레임 수신 완료 (%dx%d, 최대 %dFPS)",
                     CAMERA_SIZE[0], CAMERA_SIZE[1], CAMERA_FPS)
            if WEB_ENABLED:
                dashboard = Dashboard(feed, status)
                dashboard.start()
                stack.callback(dashboard.close)
            ir = make_entry_sensor(stack)
            metal = make_input(METAL_GPIO, METAL_ACTIVE_LOW, stack)
            adc = stack.enter_context(MCP3208(**MOISTURE_ADC)) if MOISTURE_ENABLED else None
            LOG.info("[장비 초기화] 투입 감지=%s / 금속=GPIO%s / 수분=%s / 측정판=GPIO%d / 분류판=GPIO%d",
                     (f"IR GPIO{IR_GPIO} OR 초음파 TRIG{ENTRY_TRIG_GPIO}/ECHO{ENTRY_ECHO_GPIO} 최대{ENTRY_DETECT_MAX_CM:.0f}cm" if ENTRY_MODE == "both" else
                      f"초음파 TRIG{ENTRY_TRIG_GPIO}/ECHO{ENTRY_ECHO_GPIO} 최대{ENTRY_DETECT_MAX_CM:.0f}cm" if ENTRY_MODE == "ultrasonic" else
                      f"IR GPIO{IR_GPIO}" if ENTRY_MODE == "ir" else "수동"),
                     METAL_GPIO if METAL_GPIO is not None else "미설치",
                     "활성" if MOISTURE_ENABLED else "비활성", TRAY_GPIO, DIVERTER_GPIO)
            LOG.info("[적재량 측정 일정] 시작 1회 측정 완료 / 운전 중 측정 없음 / 정상 종료 때 1회 재측정")
            LOG.info("[기기 화면] %s", f"http://<라즈베리파이 IP>:{WEB_PORT}" if WEB_ENABLED else "꺼짐")
            Sorter(feed, model, servos, ir, metal, adc, monitor, indicator, status).run()
        except KeyboardInterrupt:
            LOG.info("[종료] Ctrl+C 입력 감지")
        finally:
            status.patch(state="종료 중", message="서보 원위치 복귀 및 적재량 마지막 측정")
            try:
                servos.home(force=True)
            except Exception:
                LOG.exception("[종료] 서보 원위치 명령 실패. 하드웨어 점검 필요")
            if started:
                try:
                    monitor.measure_at_shutdown()
                    LOG.info("[종료 RGB] 적재량 결과를 %.0f초 표시한 후 GPIO를 해제합니다",
                             FILL_FINAL_DISPLAY_SECONDS)
                    time.sleep(FILL_FINAL_DISPLAY_SECONDS)
                except Exception:
                    LOG.exception("[종료] 적재량 최종 검사 오류: RGB2는 마지막 유효 적재량 판정을 유지")
                    # 측정 실패만을 이유로 RGB2를 빨강으로 바꾸지 않는다.
                    time.sleep(FILL_FINAL_DISPLAY_SECONDS)
            else:
                LOG.warning("[종료] 시작 초음파 검사가 완료되지 않아 종료 적재량 비교는 실행하지 않음")
            LOG.info("[종료] 서보 원위치 및 적재량 최종 확인 완료. GPIO/카메라 정리")
    return 0


def self_test() -> None:
    """실제 GPIO·AI 모델 없이 순수 판정 로직 회귀 검사."""
    validate_settings()
    assert ENTRY_MODE == "both"  # 기본은 IR 또는 초음파 중 하나라도 감지하면 분류
    assert CombinedEntrySensor(type("IR", (), {"value": True})(), None).value is True
    assert entry_detected(18, 25) is True  # 빈 판보다 7cm 가까워짐
    assert entry_detected(24, 25) is False  # 빈 판을 물체로 오인하면 안 됨
    assert entry_detected(32, None) is True  # 배경이 멀다면 절대 범위만 사용
    assert entry_detected(39, None) is False
    assert decide_camera({"plastic": .91, "can": .04, "paper": .05}) == ("plastic", None)
    assert decide_camera({"plastic": .34, "can": .20, "paper": .46})[1] is not None
    assert decide_camera({"plastic": .65, "can": .20, "paper": .15})[1] is not None  # 기준(70%) 미달 → 보류
    assert decide_camera({"plastic": .75, "can": .15, "paper": .10}) == ("plastic", None)  # 기준 통과
    assert decide_final("paper", "AI 신뢰도 부족", True, True) == ("can", None)
    assert decide_final(None, "카메라 오류", True, True) == ("can", None)
    assert decide_final("can", None, False, False)[1] is not None
    assert decide_final("paper", None, False, True)[1] is not None
    assert decide_final("plastic", None, False, False) == ("plastic", None)
    assert route_plan("plastic")[1] == 50 and route_plan("paper")[1] == -75
    assert TRAY_HOME == -10 and DIVERTER_HOME == 60 and DIVERTER_RIGHT == 90
    assert percent_full("plastic", EMPTY_CM["plastic"]) == 0.0
    assert percent_full("plastic", FULL_CM["plastic"]) == 100.0
    assert percent_full("plastic", (EMPTY_CM["plastic"] + FULL_CM["plastic"]) / 2) == 50.0
    assert abs(sum(interpret_probs([2.0, 1.0, -1.0])) - 1.0) < 1e-10
    assert max(interpret_probs([0.9, 0.05, 0.05])) > .89
    LOG.info("자가 테스트 통과: 분류/금속 우선/수분 보류/적재량/출력 정규화/GPIO 충돌 검사")


def main() -> int:
    parser = argparse.ArgumentParser(description="스마트 분리수거기 통합 제어")
    parser.add_argument("--check-config", action="store_true", help="하드웨어 접근 없이 GPIO·설정 검사")
    parser.add_argument("--home-only", action="store_true", help="AI/카메라 없이 서보 원위치 명령 강제 전송")
    parser.add_argument("--entry-test", action="store_true", help="AI/서보 없이 투입 센서만 단독 검사 (Ctrl+C 종료)")
    parser.add_argument("--rgb-test", action="store_true", help="센서/서보/AI 없이 RGB LED 2개 색상 순서 점등")
    parser.add_argument("--calibrate-empty", "--reset-empty", action="store_true", help="빈 통 3개를 현재 거리로 0%% 재보정 (통을 비운 경우에만 사용)")
    parser.add_argument("--self-test", action="store_true", help="하드웨어 접근 없이 주요 판정 테스트")
    parser.add_argument("--debug", action="store_true", help="자세한 로그")
    opts = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if opts.debug else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout, force=True)
    if opts.check_config:
        validate_settings()
        print("설정/GPIO 중복 검사 통과 (실물 각도·전압·배선은 별도 확인 필수)")
        print(f"측정판 BCM{TRAY_GPIO}: 원위치 {TRAY_HOME}°, 왼쪽 {TRAY_LEFT}°, 오른쪽 {TRAY_RIGHT}°")
        print(f"분류판 BCM{DIVERTER_GPIO}: 원위치 {DIVERTER_HOME}°, 캔 오른쪽 {DIVERTER_RIGHT}°")
        print(f"투입 감지={ENTRY_MODE}" +
              (f"(IR BCM{IR_GPIO} OR 초음파 TRIG BCM{ENTRY_TRIG_GPIO}, ECHO BCM{ENTRY_ECHO_GPIO}, 최대 {ENTRY_DETECT_MAX_CM:.0f}cm)"
               if ENTRY_MODE == "both" else
               f"(TRIG BCM{ENTRY_TRIG_GPIO}, ECHO BCM{ENTRY_ECHO_GPIO}, 최대 {ENTRY_DETECT_MAX_CM:.0f}cm)"
               if ENTRY_MODE == "ultrasonic" else f"(IR BCM{IR_GPIO})" if ENTRY_MODE == "ir" else ""))
        print(f"금속 BCM{METAL_GPIO}")
        print(f"수분 사용={MOISTURE_ENABLED}, 분류 RGB={STATUS_RGB_PINS}, "
              f"적재량 RGB={FILL_RGB_PINS}, 웹 포트 {WEB_PORT}")
        return 0
    if opts.self_test:
        self_test()
        print("SELF TEST PASS")
        return 0
    try:
        if sum(bool(k) for k in (opts.home_only, opts.calibrate_empty, opts.entry_test, opts.rgb_test)) > 1:
            parser.error("--home-only, --entry-test, --rgb-test, --calibrate-empty는 동시에 지정할 수 없습니다")
        if opts.rgb_test:
            return rgb_test()
        if opts.entry_test:
            return entry_sensor_test()
        if opts.home_only:
            return home_only()
        if opts.calibrate_empty:
            return calibrate_empty_bins()
        return run_hardware()
    except (KeyboardInterrupt, SystemExit):
        LOG.info("종료")
        return 130
    except Exception:
        LOG.exception("안전 정지: 자동 복구하지 않음. 배선·모델·센서·서보를 확인하세요")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
