-- Mowafak AI PreScreen — Supabase schema.
-- Apply via the Supabase SQL editor, `supabase db push`, or a migration
-- file. supabase-py (database.py / models_db.py) does NOT create these
-- tables for you.

create extension if not exists "pgcrypto";

create table if not exists candidates (
    id uuid primary key default gen_random_uuid(),
    consent_given boolean not null default false,
    raw_skills jsonb not null default '[]',
    total_experience_years numeric not null default 0,
    summary text,
    created_at timestamptz not null default now()
);

create table if not exists interview_sessions (
    id uuid primary key default gen_random_uuid(),
    candidate_id uuid not null references candidates(id) on delete cascade,
    role text,
    status text not null default 'in_progress',
    created_at timestamptz not null default now()
);

create table if not exists questions (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references interview_sessions(id) on delete cascade,
    question_index int not null,
    question_text text not null,
    skill_targeted text,
    question_type text
);

create table if not exists answers (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references interview_sessions(id) on delete cascade,
    question_id uuid not null references questions(id) on delete cascade,
    transcript text,
    language_detected text,
    audio_file_path text,
    created_at timestamptz not null default now()
);

create table if not exists final_reports (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references interview_sessions(id) on delete cascade,
    overall_score numeric,
    ai_recommendation text,
    written_summary text,
    per_skill_ratings jsonb not null default '{}',
    strengths jsonb not null default '[]',
    areas_for_development jsonb not null default '[]',
    hr_decision text,
    hr_reviewer_id text,
    hr_notes text,
    hr_decided_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists idx_sessions_candidate on interview_sessions(candidate_id);
create index if not exists idx_questions_session on questions(session_id);
create index if not exists idx_answers_session on answers(session_id);
create index if not exists idx_answers_question on answers(question_id);
create index if not exists idx_reports_session on final_reports(session_id);

-- NOTE on Row Level Security:
-- This backend uses the service_role key, which bypasses RLS entirely, so
-- RLS is not strictly required for the API to function. It is still
-- strongly recommended to enable RLS on every table below and add explicit
-- policies, as defense-in-depth in case any key other than service_role
-- (e.g. anon) is ever used against this project.
--
-- alter table candidates enable row level security;
-- alter table interview_sessions enable row level security;
-- alter table questions enable row level security;
-- alter table answers enable row level security;
-- alter table final_reports enable row level security;
