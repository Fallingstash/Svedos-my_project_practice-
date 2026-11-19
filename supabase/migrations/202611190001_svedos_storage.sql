create table if not exists sessions (
  id text primary key,
  user_id text not null,
  track_name text not null,
  display_name text not null default '',
  genre text not null,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  track_path text,
  reference_path text,
  analysis_mode text not null default 'master',
  beat_path text,
  vocal_path text,
  segments_json jsonb not null default '[]'::jsonb,
  chat_history_json jsonb not null default '[]'::jsonb,
  vibe_description text not null default '',
  old_spectrum_json jsonb,
  old_frequencies_json jsonb,
  analysis_json jsonb,
  context_summary text not null default '',
  context_summary_message_count integer not null default 0
    check (context_summary_message_count >= 0),
  share_token_hash text unique,
  shared_at timestamptz,
  share_revoked_at timestamptz,
  share_enabled boolean not null default false,
  unique (id, user_id)
);

create index if not exists idx_sessions_owner_updated
  on sessions (user_id, updated_at desc);

create table if not exists session_files (
  object_key text primary key,
  session_id text not null,
  owner_id text not null,
  role text not null,
  original_name text not null,
  content_type text,
  byte_size bigint not null check (byte_size >= 0),
  created_at timestamptz not null default now(),
  foreign key (session_id, owner_id)
    references sessions (id, user_id) on delete cascade
);
create index if not exists idx_session_files_owner_session
  on session_files (owner_id, session_id);

create table if not exists message_feedback (
  session_id text not null,
  message_index integer not null check (message_index >= 0),
  user_id text not null,
  rating text not null check (rating in ('positive', 'negative')),
  primary key (session_id, message_index),
  foreign key (session_id, user_id)
    references sessions (id, user_id) on delete cascade
);

create table if not exists request_rate_limits (
  rate_key text primary key,
  last_request_at timestamptz not null
);

create table if not exists ai_request_logs (
  id bigserial primary key,
  user_id text not null,
  session_id text references sessions(id) on delete set null,
  model text,
  prompt_tokens integer check (prompt_tokens is null or prompt_tokens >= 0),
  completion_tokens integer check (
    completion_tokens is null or completion_tokens >= 0
  ),
  latency_ms integer not null check (latency_ms >= 0),
  attempts integer not null check (attempts > 0),
  created_at timestamptz not null default now()
);

insert into storage.buckets (id, name, public, file_size_limit)
values ('svedos-audio', 'svedos-audio', false, 104857600)
on conflict (id) do update
set public = false, file_size_limit = 104857600;
