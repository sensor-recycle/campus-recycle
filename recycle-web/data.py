import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

KST = timezone(timedelta(hours=9))
# 재질별 (하루 적립 횟수, 배출 1회당 포인트)
RULES = {"플라스틱": (5, 10), "캔": (5, 10), "종이": (3, 5)}
STORE = Path(__file__).with_name("recycling_records.json")
COLUMNS = ["event_id", "student_id", "department", "material", "quantity", "points", "created_at"]


@contextmanager
def write_lock():
    # 한 기기의 여러 웹 세션이 동시에 마지막 적립 기회를 쓰지 못하도록 막음
    with STORE.with_suffix(".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def read_store():
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"users": {}, "records": []}


def write_store(store):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=STORE.parent, delete=False) as tmp:
        path = Path(tmp.name)
        try:
            json.dump(store, tmp, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            tmp.close()
            path.unlink(missing_ok=True)
            raise
    try:
        os.replace(path, STORE)
    finally:
        path.unlink(missing_ok=True)


def init_data():
    with write_lock():
        if not STORE.exists():
            write_store({"users": {}, "records": []})


def register_user(student_id, department):
    # 학과는 로그인할 때 고른 것으로 맞춘다. 전에 다른 학과로 등록한 학번도 그대로 로그인된다.
    with write_lock():
        store = read_store()
        if store["users"].get(student_id) != department:
            store["users"][student_id] = department
            write_store(store)
        return True

# ── 수거함 연결 (QR) ──────────────────────────────────────────────────────
# 수거함 QR을 찍고 로그인한 학생이 SESSION_SECONDS 동안 "지금 버리는 사람"이 된다.
# 라즈베리 파이는 active_student()로 그 학번을 얻어 save_record()에 넘기면 된다.
SESSION_SECONDS = 180
# 웹의 '수거 시작'으로 파이가 분류를 진행 중인 상태 (아래 "수거 시작 버튼" 구역 참고)
RUN_STATES = ("running", "ready")


def start_session(student_id):
    with write_lock():
        store = read_store()
        if student_id not in store["users"]:
            raise ValueError("등록되지 않은 사용자입니다.")
        store["active"] = {
            "student_id": student_id,
            "started_at": datetime.now(KST).isoformat(timespec="seconds"),
        }
        write_store(store)


def end_session(student_id):
    with write_lock():
        store = read_store()
        if (store.get("active") or {}).get("student_id") == student_id:
            store["active"] = None
            write_store(store)


def active_student():
    """지금 수거함에 연결된 학번. 없거나 시간이 지났으면 None."""
    active = read_store().get("active")
    if not active or not active.get("student_id"):
        return None
    if active.get("state") in RUN_STATES:  # 파이가 이 학생의 요청으로 분류 중
        return active["student_id"]
    if not active.get("started_at"):
        return None
    started = datetime.fromisoformat(active["started_at"])
    if datetime.now(KST) - started > timedelta(seconds=SESSION_SECONDS):
        return None
    return active["student_id"]


# 배출 현황 저장 및 포인트 반환
def save_record(student_id, material, quantity=1):
    if material not in RULES:
        raise ValueError("지원하지 않는 재질입니다.")
    if isinstance(quantity, bool) or int(quantity) != quantity or quantity < 1:
        raise ValueError("수량은 1 이상의 정수여야 합니다.")

    with write_lock():
        store = read_store()
        if student_id not in store["users"]:
            raise ValueError("등록되지 않은 사용자입니다.")

        now = datetime.now(KST)
        today = now.date().isoformat()
        limit, reward = RULES[material]

        rewarded = sum(
            row["student_id"] == student_id and row["material"] == material
            and row["created_at"][:10] == today and row["points"] > 0
            for row in store["records"]
        )
        if rewarded >= limit:
            return 0

        points = reward
        store["records"].append({
            "event_id": str(uuid4()), "student_id": student_id,
            "department": store["users"][student_id], "material": material,
            "quantity": int(quantity), "points": points,
            "created_at": now.isoformat(timespec="seconds"),
        })
        write_store(store)
        return points


def load_records(student_id=None):
    import pandas as pd  # 웹에서만 필요 (라즈베리 파이에는 pandas가 없어도 된다)

    records = [row for row in read_store()["records"] if row["points"] > 0]
    if student_id is not None:
        records = [row for row in records if row["student_id"] == student_id]

    return pd.DataFrame(records[::-1], columns=COLUMNS).sort_values(
        "created_at", ascending=False, kind="stable", ignore_index=True
    )


# ── 공용 DB (Supabase) ────────────────────────────────────────────────────
# 웹(Streamlit Cloud)과 라즈베리 파이는 서로 다른 기기라 위의 JSON 파일을 함께 쓸 수 없다.
# SUPABASE_URL / SUPABASE_KEY 가 설정돼 있으면 아래 _db_* 함수들이 위의 JSON 버전을 대신하고,
# 설정이 없으면 지금까지처럼 이 폴더의 JSON 파일을 쓴다. 규칙(한도·포인트·연결 시간)은 같다.
# 테이블은 supabase_schema.sql 을 Supabase SQL Editor 에 한 번 붙여 넣어 만든다.
def _supabase_config():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):  # 로컬 실행 · 라즈베리 파이: 이 폴더의 .streamlit/secrets.toml
        # tomllib은 파이썬 3.11부터 있어서(라즈베리 파이는 3.9일 수 있음) KEY = "값" 줄을 직접 읽는다.
        try:
            text = (Path(__file__).parent / ".streamlit" / "secrets.toml").read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
        found = dict(re.findall(
            r"""^[ \t]*(SUPABASE_URL|SUPABASE_KEY)[ \t]*=[ \t]*["'“”]([^"'“”\r\n]*)["'“”]""",
            text, re.M))
        url, key = found.get("SUPABASE_URL"), found.get("SUPABASE_KEY")
    if not (url and key):  # Streamlit Cloud: 앱 설정의 Secrets
        try:
            import streamlit as st
            if st.secrets.load_if_toml_exists():
                url, key = st.secrets.get("SUPABASE_URL"), st.secrets.get("SUPABASE_KEY")
        except Exception:
            pass
    return (str(url).rstrip("/"), str(key)) if url and key else None


