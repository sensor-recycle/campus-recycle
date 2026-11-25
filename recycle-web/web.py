from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import ui
from data import (
    KST, RULES, bin_status, end_session, init_data, load_records,
    register_user, request_start, start_session,
)


st.set_page_config(
    page_title="센서 3팀 - 캠퍼스 스마트 분리수거함",
    page_icon="❄️",
    layout="centered",
)
init_data()

SUBJECT = ["가족자원경영학과", "경영학부", "경제학부", "공예과", "관현악과", "교육학부", "글로벌서비스학부", "글로벌협력전공", "기계시스템학부", "데이터사이언스전공", "독일언어·문화학과", "르꼬르동블루외식경영전공", "무용과", "문헌정보학과", "문화관광외식학부", "문화관광학전공", "미디어학부", "미디어학전공", "법학부", "사회심리학과", "산업디자인과", "생명시스템학부", "성악과", "소비자경제학과", "소프트웨어학부", "수학과", "숙명-CQU 경영국제학부", "시각·영상디자인과", "식품영양학과", "신소재물리학부", "아동복지학부", "앙트러프러너십전공", "약학부", "역사문화학과", "영어영문학부", "영어영문학전공", "융합국제학부", "의류학과", "인공지능공학부", "일본학과", "자유전공학부", "작곡과", "정치외교학과", "중어중문학부", "지능형전자시스템학부", "첨단공학부", "체육교육과", "컴퓨터과학전공", "테슬(TESL)전공", "통계학과", "프랑스언어·문화학과", "피아노과", "한국어문학부", "한류국제학부", "행정학과", "홍보광고학과", "화공생명공학부", "화학과", "환경디자인과", "회화과"]
MATERIAL = ["플라스틱", "캔", "종이"]
MENU = ["내 실적", "배출 내역·학과 현황"]
# 수거함에 붙인 QR 주소(…/?bin=1)로 들어온 접속만 수거함과 연결한다.
VIA_BIN = "bin" in st.query_params


ui.inject_css()


with st.container(key="page_header"):
    ui.hero(
        "캠퍼스 스마트 분리수거",
        "개인 배출 내역과 학과별 실적을 조회하세요.",
        mascot="cheer" if "user" in st.session_state else "ani",
    )


# 로그인 화면
if "user" not in st.session_state:
    with st.container(key="sec_login"):
        st.subheader("사용자 정보")

    with st.form("login_form"):
        student_id = st.text_input(
            "학번", placeholder="학번을 입력해주세요", max_chars=7
        )
        department = st.selectbox("학과", SUBJECT)
        start = st.form_submit_button(
            "실적 조회하기", type="primary", use_container_width=True
        )

    if start:
        student_id = student_id.strip()
        if not (student_id.isascii() and student_id.isdigit()):
            st.error("숫자만 입력해주세요.")
        elif not register_user(student_id, department):
            st.error("학번과 학과를 올바르게 입력했는지 확인해주세요.")
        else:
            st.session_state.user = {
                "student_id": student_id,
                "department": department,
            }
            if VIA_BIN and bin_status(student_id) != "busy":
                start_session(student_id)
            st.rerun()

    st.stop()


# 사용자 정보 및 메뉴
user = st.session_state.user

with st.container(key="userbar"):
    info_col, switch_col = st.columns([4, 1], vertical_alignment="center")
    with info_col:
        ui.user_chip(user["department"], user["student_id"])
    if switch_col.button("사용자 변경"):
        end_session(user["student_id"])
        del st.session_state["user"]
        st.rerun()

# 수거함 상태 (수거함 QR로 들어온 경우에만 '수거 시작'을 쓸 수 있다)
BIN_MESSAGES = {
    None: "수거함의 QR을 찍고 들어오면 배출이 적립돼요.",
    "idle": "분리수거 하는 당신은 멋진 눈송이!",
    "waking": "수거함을 깨우는 중이에요. 잠시만 기다려주세요!",
    "running": "수거함을 준비하고 있어요. 잠시만 기다려주세요!",
    "ready": "준비 완료! 수거함에 물건을 올려주세요.",
    "no_response": "수거함이 응답하지 않아요. 전원과 인터넷을 확인한 뒤 다시 눌러주세요.",
    "busy": "다른 학생이 사용 중이에요. 잠시만 기다려주세요.",
    "sorted": "분류가 완료되었습니다.",
    "over_limit": "일일 포인트 적립 횟수를 초과하셨습니다.",
}
status = bin_status(user["student_id"]) if VIA_BIN else None

