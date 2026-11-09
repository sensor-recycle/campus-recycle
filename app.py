from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from data import KST, init_data, load_records, register_user, save_record


st.set_page_config(page_title="센서3팀_캠퍼스 스마트 분리수거함", layout="centered")
init_data()

SUBJECT = ["한국어문학부", "역사문화학과", "프랑스언어·문화학과", "중어중문학부", "독일언어·문화학과", "일본학과", "문헌정보학과", "문화관광외식학부", "문화관광학전공", "르꼬르동블루외식경영전공", "교육학부", "화학과", "생명시스템학부", "수학과", "통계학과", "체육교육과", "무용과", "화공생명공학부", "인공지능공학부", "지능형전자시스템학부", "신소재물리학부", "소프트웨어학부", "컴퓨터과학전공", "데이터사이언스전공", "기계시스템학부", "첨단공학부", "가족자원경영학과", "아동복지학부", "의류학과", "식품영양학과", "정치외교학과", "행정학과", "홍보광고학과", "소비자경제학과", "사회심리학과", "법학부", "경제학부", "경영학부", "피아노과", "관현악과", "성악과", "작곡과", "약학부", "시각·영상디자인과", "산업디자인과", "환경디자인과", "공예과", "회화과", "자유전공학부", "융합국제학부", "한류국제학부", "숙명-CQU 경영국제학부", "글로벌서비스학부", "글로벌협력전공", "앙트러프러너십전공", "영어영문학부", "영어영문학전공", "테슬(TESL)전공", "미디어학부", "미디어학전공"]
MATERIAL = ["플라스틱", "캔", "종이"]
MENU = ["분리수거 등록", "내 실적", "배출 내역·학과 현황"]


st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");

