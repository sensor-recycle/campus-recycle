-- 캠퍼스 스마트 분리수거: 웹(Streamlit Cloud)과 라즈베리 파이가 함께 쓰는 공용 DB.
-- Supabase 대시보드 > SQL Editor 에 전체를 붙여 넣고 한 번만 실행한다.
-- data.py 는 SUPABASE_URL / SUPABASE_KEY 가 설정돼 있을 때 이 테이블들을 쓴다.

-- 학번 → 학과 (웹에서 처음 로그인할 때 등록)
create table if not exists users (
    student_id text primary key,
    department text not null
);

-- 배출 기록. created_at 은 지금 JSON 파일과 같은 한국시간 문자열
-- (예: 2026-11-25T14:14:00+09:00) 그대로 저장해서 "오늘" 계산이 달라지지 않게 한다.
create table if not exists records (
    seq        bigint generated always as identity primary key,
    event_id   uuid not null unique default gen_random_uuid(),
    student_id text not null references users (student_id),
    department text not null,
    material   text not null,
    quantity   integer not null check (quantity >= 1),
    points     integer not null,
    created_at text not null
);
create index if not exists records_student_idx on records (student_id, created_at desc);

-- 지금 수거함에 연결된 학생 (수거함 1대 = 항상 1줄)
create table if not exists bin_session (
    id           integer primary key check (id = 1),
    student_id   text,
    started_at   text,
    requested_at text,   -- 웹에서 '수거 시작'을 누른 시각
    state        text    -- requested → running → ready → sorted / over_limit / done
);
-- 이미 테이블을 만들어 둔 경우: '수거 시작' 버튼용 칸 두 개만 추가된다.
alter table bin_session add column if not exists requested_at text;
alter table bin_session add column if not exists state text;

-- 공개(anon) 키로는 읽거나 쓸 수 없게 막는다. 웹과 파이는 secret(service_role) 키를 쓴다.
alter table users       enable row level security;
alter table records     enable row level security;
alter table bin_session enable row level security;
