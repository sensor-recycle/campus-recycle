"""화면 디자인 전용 모듈 (숙명 로열블루 + 눈송이 테마).

데이터/적립 로직은 건드리지 않고, web.py가 계산한 값을 예쁘게 그려주는 역할만 한다.
"""

import base64
import struct
from html import escape
from pathlib import Path
from urllib.parse import quote

import streamlit as st

# 색상은 숙명여자대학교 UI 전용 색상(sookmyung.ac.kr/kr/intro/ui.do)을 쓴다.
#   Royal Blue #003087 (Pantone 287C) · Light Blue #0072CE (285C) · Royal Yellow #FFB500 (7549C)
# 실제 값은 아래 CSS의 :root 변수(--sm-blue 등)에 있다.

# 눈송이 픽셀 캐릭터 (assets 폴더). PNG는 도트 1칸 = 1픽셀로 저장돼 있어서
# 화면에서는 "도트 1칸 = N px" 정수배로만 키워 또렷하게 보여준다.
ASSETS = Path(__file__).with_name("assets")
ANI_DOTS = 36  # noonsong_ani.gif (270px) 는 도트 1칸이 7.5px → 가로 36칸

# 재질 아이콘: 세 가지 모두 같은 단순한 선 스타일 (24x24)
MATERIAL_ICON = {
    "플라스틱": (
        '<rect x="10" y="2.5" width="4" height="2.2" rx=".6"/>'
        '<path d="M10.2 4.7v1.6c0 .9-2.2 1.9-2.2 4.2V20A1.5 1.5 0 0 0 9.5 21.5h5'
        'A1.5 1.5 0 0 0 16 20v-9.5c0-2.3-2.2-3.3-2.2-4.2V4.7"/>'
        '<path d="M8 12.5h8M8 17h8"/>'
    ),
    "캔": (
        '<path d="M7 6.5v11c0 1.4 2.2 2.5 5 2.5s5-1.1 5-2.5v-11"/>'
        '<ellipse cx="12" cy="6.5" rx="5" ry="2.5"/>'
        '<ellipse cx="12" cy="6.6" rx="1.7" ry=".7"/>'
        '<path d="M7 14.5c0 1.4 2.2 2.5 5 2.5s5-1.1 5-2.5"/>'
    ),
    "종이": (
        '<path d="M6.5 3H14l4 4v13.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5v-17'
        'A.5.5 0 0 1 6.5 3Z"/>'
        '<path d="M14 3v4h4M9 11.5h6M9 14.5h6M9 17.5h3.5"/>'
    ),
}
MATERIAL_TONE = {"플라스틱": "sky", "캔": "yellow", "종이": "mint"}


# ── 캐릭터 ────────────────────────────────────────────────────────────────
def _asset_uri(filename):
    path = ASSETS / filename
    if not path.exists():
        return ""
    mime = "image/gif" if path.suffix == ".gif" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _sprite(name):
    """(data URI, 가로 도트 수, 세로 도트 수) — name: read/guide/magic/cheer/notice/smile"""
    path = ASSETS / f"noonsong_{name}.png"
    if not path.exists():
        return "", 0, 0
    width, height = struct.unpack(">II", path.read_bytes()[16:24])  # PNG IHDR
    return _asset_uri(path.name), width, height


def _sprite_img(name, dot=2):
    uri, width, height = _sprite(name)
    if not uri:
        return ""
    return (f'<img class="sm-px" src="{uri}" alt="" '
            f'style="width:{width * dot}px;height:{height * dot}px">')


def _sprite_css(selector, name, dot=2):
    """CSS 가상 요소에 캐릭터를 배경으로 깔 때 쓰는 규칙"""
    uri, width, height = _sprite(name)
    if not uri:
        return f"{selector} {{ display: none; }}\n"
    return (f'{selector} {{ width: {width * dot}px; height: {height * dot}px; '
            f'background: url("{uri}") center / 100% 100% no-repeat; '
            'image-rendering: pixelated; }\n')


