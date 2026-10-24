-- Schéma de départ (plan du projet, « Conception de la mémoire »).
-- Appliquer avec : python scripts/apply_schema.py (sans risque de le relancer).

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

-- Journal des coûts : une ligne par appel à une API payante
create table if not exists usage (
  id bigserial primary key,
  day date not null default (now() at time zone 'Europe/Paris')::date,
  service text not null,       -- 'claude' ou 'openai'
  model text not null,
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  cache_read_tokens integer not null default 0,
  cache_write_tokens integer not null default 0,
  cost_usd numeric(12, 6) not null,
  created_at timestamptz default now()
);
create index if not exists usage_day_idx on usage (day);

-- Appels passés par Marcus (via Vapi)
create table if not exists calls (
  id bigserial primary key,
  vapi_id text,
  numero text not null,         -- format international (+33…)
  destinataire text not null,
  objectif text not null,
  ouverture text not null,      -- première phrase de Marcus
  details text not null default '',
  status text not null default 'en_attente', -- en_attente, annule, en_cours, termine, echec
  ended_reason text,
  resume text,
  transcript text,
  duree_secondes integer,
  created_at timestamptz default now(),
  ended_at timestamptz
);

-- Coûts estimés (minutes Twilio) corrigés ensuite avec le prix réel : référence de l'appel Twilio
alter table usage add column if not exists ref text;
alter table usage add column if not exists estime boolean not null default false;

-- Rappels qui se répètent : aucune, quotidienne, jours_ouvres, hebdomadaire, mensuelle
alter table reminders add column if not exists repetition text not null default 'aucune';

-- Appels reçus (rappels de commerces, proches…) : même table, sens 'entrant'
alter table calls add column if not exists direction text not null default 'sortant';
create unique index if not exists calls_vapi_id_idx on calls (vapi_id);

-- RLS activé sans aucune politique : la clé publique (anon) ne voit rien,
-- seul le bot, avec la connexion directe, lit et écrit.
alter table messages enable row level security;
alter table memories enable row level security;
alter table reminders enable row level security;
alter table usage enable row level security;
alter table calls enable row level security;