SUPABASE = _supabase_config()
_HTTP = None


def _db(method, table, params=None, body=None, prefer=None):
    global _HTTP
    import requests

    if _HTTP is None:
        _HTTP = requests.Session()
    url, key = SUPABASE
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if prefer:
        headers["Prefer"] = prefer
    response = _HTTP.request(
        method, f"{url}/rest/v1/{table}", params=params, json=body, headers=headers, timeout=8
    )
    response.raise_for_status()
    return response.json() if response.content else []


def _db_department(student_id):
    rows = _db("GET", "users", {"select": "department", "student_id": f"eq.{student_id}"})
    return rows[0]["department"] if rows else None


def _db_init_data():
    pass


def _db_register_user(student_id, department):
    # 학과는 로그인할 때 고른 것으로 맞춘다 (없으면 등록, 있으면 학과만 바꿈).
    _db("POST", "users", {"on_conflict": "student_id"},
        {"student_id": student_id, "department": department},
        prefer="resolution=merge-duplicates")
    return True


def _db_start_session(student_id):
    if _db_department(student_id) is None:
        raise ValueError("등록되지 않은 사용자입니다.")
    _db("POST", "bin_session", {"on_conflict": "id"}, {
        "id": 1, "student_id": student_id,
        "started_at": datetime.now(KST).isoformat(timespec="seconds"),
    }, prefer="resolution=merge-duplicates")


def _db_end_session(student_id):
    _db("PATCH", "bin_session", {"id": "eq.1", "student_id": f"eq.{student_id}"},
        {"student_id": None})


def _db_active_student():
    rows = _db("GET", "bin_session", {"select": "*", "id": "eq.1"})
    if not rows or not rows[0].get("student_id"):
        return None
    if rows[0].get("state") in RUN_STATES:  # 파이가 이 학생의 요청으로 분류 중
        return rows[0]["student_id"]
    if not rows[0].get("started_at"):
        return None
    started = datetime.fromisoformat(rows[0]["started_at"])
    if datetime.now(KST) - started > timedelta(seconds=SESSION_SECONDS):
        return None
    return rows[0]["student_id"]


def _db_save_record(student_id, material, quantity=1):
    if material not in RULES:
        raise ValueError("지원하지 않는 재질입니다.")
    if isinstance(quantity, bool) or int(quantity) != quantity or quantity < 1:
        raise ValueError("수량은 1 이상의 정수여야 합니다.")

    department = _db_department(student_id)
    if department is None:
        raise ValueError("등록되지 않은 사용자입니다.")

    now = datetime.now(KST)
    limit, reward = RULES[material]
    rewarded = _db("GET", "records", {
        "select": "event_id", "student_id": f"eq.{student_id}", "material": f"eq.{material}",
        "created_at": f"like.{now.date().isoformat()}*", "points": "gt.0",
    })
    if len(rewarded) >= limit:
        return 0

    _db("POST", "records", body={
        "event_id": str(uuid4()), "student_id": student_id, "department": department,
        "material": material, "quantity": int(quantity), "points": reward,
        "created_at": now.isoformat(timespec="seconds"),
    })
    return reward