def material_icon(name):
    """재질 아이콘 <img> 태그. 모르는 재질은 종이 아이콘으로 대신한다."""
    shapes = MATERIAL_ICON.get(name, MATERIAL_ICON["종이"])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#fff" '
        'stroke="#5B6B8C" stroke-width="1.5" stroke-linecap="round" '
        f'stroke-linejoin="round">{shapes}</svg>'
    )
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="data:image/svg+xml;base64,{data}" alt="">'


# ── 눈꽃 아이콘 (CSS mask / 배경용) ───────────────────────────────────────
def _flake_url(color="black"):
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
        f"stroke='{color}' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
        "<path d='M12 2v20M3.34 7l17.32 10M3.34 17 20.66 7'/>"
        "<path d='M9.5 3.8 12 6l2.5-2.2M9.5 20.2 12 18l2.5 2.2"
        "M3.2 10.4l3.2-.9-.8-3.2M20.8 13.6l-3.2.9.8 3.2"
        "M3.2 13.6l3.2.9-.8 3.2M20.8 10.4l-3.2-.9.8-3.2'/></svg>"
    )
    return f'url("data:image/svg+xml,{quote(svg)}")'


CSS = """
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");
@import url("https://fonts.googleapis.com/css2?family=Jua&display=swap");

:root {
    --sm-blue: #003087;
    --sm-blue-deep: #00256B;
    --sm-sky: #0072CE;
    --sm-yellow: #FFB500;
    --sm-ink: #14213D;
    --sm-muted: #5B6680;
    --sm-line: #D5E1F5;
    --sm-ice: #EAF2FE;
    --sm-bg: #F3F7FE;
    --sm-card: #FFFFFF;
    --sm-shadow: 0 6px 20px rgba(0, 48, 135, 0.08);
    --sm-flake: __FLAKE__;
    --sm-display: "Jua", "Pretendard", sans-serif;
}

html, body, .stApp, .stApp * :not([class*="material"]):not([data-testid="stIconMaterial"]) {
    font-family: "Pretendard", sans-serif;
}

/* ── 바탕 ─────────────────────────────────────────── */
.stApp {
    color: var(--sm-ink);
    background: linear-gradient(180deg, #DDEAFE 0, #F3F7FE 360px) var(--sm-bg);
}
/* 넓은 화면에서만 양옆 여백에 눈꽃을 뿌린다 */
@media (min-width: 760px) {
    .stApp {
        background:
            __FLAKE_BG__ calc(50% - 330px) 120px / 44px no-repeat,
            __FLAKE_BG__ calc(50% + 350px) 250px / 30px no-repeat,
            __FLAKE_BG__ calc(50% - 400px) 460px / 26px no-repeat,
            __FLAKE_BG__ calc(50% + 300px) 610px / 52px no-repeat,
            __FLAKE_BG__ calc(50% - 300px) 780px / 34px no-repeat,
            linear-gradient(180deg, #DDEAFE 0, #F3F7FE 360px) var(--sm-bg);
    }
}
[data-testid="stHeader"] { background: transparent; }

.block-container {
    max-width: 480px;
    padding: 2rem 1.25rem 3rem;
}
[data-testid="stHtml"] { line-height: 1.5; word-break: keep-all; }

/* ── 상단 히어로 ──────────────────────────────────── */
.sm-hero {
    position: relative;
    overflow: hidden;
    display: flex;
    align-items: center;
    min-height: 176px;
    padding: 20px 20px 22px;
    border-radius: 26px;
    color: #fff;
    background:
        radial-gradient(120px 120px at 88% 112%, rgba(255, 255, 255, 0.22), transparent 70%),
        linear-gradient(135deg, var(--sm-blue-deep) 0%, var(--sm-blue) 45%, var(--sm-sky) 130%);
    box-shadow: 0 14px 30px rgba(0, 48, 135, 0.24);
}
.sm-hero-copy { position: relative; z-index: 1; flex: 1; min-width: 0; padding-right: 150px; }
.sm-title {
    margin: 0 0 8px;
    font-family: var(--sm-display) !important;
    font-size: 1.78rem; line-height: 1.2; letter-spacing: -0.01em;
    text-wrap: balance;
}
.sm-sub { font-size: 0.86rem; color: rgba(255, 255, 255, 0.84); text-wrap: balance; }
.sm-px { image-rendering: pixelated; display: block; max-width: none; }
.sm-hero { --dot: 4px; }
.sm-hero-mascot {
    position: absolute; right: 18px; bottom: 14px;
    width: calc(var(--dots) * var(--dot)); height: auto;
    image-rendering: pixelated;
    /* 파란 배경에서도 또렷하게: 도트 1칸 두께의 흰 테두리 */
    filter: drop-shadow(var(--dot) 0 0 #fff) drop-shadow(calc(var(--dot) * -1) 0 0 #fff)
            drop-shadow(0 var(--dot) 0 #fff) drop-shadow(0 calc(var(--dot) * -1) 0 #fff);
}
.sm-hero-mascot.hop { animation: sm-hop 0.9s steps(1, end) infinite; }
.sm-snow {
    position: absolute; top: -18px;
    width: 14px; height: 14px;
    background: #fff; opacity: 0;
    -webkit-mask: var(--sm-flake) center / contain no-repeat; mask: var(--sm-flake) center / contain no-repeat;
    animation: sm-fall 7s linear infinite;
}
.sm-snow.s1 { left: 8%;  animation-delay: 0s; }
.sm-snow.s2 { left: 27%; animation-delay: 2.6s; width: 10px; height: 10px; }
.sm-snow.s3 { left: 46%; animation-delay: 4.8s; width: 18px; height: 18px; }
.sm-snow.s4 { left: 63%; animation-delay: 1.4s; width: 11px; height: 11px; }
.sm-snow.s5 { left: 92%; animation-delay: 3.7s; }

@keyframes sm-fall {
    0%   { transform: translateY(0) rotate(0deg); opacity: 0; }
    12%  { opacity: 0.4; }
    80%  { opacity: 0.3; }
    100% { transform: translateY(190px) rotate(160deg); opacity: 0; }
}
@keyframes sm-hop {
    0%, 100% { transform: translateY(0); }
    50%      { transform: translateY(calc(var(--dot) * -1)); }
}
@media (prefers-reduced-motion: reduce) {
    .sm-hero-mascot.hop { animation: none; }
    .sm-snow { animation: none; opacity: 0.35; top: 14px; }
}

.st-key-page_header { padding-bottom: 0.5rem; }

/* ── 제목/캡션 ────────────────────────────────────── */
.stApp h1, .stApp h2, .stApp h3 {
    color: var(--sm-blue) !important;
    font-family: var(--sm-display) !important;
    font-weight: 400;
    letter-spacing: -0.01em;
}
h3 { display: flex; align-items: center; gap: 8px; }
h3::before {
    content: "";
    flex: none;
    width: 20px; height: 20px;
    background: var(--sm-sky);
    -webkit-mask: var(--sm-flake) center / contain no-repeat; mask: var(--sm-flake) center / contain no-repeat;
}
h3 * { font-family: inherit !important; }

.stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stCaptionContainer"] p {
    color: var(--sm-muted) !important;
    opacity: 1;
}

/* ── 로그인 폼 · 입력 ─────────────────────────────── */
[data-testid="stForm"] {
    padding: 20px 18px 18px;
    border: 1px solid var(--sm-line);
    border-radius: 22px;
    background: var(--sm-card);
    box-shadow: var(--sm-shadow);
}
[data-testid="stWidgetLabel"] p { color: var(--sm-ink); font-weight: 600; }

[data-testid="stTextInputRootElement"],
.stSelectbox [data-baseweb="select"] > div,
.stSelectbox .react-aria-ComboBox > [role="group"] {
    border: 1.5px solid var(--sm-line) !important;
    border-radius: 14px !important;
    background: #F8FBFF !important;
    transition: border-color 0.15s, box-shadow 0.15s;
}
[data-testid="stTextInputRootElement"] > div,
[data-testid="stTextInputRootElement"] input { background: transparent !important; }
[data-testid="stTextInputRootElement"]:focus-within,
.stSelectbox [data-baseweb="select"] > div:focus-within,
.stSelectbox .react-aria-ComboBox > [role="group"]:focus-within {
    border-color: var(--sm-sky) !important;
    box-shadow: 0 0 0 3px rgba(0, 114, 206, 0.16);
}
.stApp input::placeholder { color: #9AA6BD; }

/* ── 버튼 ─────────────────────────────────────────── */
.stButton > button, .stFormSubmitButton > button {
    min-height: 48px;
    border-radius: 14px;
    border: 1.5px solid var(--sm-line);
    background: #fff;
    color: var(--sm-blue);
    font-weight: 600;
    transition: transform 0.12s, box-shadow 0.12s, background 0.12s, border-color 0.12s;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
    border-color: var(--sm-sky);
    color: var(--sm-blue);
    background: var(--sm-ice);
}
.stButton > button:focus-visible, .stFormSubmitButton > button:focus-visible {
    outline: 3px solid rgba(0, 114, 206, 0.35);
    outline-offset: 2px;
}
.stButton > button[kind^="primary"], .stFormSubmitButton > button[kind^="primary"] {
    border: 0;
    color: #fff;
    font-size: 1.02rem; font-weight: 700;
    background: linear-gradient(135deg, var(--sm-blue) 0%, #0A56C2 100%);
    box-shadow: 0 8px 18px rgba(0, 48, 135, 0.28);
}
.stButton > button[kind^="primary"] p, .stFormSubmitButton > button[kind^="primary"] p { font-weight: 700; }
.stButton > button[kind^="primary"]:hover, .stFormSubmitButton > button[kind^="primary"]:hover {
    color: #fff;
    background: linear-gradient(135deg, #003A9E 0%, #0C63DA 100%);
    transform: translateY(-1px);
    box-shadow: 0 10px 22px rgba(0, 48, 135, 0.34);
}
.stButton > button[kind^="primary"]:active, .stFormSubmitButton > button[kind^="primary"]:active {
    transform: translateY(0);
    box-shadow: 0 4px 10px rgba(0, 48, 135, 0.3);
}

/* ── 사용자 바 ────────────────────────────────────── */
.st-key-userbar {
    padding: 8px 10px 8px 8px;
    border: 1px solid var(--sm-line);
    border-radius: 999px;
    background: var(--sm-card);
    box-shadow: var(--sm-shadow);
}
.st-key-userbar [data-testid="stHorizontalBlock"] { justify-content: space-between; align-items: center; }
.st-key-userbar [data-testid="stColumn"] { flex: 0 1 auto !important; width: auto !important; }
.st-key-userbar [data-testid="stColumn"]:last-child { flex: 0 0 auto !important; }
.st-key-userbar .stButton > button {
    min-height: 0; width: auto; padding: 0.34rem 0.8rem;
    font-size: 0.8rem; font-weight: 600; border-radius: 999px; white-space: nowrap;
    background: var(--sm-ice); border-color: transparent;
}
.st-key-userbar .stButton > button:hover { background: #DCE9FD; border-color: transparent; }
.sm-user { display: flex; align-items: center; gap: 10px; min-width: 0; }
.sm-avatar {
    position: relative; overflow: hidden;
    flex: none;
    width: 40px; height: 40px;
    border-radius: 50%;
    background: var(--sm-ice);
    box-shadow: inset 0 0 0 1.5px var(--sm-line);
}
.sm-avatar img { position: absolute; left: -3px; top: -1px; }
.sm-user-text { min-width: 0; line-height: 1.25; }
.sm-user-dept { font-weight: 700; font-size: 0.95rem; overflow-wrap: anywhere; }
.sm-user-id { font-size: 0.78rem; color: var(--sm-muted); font-variant-numeric: tabular-nums; }

/* ── 안내 문구 ────────────────────────────────────── */
.st-key-notice {
    position: relative;
    justify-content: center;
    min-height: 60px;
    padding-right: 62px;
}
.st-key-notice::after { content: ""; position: absolute; right: 0; bottom: 0; }
.st-key-notice [data-testid="stCaptionContainer"] {
    position: relative;
    margin: 0;
    padding: 9px 14px;
    border: 1px solid var(--sm-line);
    border-radius: 14px;
    background: #fff;
    box-shadow: var(--sm-shadow);
}
.st-key-notice [data-testid="stCaptionContainer"]::after {  /* 말풍선 꼬리 */
    content: "";
    position: absolute; right: -6px; top: 50%;
    width: 10px; height: 10px;
    border: 1px solid var(--sm-line); border-left: 0; border-bottom: 0;
    background: #fff;
    transform: translateY(-50%) rotate(45deg);
}
.stApp .st-key-notice [data-testid="stCaptionContainer"] p {
    margin: 0;
    color: var(--sm-ink) !important; font-weight: 500;
    word-break: keep-all; text-wrap: balance;
}

/* 제목 옆에 서 있는 캐릭터 */
:is(.st-key-sec_login, .st-key-sec_history) { position: relative; }
:is(.st-key-sec_login, .st-key-sec_history)::after {
    content: ""; position: absolute; right: 18px; bottom: -16px; z-index: 2;
}
.st-key-sec_history::after { bottom: -44px; }  /* '조회 기간' 선택 상자 위에 서도록 */

/* ── 탭 ───────────────────────────────────────────── */
[data-testid="stTabs"] [role="tablist"] {
    gap: 4px;
    padding: 4px;
    border-radius: 999px;
    background: #DFEAFB;
}
[data-testid="stTabs"] [role="tab"] {
    flex: 1;
    justify-content: center;
    height: 42px;
    padding: 0 10px;
    border-radius: 999px;
    color: var(--sm-muted);
    transition: background 0.15s, color 0.15s;
}
[data-testid="stTabs"] [role="tab"] p { font-size: 0.95rem; font-weight: 600; }
[data-testid="stTabs"] [role="tab"]:hover { color: var(--sm-blue); }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: var(--sm-blue);
    background: #fff;
    box-shadow: 0 2px 8px rgba(0, 48, 135, 0.14);
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p { font-weight: 700; }
[data-testid="stTabs"] .react-aria-SelectionIndicator,
[data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stTabs"] [data-baseweb="tab-border"] { display: none !important; }
[data-testid="stTabPanel"] { padding-top: 1rem; }

/* ── 카드 공통 ────────────────────────────────────── */
.sm-card {
    padding: 16px;
    border: 1px solid var(--sm-line);
    border-radius: 22px;
    background: var(--sm-card);
    box-shadow: var(--sm-shadow);
}
.sm-card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.sm-card-title { font-weight: 700; font-size: 1rem; }
.sm-card-note { font-size: 0.76rem; color: var(--sm-muted); white-space: nowrap; }

/* 오늘 적립 횟수 */
.sm-row { display: flex; align-items: center; gap: 12px; padding: 11px 0; }
.sm-row + .sm-row { border-top: 1px dashed var(--sm-line); }
.sm-row:last-child { padding-bottom: 2px; }
.sm-ico {
    flex: none;
    display: grid; place-items: center;
    width: 44px; height: 44px;
    border-radius: 15px;
}
.sm-ico img { display: block; width: 28px; height: 28px; }
.sm-ico.sky { background: #E3F0FF; }
.sm-ico.yellow { background: #FFF2CC; }
.sm-ico.mint { background: #E2F6EE; }
.sm-row-main { flex: 1; min-width: 0; }
.sm-row-name { display: flex; align-items: center; gap: 7px; font-weight: 700; }
.sm-chip {
    padding: 2px 8px;
    border-radius: 999px;
    background: #FFF2CC; color: #7A5A00;
    font-size: 0.72rem; font-weight: 700; white-space: nowrap;
}
.sm-pips { display: flex; gap: 4px; margin-top: 6px; }
.sm-pips i {
    width: 19px; height: 19px;
    background: #CBD8EE;
    -webkit-mask: var(--sm-flake) center / contain no-repeat; mask: var(--sm-flake) center / contain no-repeat;
}
.sm-pips i.on { background: var(--sm-sky); }
.sm-row.full .sm-pips i.on { background: var(--sm-blue); }
.sm-row-count { flex: none; font-size: 0.86rem; color: var(--sm-muted); font-variant-numeric: tabular-nums; }
.sm-row-count b { font-size: 1.3rem; font-weight: 800; color: var(--sm-blue); }

/* 누적 포인트 */
.sm-points {
    position: relative; overflow: hidden;
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 18px 20px;
    border-radius: 22px;
    color: #fff;
    background: linear-gradient(135deg, var(--sm-blue-deep) 0%, var(--sm-blue) 50%, #0A5FCF 120%);
    box-shadow: 0 12px 26px rgba(0, 48, 135, 0.24);
}
.sm-points::after {
    content: "";
    position: absolute; right: -26px; top: -30px;
    width: 130px; height: 130px;
    background: #fff; opacity: 0.08;
    -webkit-mask: var(--sm-flake) center / contain no-repeat; mask: var(--sm-flake) center / contain no-repeat;
}
.sm-points-label { font-size: 0.9rem; font-weight: 600; color: rgba(255, 255, 255, 0.86); }
.sm-points-value {
    margin-top: 2px;
    font-size: 2.3rem; font-weight: 800; line-height: 1.15; letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
}
.sm-points-value span { margin-left: 5px; font-size: 1.25rem; color: var(--sm-yellow); }
.sm-disc {
    position: relative; z-index: 1; flex: none;
    display: grid; place-items: center;
    width: 92px; height: 92px;
    border-radius: 50%;
    background: #fff;
    box-shadow: 0 0 0 5px rgba(255, 255, 255, 0.22), 0 6px 14px rgba(0, 20, 70, 0.3);
}

/* 재질별 누적 */
.sm-mats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 10px; }
.sm-mat {
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    padding: 14px 6px 13px;
    border: 1px solid var(--sm-line);
    border-radius: 20px;
    background: var(--sm-card);
    box-shadow: var(--sm-shadow);
    text-align: center;
}
.sm-mat-name { font-size: 0.86rem; font-weight: 600; color: var(--sm-muted); }
.sm-mat-value { font-size: 1.4rem; font-weight: 800; color: var(--sm-blue); line-height: 1.1; font-variant-numeric: tabular-nums; }
.sm-mat-value small { margin-left: 2px; font-size: 0.8rem; font-weight: 600; color: var(--sm-muted); }

/* 빈 화면 */
.sm-empty {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 18px;
    border: 1.5px dashed #B9CDEE;
    border-radius: 22px;
    background: var(--sm-ice);
}
.sm-empty img { flex: none; }
.sm-empty p { margin: 0; color: var(--sm-blue); font-weight: 600; font-size: 0.95rem; }

/* ── 표 · 구분선 ──────────────────────────────────── */
[data-testid="stDataFrame"] {
    border: 1px solid var(--sm-line);
    border-radius: 18px;
    overflow: hidden;
    background: #fff;
    box-shadow: var(--sm-shadow);
}
[data-testid="stMarkdownContainer"] hr {
    border: 0;
    border-top: 2px dashed #C6D6F1;
    margin: 1.25rem 0 0.75rem;
}

/* ── 알림 ─────────────────────────────────────────── */
[data-testid="stAlertContainer"] { border-radius: 14px; }

/* ── 공통 레이아웃 ────────────────────────────────── */
[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem; }
[data-testid="stColumn"] { min-width: 0 !important; }

@media (max-width: 480px) {
    .block-container { padding-inline: 1rem; }
    .sm-title { font-size: 1.62rem; }
    .sm-hero { --dot: 3px; min-height: 150px; }
    .sm-hero-mascot { right: 14px; bottom: 12px; }
    .sm-hero-copy { padding-right: 112px; }
}
</style>
""".replace("__FLAKE_BG__", _flake_url("#C3D6F5")).replace("__FLAKE__", _flake_url())

