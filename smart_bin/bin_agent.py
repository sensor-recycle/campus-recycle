#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""수거함 대기 프로그램 (라즈베리 파이에서 계속 켜 둔다).

웹에서 '수거 시작'을 누르면 DB에 요청이 남는다. 이 프로그램이 2초마다 그 요청을 확인하다가
새 요청이 오면 main.py를 1회 실행하고, 끝나면 다시 대기한다.

    cd ~/smart_bin && python ~/campus-recycle/smart_bin/bin_agent.py

main.py 위치가 ~/smart_bin/main.py 가 아니면 경로를 인자로 준다:
    python bin_agent.py /경로/main.py
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# campus-recycle 저장소를 clone한 위치의 recycle-web 폴더 (data.py가 있는 곳)
REWARD_DIR = Path.home() / "campus-recycle" / "recycle-web"
MAIN_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "smart_bin" / "main.py"
POLL_SECONDS = 2
STOP_GRACE_SECONDS = 30   # 시간 초과로 멈출 때 main.py가 서보 원위치·종료 측정을 끝내도록 기다리는 시간

LOG = logging.getLogger("bin-agent")


def run_once(timeout):
    """main.py를 1회 실행한다. timeout초가 지나도 안 끝나면 Ctrl+C와 같은 신호로 멈춘다."""
    process = subprocess.Popen([sys.executable, str(MAIN_PATH)], cwd=str(MAIN_PATH.parent))
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        LOG.warning("%d초 안에 끝나지 않아 main.py를 멈춥니다 (물건이 투입되지 않음)", timeout)
        # SIGINT = Ctrl+C: main.py가 서보를 원위치로 돌리고 정리한 뒤 끝난다.
        process.send_signal(signal.SIGINT) if os.name != "nt" else process.terminate()
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            LOG.error("main.py가 멈추지 않아 강제 종료합니다. 서보 위치를 확인하세요")
            process.kill()
            process.wait()
    except KeyboardInterrupt:
        # 터미널의 Ctrl+C는 main.py에도 함께 전달된다. 정리가 끝날 때까지 기다린다.
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.kill()
        raise
    return process.returncode


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [대기] %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    if not MAIN_PATH.is_file():
        LOG.error("main.py를 찾을 수 없습니다: %s", MAIN_PATH)
        return 1
    sys.path.insert(0, str(REWARD_DIR))
    try:
        import data
    except ImportError:
        LOG.error("data.py를 불러올 수 없습니다. 저장소가 %s 에 있는지 확인하세요", REWARD_DIR.parent)
        return 1
    if not data.SUPABASE:
        LOG.warning("DB 설정(secrets.toml)이 없어 이 기기의 JSON 파일만 봅니다. 웹의 버튼은 전달되지 않습니다")
    run_timeout = data.SESSION_SECONDS - 15   # 웹의 연결 시간(3분) 안에 끝나도록

    try:
        # 꺼져 있던 동안 남은 요청은 지운다 (뒤늦게 혼자 시작하지 않도록).
        # 부팅 직후에는 인터넷이 아직 안 붙었을 수 있으니, 될 때까지 기다린 다음에 대기를 시작한다.
        while True:
            try:
                data.finish_run()
                break
            except Exception as error:
                LOG.warning("DB에 연결하지 못했습니다: %s → 5초 뒤 재시도 (인터넷 확인)", type(error).__name__)
                time.sleep(5)
        LOG.info("웹의 '수거 시작'을 기다립니다. 실행할 파일: %s (종료: Ctrl+C)", MAIN_PATH)

        while True:
            try:
                student_id = data.claim_start()
            except Exception as error:
                LOG.warning("DB 확인 실패: %s → 5초 뒤 재시도", type(error).__name__)
                time.sleep(5)
                continue
            if student_id:
                LOG.info("★ '수거 시작' 요청 수신 → main.py 실행")
                code = run_once(run_timeout)
                LOG.info("main.py 종료 (코드 %s) → 다시 대기", code)
                try:
                    data.finish_run()
                except Exception:
                    LOG.exception("종료 상태를 DB에 알리지 못했습니다")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        LOG.info("대기 프로그램을 종료합니다")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
