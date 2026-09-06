-- AutoTrade Cryptos · Supabase schema
-- Pegar en: Supabase → SQL Editor → New query → Run
-- Service role bypasea RLS; el frontend NO debe usar la service key.

create extension if not exists "pgcrypto";

-- ─── Operaciones activas / históricas ───────────────────────────────────────
create table if not exists public.operations (
  id uuid primary key,
  owner_wallet text not null default 'default',
  token_address text not null,
  buy_price_usd numeric not null check (buy_price_usd > 0),
  sell_price_usd numeric not null check (sell_price_usd > 0),
  usdc_amount numeric not null check (usdc_amount > 0),
  demo boolean not null default true,
  cycle_seconds integer not null default 86400 check (cycle_seconds > 0),
  status text not null
    check (status in ('draft', 'running', 'paused', 'completed', 'stopped')),
  phase text not null
    check (phase in ('idle', 'waiting_buy', 'holding', 'waiting_sell', 'grid')),
  token jsonb not null default '{}'::jsonb,
  last_price_usd numeric,
  bought_token_amount_raw bigint not null default 0,
  buy_amount_out numeric,
  sell_amount_out numeric,
  buy_tx text,
  sell_tx text,
  demo_usdc_balance numeric not null default 1000,
  estimated jsonb not null default '{}'::jsonb,
  realized_profit_usd numeric,
  realized_total_usd numeric,
  error text,
  mode text not null default 'classic'
    check (mode in ('classic', 'grid')),
  grid_count integer not null default 0,
  grid_levels jsonb not null default '[]'::jsonb,
  last_cycle_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists operations_status_idx
  on public.operations (status);

create index if not exists operations_owner_status_idx
  on public.operations (owner_wallet, status);

create index if not exists operations_updated_idx
  on public.operations (updated_at desc);

-- ─── Auditoría de eventos por operación ───────────────────────────────────
create table if not exists public.operation_events (
  id uuid primary key default gen_random_uuid(),
  operation_id uuid not null references public.operations (id) on delete cascade,
  event_key text not null,
  kind text not null,
  title text not null,
  detail text not null default '',
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  unique (operation_id, event_key)
);

create index if not exists operation_events_op_ts_idx
  on public.operation_events (operation_id, created_at desc);

create index if not exists operation_events_kind_idx
  on public.operation_events (kind, created_at desc);

-- ─── Auditoría de ejecuciones del cron diario ─────────────────────────────
create table if not exists public.cron_runs (
  id uuid primary key default gen_random_uuid(),
  job_name text not null default 'daily-cycles',
  started_at timestamptz not null default timezone('utc', now()),
  finished_at timestamptz,
  ok boolean,
  processed integer not null default 0,
  succeeded integer not null default 0,
  failed integer not null default 0,
  details jsonb not null default '{}'::jsonb,
  error text
);

create index if not exists cron_runs_started_idx
  on public.cron_runs (started_at desc);

-- ─── RLS: cerrado al anon/authenticated; solo service_role (API) ───────────
alter table public.operations enable row level security;
alter table public.operation_events enable row level security;
alter table public.cron_runs enable row level security;

-- Sin policies públicas = nadie con anon key lee/escribe.
-- La API usa SUPABASE_SERVICE_ROLE_KEY (bypasea RLS).

comment on table public.operations is 'Operaciones AutoTrade (classic/grid). Ciclo fijo 24h vía cron Vercel.';
comment on table public.operation_events is 'Timeline auditable de cada operación (cascade delete).';
comment on table public.cron_runs is 'Histórico de jobs /api/cron/daily-cycles.';