html, body, .stApp, .stApp * :not([class*="material"]):not([data-testid="stIconMaterial"]) {
    font-family: "Pretendard", sans-serif;
}
.block-container { max-width: 720px; padding-top: 2.5rem; padding-bottom: 3rem; }
h1, h2, h3 { color: #003087; font-weight: 700; letter-spacing: -0.02em; }
[data-testid="stForm"] { border: 1px solid #E3E6EE; border-radius: 12px; }
[data-testid="stMetric"] { background: #F5F6FA; border-radius: 12px; padding: 16px 18px; }
[data-testid="stMetricValue"] { color: #003087; font-weight: 700; }
.stButton > button, .stFormSubmitButton > button { border-radius: 10px; }
</style>
""", unsafe_allow_html=True)


# 헤더
st.title("캠퍼스 스마트 분리수거")
st.caption("나의 분리배출을 기록하고 포인트를 모으세요.")


# 로그인 화면
if "user" not in st.session_state:
    st.subheader("로그인")

    with st.form("login_form"):
        student_id = st.text_input(
            "학번", placeholder="학번을 입력해주세요", max_chars=20
        )
        department = st.selectbox("학과", SUBJECT)
        start = st.form_submit_button("로그인하기", type="primary", use_container_width=True)

    if start:
        student_id = student_id.strip()
        if not (student_id.isascii() and student_id.isdigit()):
            st.error("숫자만 입력해주세요.")
        elif not register_user(student_id, department):
            st.error("해당하는 학생을 찾을 수 없습니다.")
        else:
            st.session_state.user = {"student_id": student_id, "department": department}
            st.session_state.page = MENU[0]
            st.rerun()

    st.stop()


# 마이페이지
user = st.session_state.user

info_col, switch_col = st.columns([4, 1], vertical_alignment="center")
info_col.write(f"**{user['department']}** · {user['student_id']}")
if switch_col.button("사용자 변경", use_container_width=True):
    del st.session_state["user"]
    st.session_state.pop("notice", None)
    st.rerun()

page = st.radio("메뉴", MENU, horizontal=True, label_visibility="collapsed", key="page")
st.divider()

if "notice" in st.session_state:
    st.success(st.session_state.pop("notice"))


# 분리수거품 등록 화면
if page == "등록":
    st.subheader("분리배출 등록")
    # st.caption("(센서 연결 전, 수거 완료를 직접 입력하는 시연 화면이라는 뉘앙스의 멘트)")

    material = st.selectbox("재질", MATERIAL)

    with st.form(f"register_{material}", clear_on_submit=True):
        if material == "종이":
            weight_g = st.number_input(
                "건조 종이 무게 (g)", min_value=0.0, max_value=5000.0, value=10.0, step=1.0
            )
            quantity = 0
        else:
            quantity = st.number_input("수거 개수", min_value=1, max_value=100, value=1, step=1)
            weight_g = 0.0

        confirmed = st.checkbox("정상적으로 수거가 완료되었습니다.")
        submitted = st.form_submit_button("수거 등록", type="primary", use_container_width=True)

    st.caption("적립 기준(임시): 플라스틱·캔 1개당 10P / 종이 누적 10g당 1P")

    if submitted:
        if not confirmed:
            st.warning("재활용품이 정상적으로 수거되었는지 확인해 주세요.")
        else:
            points = save_record(
                student_id=user["student_id"],
                material=material,
                quantity=quantity,
                weight_g=weight_g,
            )
            st.session_state.notice = f"등록이 완료되었습니다. +{points:,}P"
            st.rerun()


# 내 실적 표시 화면
elif page == "내 실적":
    mine = load_records(user["student_id"])

    st.subheader("내 실적")
    st.caption("전체 기간 누적 · 시연 데이터")

    st.metric("누적 환경 포인트", f"{int(mine['points'].sum()):,} P")

    col1, col2, col3 = st.columns(3)
    for col, name in zip((col1, col2), ("플라스틱", "캔")):
        count = mine.loc[mine["material"] == name, "quantity"].sum()
        col.metric(name, f"{int(count):,}개")
    paper = mine.loc[mine["material"] == "종이", "weight_g"].sum()
    col3.metric("종이", f"{paper:,.1f}g")

    if mine.empty:
        st.info("아직 등록된 실적이 없습니다.")


# 배출 내역 · 학과 현황
else:
    mine = load_records(user["student_id"])

    st.subheader("배출 내역")
    period = st.selectbox("조회 기간", ["전체", "최근 7일", "최근 30일"])

    filtered = mine
    if period != "전체" and not mine.empty:
        days = 7 if period == "최근 7일" else 30
        cutoff = datetime.now(KST) - timedelta(days=days)
        filtered = mine[pd.to_datetime(mine["created_at"], utc=True) >= cutoff]

    if filtered.empty:
        st.info("선택한 기간에 배출 내역이 존재하지 않습니다.")
    else:
        history = pd.DataFrame({
            "배출 일시": filtered["created_at"].map(
                lambda v: datetime.fromisoformat(v).strftime("%Y-%m-%d %H:%M")
            ),
            "재질": filtered["material"],
            "수거량": [
                f"{w:,.1f} g" if m == "종이" else f"{int(q)} 개"
                for m, w, q in zip(filtered["material"], filtered["weight_g"], filtered["quantity"])
            ],
            "포인트": filtered["points"].map(lambda v: f"+{int(v):,} P"),
        })
        st.dataframe(history, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("학과별 현황")
    st.caption("전체 기간 누적 기준 · 위 기간 필터와 별개 · 동점은 공동 순위")

    records = load_records()
    if records.empty:
        st.info("아직 등록된 학과별 실적이 없습니다.")
    else:
        ranking = (
            records.groupby("department", as_index=False)
            .agg(points=("points", "sum"), participants=("student_id", "nunique"))
            .sort_values(["points", "department"], ascending=[False, True])
        )
        ranking["rank"] = ranking["points"].rank(method="min", ascending=False).astype(int)
        ranking = ranking[["rank", "department", "points", "participants"]].rename(columns={
            "rank": "순위", "department": "학과",
            "points": "누적 포인트", "participants": "참여 인원",
        })
        st.dataframe(ranking, hide_index=True, use_container_width=True)
