from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from data import KST, RULES, init_data, load_records, register_user


st.set_page_config(
    page_title="센서 3팀 - 캠퍼스 스마트 분리수거함",
    layout="centered",
)
init_data()

SUBJECT = ["가족자원경영학과", "경영학부", "경제학부", "공예과", "관현악과", "교육학부", "글로벌서비스학부", "글로벌협력전공", "기계시스템학부", "데이터사이언스전공", "독일언어·문화학과", "르꼬르동블루외식경영전공", "무용과", "문헌정보학과", "문화관광외식학부", "문화관광학전공", "미디어학부", "미디어학전공", "법학부", "사회심리학과", "산업디자인과", "생명시스템학부", "성악과", "소비자경제학과", "소프트웨어학부", "수학과", "숙명-CQU 경영국제학부", "시각·영상디자인과", "식품영양학과", "신소재물리학부", "아동복지학부", "앙트러프러너십전공", "약학부", "역사문화학과", "영어영문학부", "영어영문학전공", "융합국제학부", "의류학과", "인공지능공학부", "일본학과", "자유전공학부", "작곡과", "정치외교학과", "중어중문학부", "지능형전자시스템학부", "첨단공학부", "체육교육과", "컴퓨터과학전공", "테슬(TESL)전공", "통계학과", "프랑스언어·문화학과", "피아노과", "한국어문학부", "한류국제학부", "행정학과", "홍보광고학과", "화공생명공학부", "화학과", "환경디자인과", "회화과"]
MATERIAL = ["플라스틱", "캔", "종이"]
MENU = ["내 실적", "배출 내역·학과 현황"]


st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");

html, body, .stApp, .stApp * :not([class*="material"]):not([data-testid="stIconMaterial"]) {
    font-family: "Pretendard", sans-serif;
}

.block-container {
    max-width: 480px;
    padding: 2rem 1.25rem 3rem;
}

h1, h2, h3 {
    color: #166534;
    font-weight: 700;
    letter-spacing: -0.02em;
}

[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
    color: #5F6B63;
}

[data-testid="stForm"] {
    border: 1px solid #DDE5DF;
    border-radius: 12px;
}

[data-testid="stMetric"] {
    background: #F5F5F5;
    border-radius: 12px;
    padding: 16px 18px;
}

[data-testid="stMetricLabel"] p {
    color: #111111;
    font-size: 1rem;
    font-weight: 500;
}

[data-testid="stMetricValue"] {
    padding-top: 8px;
    color: #166534;
    font-weight: 700;
}

:is(.st-key-empty_results, .st-key-empty_history, .st-key-empty_ranking)
[data-testid="stAlertContainer"] {
    background-color: #EDF2E8;
}

:is(.st-key-empty_results, .st-key-empty_history, .st-key-empty_ranking)
[data-testid="stAlert"] p {
    color: #166534;
}

.stButton > button, .stFormSubmitButton > button {
    border-radius: 10px;
}

.st-key-page_header { padding-bottom: 1.5rem; }
.st-key-page_header h1 { padding-bottom: 0.75rem; }
[data-testid="stTabPanel"] { padding-top: 1rem; }

.st-key-userbar [data-testid="stHorizontalBlock"] { justify-content: space-between; align-items: center; }
.st-key-userbar [data-testid="stColumn"] { flex: 0 0 auto !important; width: auto !important; }
.st-key-userbar .stButton > button {
    min-height: 0; width: auto; padding: 0.3rem 0.75rem;
    font-size: 0.8rem; font-weight: 500; border-radius: 999px; white-space: nowrap;
}

[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem; }
[data-testid="stColumn"] { min-width: 0 !important; }
[data-testid="stMetric"] { padding: 14px 12px; }
[data-testid="stMetricLabel"] p { font-size: 0.9rem; }
[data-testid="stMetricValue"] { font-size: 1.35rem; }

.stButton > button, .stFormSubmitButton > button { min-height: 48px; }
.st-key-userbar .stButton > button { min-height: 0; }

@media (max-width: 480px) {
    .block-container { padding-inline: 1rem; }
}
</style>
""", unsafe_allow_html=True)


with st.container(key="page_header"):
    st.title("캠퍼스 스마트 분리수거")
    st.caption("개인 배출 내역과 학과별 실적을 조회하세요.")


# 로그인 화면
if "user" not in st.session_state:
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
            st.rerun()

    st.stop()


# 사용자 정보 및 메뉴
user = st.session_state.user

with st.container(key="userbar"):
    info_col, switch_col = st.columns([4, 1], vertical_alignment="center")
    info_col.write(f"**{user['department']}** · {user['student_id']}")
    if switch_col.button("사용자 변경"):
        del st.session_state["user"]
        st.rerun()

st.caption("센서 연결 전에는 새 배출 기록이 표시되지 않아요.") # 수정할지 말지 고민..
results_tab, history_tab = st.tabs(MENU)

# 내 실적
with results_tab:
    mine = load_records(user["student_id"])

    st.subheader("내 실적")
    today = datetime.now(KST).date().isoformat()
    st.caption("오늘 적립 횟수 · 한국시간 자정 초기화")
    for name, (limit, reward) in RULES.items():
        used = len(mine[(mine["material"] == name) & (mine["points"] > 0)
                        & (mine["created_at"].astype(str).str.startswith(today))])
        st.write(f"{name}  {used}/{limit}회 · 1회 {reward}P")
    st.caption("일일 한도 내의 배출만 수거량과 내역에 기록돼요.")
    st.caption("전체 기간 누적")
    st.metric("누적 환경 포인트", f"{int(mine['points'].sum()):,} P")

    col1, col2, col3 = st.columns(3)
    for col, name in zip((col1, col2, col3), MATERIAL):
        count = mine.loc[mine["material"] == name, "quantity"].sum()
        col.metric(name, f"{int(count):,}" + ("건" if name == "종이" else "개"))

    if mine.empty:
        with st.container(key="empty_results"):
            st.info("아직 등록된 실적이 없습니다.")


# 배출 내역 및 학과 현황
with history_tab:
    mine = load_records(user["student_id"])

    st.subheader("배출 내역")
    period = st.selectbox("조회 기간", ["전체", "최근 7일", "최근 30일"])

    filtered = mine
    if period != "전체" and not mine.empty:
        days = 7 if period == "최근 7일" else 30
        cutoff = datetime.now(KST) - timedelta(days=days)
        filtered = mine[pd.to_datetime(mine["created_at"], utc=True) >= cutoff]

    if filtered.empty:
        with st.container(key="empty_history"):
            st.info("선택한 기간에 배출 내역이 존재하지 않습니다.")
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
            st.info("아직 등록된 학과별 실적이 없습니다.")
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