with st.container(key="notice"):
    st.caption(BIN_MESSAGES[status])

if status in ("idle", "no_response", "sorted", "over_limit"):
    if st.button("수거 시작", type="primary", use_container_width=True):
        try:
            request_start(user["student_id"])
        except Exception:
            st.error("수거함에 신호를 보내지 못했어요. 잠시 후 다시 눌러주세요.")
        else:
            st.rerun()


@st.fragment(run_every=2)
def watch_bin(student_id, seen):
    # 2초마다 확인해서, 새 배출이 기록되거나 수거함 상태가 바뀌면 화면을 새로 그린다.
    now = (len(load_records(student_id)), bin_status(student_id))
    if now != seen:
        st.rerun()


if VIA_BIN:
    watch_bin(user["student_id"], (len(load_records(user["student_id"])), status))

results_tab, history_tab = st.tabs(MENU)

# 내 실적
with results_tab:
    mine = load_records(user["student_id"])

    st.subheader("내 실적")
    today = datetime.now(KST).date().isoformat()
    daily = []
    for name, (limit, reward) in RULES.items():
        used = len(mine[(mine["material"] == name) & (mine["points"] > 0)
                        & (mine["created_at"].astype(str).str.startswith(today))])
        daily.append((name, used, limit, reward))
    ui.daily_card("오늘 적립 횟수", "한국시간 자정 초기화", daily)
    st.caption("일일 한도 내의 배출만 수거량과 내역에 기록돼요.")
    st.caption("전체 기간 누적")
    ui.points_card("누적 환경 포인트", int(mine["points"].sum()))

    totals = []
    for name in MATERIAL:
        count = mine.loc[mine["material"] == name, "quantity"].sum()
        totals.append((name, int(count), "건" if name == "종이" else "개"))
    ui.material_cards(totals)

    if mine.empty:
        with st.container(key="empty_results"):
            ui.empty_state("아직 등록된 실적이 없습니다.")


# 배출 내역 및 학과 현황
with history_tab:
    mine = load_records(user["student_id"])

    with st.container(key="sec_history"):
        st.subheader("배출 내역")
    period = st.selectbox("조회 기간", ["전체", "최근 7일", "최근 30일"])

    filtered = mine
    if period != "전체" and not mine.empty:
        days = 7 if period == "최근 7일" else 30
        cutoff = datetime.now(KST) - timedelta(days=days)
        filtered = mine[pd.to_datetime(mine["created_at"], utc=True) >= cutoff]

    if filtered.empty:
        with st.container(key="empty_history"):
            ui.empty_state("선택한 기간에 배출 내역이 존재하지 않습니다.")
    else:
        history = pd.DataFrame({
            "배출 일시": filtered["created_at"].map(
                lambda value: datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
            ),
            "재질": filtered["material"],
            "수거량": [
                f"{int(quantity)}" + ("건" if material == "종이" else "개")
                for material, quantity in zip(filtered["material"], filtered["quantity"])
            ],
            "포인트": filtered["points"].map(lambda value: f"+{int(value):,} P"),
        })
        st.dataframe(history, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("학과별 현황")
    st.caption("전체 기간 누적 기준 · 위 기간 필터와 별개 · 동점은 공동 순위")

    records = load_records()
    if records.empty:
        with st.container(key="empty_ranking"):
            ui.empty_state("아직 등록된 학과별 실적이 없습니다.")
    else:
        ranking = (
            records.groupby("department", as_index=False)
            .agg(points=("points", "sum"), participants=("student_id", "nunique"))
            .sort_values(["points", "department"], ascending=[False, True])
        )
        ranking["rank"] = ranking["points"].rank(method="min", ascending=False).astype(int)
        ranking = ranking[["rank", "department", "points", "participants"]].rename(
            columns={
                "rank": "순위",
                "department": "학과",
                "points": "누적 포인트",
                "participants": "참여 인원",
            }
        )
        st.dataframe(ranking, hide_index=True, use_container_width=True)