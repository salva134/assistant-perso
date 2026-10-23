-- Schéma de départ (plan du projet, « Conception de la mémoire »).
-- À coller dans Supabase > SQL Editor, puis Run (déjà appliqué le 2026-10-23).

create extension if not exists vector;

create table if not exists messages (
  id bigserial primary key,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamptz default now()
);

create table if not exists memories (
  id bigserial primary key,
  type text not null check (type in ('profil', 'preference', 'fait', 'evenement')),
  content text not null,
  embedding vector(1536),      -- text-embedding-3-small
  source_message_id bigint references messages(id) on delete set null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists memories_embedding_idx
  on memories using hnsw (embedding vector_cosine_ops);

create table if not exists reminders (
  id bigserial primary key,
  text text not null,
  due_at timestamptz not null, -- stocké en UTC, affiché en heure de Paris
  sent boolean default false,
  created_at timestamptz default now()
);

-- RLS activé sans aucune politique : la clé publique (anon) ne voit rien,
-- seul le bot, avec la clé secrète, lit et écrit.
alter table messages enable row level security;
alter table memories enable row level security;
alter table reminders enable row level security;
