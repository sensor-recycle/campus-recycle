import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st


KST = timezone(timedelta(hours=9))

COLUMNS = [
    "event_id", "student_id", "department", "material",
    "quantity", "weight_g", "points", "created_at",
]


def init_data():
    if "demo_users" not in st.session_state:
        st.session_state.demo_users = {}

    if "demo_records" not in st.session_state:
        st.session_state.demo_records = []


def register_user(student_id, department):
    init_data()
    users = st.session_state.demo_users

    if student_id in users and users[student_id] != department:
        return False

    users[student_id] = department
    return True


def save_record(student_id, material, quantity=0, weight_g=0.0):
    init_data()

    if student_id not in st.session_state.demo_users:
        raise ValueError("등록되지 않은 사용자입니다.")

    if material not in ["플라스틱", "캔", "종이"]:
        raise ValueError("지원하지 않는 재질입니다.")

    if material == "종이":
        if weight_g <= 0:
            raise ValueError("종이 무게는 0보다 커야 합니다.")

        quantity = 0

        # 누적 10g당 1P
        previous_weight = sum(
            record["weight_g"]
            for record in st.session_state.demo_records
            if record["student_id"] == student_id
            and record["material"] == "종이"
        )
        points = (
            int((previous_weight + weight_g) // 10)
            - int(previous_weight // 10)
        )
    else:
        if quantity < 1 or int(quantity) != quantity:
            raise ValueError("개수는 1 이상의 정수여야 합니다.")

        quantity = int(quantity)
        weight_g = 0.0
        points = quantity * 10

    st.session_state.demo_records.append({
        "event_id": str(uuid.uuid4()),
        "student_id": student_id,
        "department": st.session_state.demo_users[student_id],
        "material": material,
        "quantity": quantity,
        "weight_g": float(weight_g),
        "points": points,
        "created_at": datetime.now(KST).isoformat(timespec="seconds"),
    })

    return points


def load_records(student_id=None):
    init_data()
    df = pd.DataFrame(st.session_state.demo_records, columns=COLUMNS)

    for column in ["quantity", "weight_g", "points"]:
        df[column] = pd.to_numeric(df[column])

    if student_id is not None:
        df = df[df["student_id"] == student_id]

    return (
        df.sort_values("created_at", ascending=False)
        .reset_index(drop=True)
    )