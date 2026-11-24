import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import pandas as pd

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
    with write_lock():
        store = read_store()
        if student_id in store["users"]:
            return store["users"][student_id] == department
        store["users"][student_id] = department
        write_store(store)
        return True

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
    records = [row for row in read_store()["records"] if row["points"] > 0]
    if student_id is not None:
        records = [row for row in records if row["student_id"] == student_id]

    return pd.DataFrame(records[::-1], columns=COLUMNS).sort_values(
        "created_at", ascending=False, kind="stable", ignore_index=True
    )