CSS = CSS.replace("</style>", (
    _sprite_css(".st-key-notice::after", "notice")
    + _sprite_css(".st-key-sec_login::after", "guide")
    + _sprite_css(".st-key-sec_history::after", "read")
    + "</style>"
))


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


# ── 화면 조각 ─────────────────────────────────────────────────────────────
def hero(title, subtitle, mascot="ani"):
    """mascot: "ani"(움직이는 GIF) 또는 캐릭터 이름(cheer 등)"""
    if mascot == "ani":
        uri, dots, cls = _asset_uri("noonsong_ani.gif"), ANI_DOTS, ""
    else:
        uri, dots, _ = _sprite(mascot)
        cls = " hop"
    image = (
        f'<img class="sm-hero-mascot{cls}" style="--dots:{dots}" src="{uri}" '
        'alt="눈송이 캐릭터">' if uri else ""
    )
    snow = "".join(f'<i class="sm-snow s{n}"></i>' for n in range(1, 6))
    st.html(
        f'<div class="sm-hero">{snow}'
        '<div class="sm-hero-copy">'
        f'<div class="sm-title" role="heading" aria-level="1">{escape(title)}</div>'
        f'<div class="sm-sub">{escape(subtitle)}</div>'
        f'</div>{image}</div>'
    )