def _db_load_records(student_id=None):
    import pandas as pd

    params = {"select": ",".join(COLUMNS), "points": "gt.0", "order": "created_at.desc,seq.desc"}
    if student_id is not None:
        params["student_id"] = f"eq.{student_id}"
    return pd.DataFrame(_db("GET", "records", params), columns=COLUMNS)


if SUPABASE:
    init_data, register_user, load_records, save_record = (
        _db_init_data, _db_register_user, _db_load_records, _db_save_record)
    start_session, end_session, active_student = (
        _db_start_session, _db_end_session, _db_active_student)


# ── 웹의 '수거 시작' 버튼 ↔ 라즈베리 파이 ─────────────────────────────────
# 웹(클라우드)은 파이에 직접 접속할 수 없어서, 버튼을 누르면 DB에 요청만 남긴다.
# 파이의 대기 프로그램(smart_bin/bin_agent.py)이 그 요청을 보고 main.py를 1회 실행한다.
#   state: requested(버튼 누름) → running(파이가 실행 시작) → ready(투입 대기) → done(끝)
START_WAIT_SECONDS = 20  # 버튼을 누른 뒤 파이가 응답하기를 기다려 주는 시간


def _read_session():
    if SUPABASE:
        rows = _db("GET", "bin_session", {"select": "*", "id": "eq.1"})
        return rows[0] if rows else {}
    return read_store().get("active") or {}


def _write_session(fields, only_if_state=None):
    """수거함 상태를 고친다. only_if_state를 주면 지금 그 상태일 때만 고치고 성공 여부를 돌려준다."""
    if SUPABASE:
        if only_if_state is None:
            _db("POST", "bin_session", {"on_conflict": "id"}, {"id": 1, **fields},
                prefer="resolution=merge-duplicates")
            return True
        return bool(_db("PATCH", "bin_session", {"id": "eq.1", "state": f"eq.{only_if_state}"},
                        fields, prefer="return=representation"))
    with write_lock():
        store = read_store()
        active = store.get("active") or {}
        if only_if_state is not None and active.get("state") != only_if_state:
            return False
        active.update(fields)
        store["active"] = active
        write_store(store)
        return True


def bin_status(student_id):
    """웹 화면용 수거함 상태: idle / waking / no_response / running / ready / busy"""
    session = _read_session()
    state, requested = session.get("state"), session.get("requested_at")
    if state not in ("requested",) + RUN_STATES or not requested:
        return "idle"
    age = (datetime.now(KST) - datetime.fromisoformat(requested)).total_seconds()
    mine = session.get("student_id") == student_id
    if state == "requested":  # 버튼은 눌렀는데 파이가 아직 가져가지 않음
        if age <= START_WAIT_SECONDS:
            return "waking" if mine else "busy"
        return "no_response" if mine and age <= SESSION_SECONDS else "idle"
    if age > SESSION_SECONDS:  # 파이가 끝났다고 알리지 못한 채 시간이 지남
        return "idle"
    return state if mine else "busy"


def request_start(student_id):
    """웹의 '수거 시작': 이 학생을 수거함에 연결하고 파이에게 1회 분류를 요청한다. 사용 중이면 False."""
    if bin_status(student_id) == "busy":
        return False
    start_session(student_id)
    _write_session({
        "requested_at": datetime.now(KST).isoformat(timespec="seconds"), "state": "requested",
    })
    return True


def claim_start():
    """(파이) 새 '수거 시작' 요청이 있으면 가져가고 그 학번을 돌려준다. 없으면 None."""
    session = _read_session()
    if session.get("state") != "requested":
        return None
    if not _write_session({"state": "running"}, only_if_state="requested"):
        return None
    return session.get("student_id")


def mark_ready():
    """(파이) main.py가 투입 대기에 들어갔음을 웹에 알린다."""
    return _write_session({"state": "ready"}, only_if_state="running")


def finish_run():
    """(파이) 1회 분류가 끝났음을 알린다. 대기 프로그램을 켤 때 남은 요청을 지우는 데도 쓴다."""
    _write_session({"state": "done"})