def user_chip(department, student_id):
    st.html(
        '<div class="sm-user">'
        f'<div class="sm-avatar" aria-hidden="true">{_sprite_img("smile")}</div>'
        '<div class="sm-user-text">'
        f'<div class="sm-user-dept">{escape(department)}</div>'
        f'<div class="sm-user-id">{escape(student_id)}</div>'
        '</div></div>'
    )


def daily_card(title, note, rows):
    """rows: (재질, 오늘 적립 횟수, 하루 한도, 1회 포인트) 목록"""
    body = ""
    for name, used, limit, reward in rows:
        pips = "".join(
            f'<i class="{"on" if n < used else ""}"></i>' for n in range(limit)
        )
        body += (
            f'<div class="sm-row{" full" if used >= limit else ""}">'
            f'<span class="sm-ico {MATERIAL_TONE.get(name, "sky")}" aria-hidden="true">'
            f'{material_icon(name)}</span>'
            '<div class="sm-row-main">'
            f'<div class="sm-row-name">{escape(name)}'
            f'<span class="sm-chip">1회 {reward}P</span></div>'
            f'<div class="sm-pips" aria-hidden="true">{pips}</div>'
            '</div>'
            f'<div class="sm-row-count"><b>{used}</b>/{limit}회</div>'
            '</div>'
        )
    st.html(
        '<div class="sm-card">'
        f'<div class="sm-card-head"><span class="sm-card-title">{escape(title)}</span>'
        f'<span class="sm-card-note">{escape(note)}</span></div>'
        f'{body}</div>'
    )


def points_card(label, points):
    st.html(
        '<div class="sm-points"><div>'
        f'<div class="sm-points-label">{escape(label)}</div>'
        f'<div class="sm-points-value">{points:,}<span>P</span></div>'
        f'</div><div class="sm-disc" aria-hidden="true">{_sprite_img("magic")}</div></div>'
    )


def material_cards(items):
    """items: (재질, 수량, 단위) 목록"""
    cards = "".join(
        '<div class="sm-mat">'
        f'<span class="sm-ico {MATERIAL_TONE.get(name, "sky")}" aria-hidden="true">'
        f'{material_icon(name)}</span>'
        f'<span class="sm-mat-name">{escape(name)}</span>'
        f'<span class="sm-mat-value">{count:,}<small>{escape(unit)}</small></span>'
        '</div>'
        for name, count, unit in items
    )
    st.html(f'<div class="sm-mats">{cards}</div>')


def empty_state(message):
    st.html(
        '<div class="sm-empty">'
        f'{_sprite_img("guide")}'
        f'<p>{escape(message)}</p></div>'
    )
