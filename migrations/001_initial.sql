-- Schema for accounts, ratings and stored games.
--
-- Applied once, by server/db.py, which records it in schema_migrations and
-- will not run it twice. Never edit an applied migration: add another file.
--
-- Three things are being stored, and only three: who a player is, how strong
-- they are at each tempo, and what happened in a game. If a leak dumped all of
-- it, the damage is a list of display names, some numbers, and some chess.

-- Case-insensitive display names, so "Valo" and "valo" cannot both exist.
create extension if not exists citext;


-- ---------------------------------------------------------------------------
-- Players
-- ---------------------------------------------------------------------------
create table users (
  id              uuid primary key default gen_random_uuid(),

  -- Chosen by the player, and the only thing about them that anyone else sees.
  -- Deliberately not an email: none is collected, so none can be leaked, and
  -- there is no address to send a password reset to because there is no reset.
  name            citext not null unique,

  -- argon2id. The parameters travel inside the string, so raising them later
  -- does not invalidate existing hashes.
  --
  -- Null when `provider` is not 'local': an OAuth account has no password
  -- here, which is the whole point of adding one later.
  password_hash   text,

  -- 'local' today. The column exists now so that adding Google sign-in later
  -- is a new row value rather than a migration of every existing account.
  provider         text not null default 'local',
  provider_user_id text,

  created_at      timestamptz not null default now(),
  last_seen_at    timestamptz,

  constraint local_accounts_have_a_password
    check ((provider = 'local') = (password_hash is not null)),
  constraint federated_accounts_have_a_subject
    check ((provider = 'local') = (provider_user_id is null))
);

create unique index users_provider_subject
  on users (provider, provider_user_id)
  where provider_user_id is not null;


-- ---------------------------------------------------------------------------
-- Sessions
-- ---------------------------------------------------------------------------
-- A row per signed-in browser. Server-side rather than a self-contained token
-- so that signing out, or revoking every session after a scare, is a delete
-- rather than a wait for an expiry nobody can shorten.
create table sessions (
  -- sha256 of the cookie value, never the value itself: a dump of this table
  -- must not let anyone log in as anybody.
  token_hash   bytea primary key,
  user_id      uuid not null references users(id) on delete cascade,
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null
);

create index sessions_by_user on sessions (user_id);
create index sessions_expiry  on sessions (expires_at);


-- ---------------------------------------------------------------------------
-- Ratings
-- ---------------------------------------------------------------------------
-- One row per player per tempo. A separate table rather than three columns on
-- `users`, so a fourth tempo is a row and not a migration.
--
-- Civilizations on and off share a rating within a tempo: a civ is a balanced
-- side choice, not a different game.
create table ratings (
  user_id       uuid not null references users(id) on delete cascade,
  tempo         text not null check (tempo in ('bullet', 'rapid', 'slow')),
  rating        double precision not null default 1200,
  games_played  integer not null default 0,
  updated_at    timestamptz not null default now(),
  primary key (user_id, tempo)
);


-- ---------------------------------------------------------------------------
-- Games
-- ---------------------------------------------------------------------------
create table games (
  id            uuid primary key default gen_random_uuid(),
  played_at     timestamptz not null default now(),

  -- Null for an anonymous seat. Anonymous play stays first-class, so a game
  -- with one or two unknown players is still stored and still replayable by
  -- whoever holds the link.
  white_user_id uuid references users(id) on delete set null,
  black_user_id uuid references users(id) on delete set null,

  white_civ     text,
  black_civ     text,

  tempo         text not null,   -- 'bullet' | 'rapid' | 'slow' | 'custom'
  winner        text not null check (winner in ('white', 'black', 'draw')),
  ticks         integer not null,

  rated         boolean not null default false,
  -- Why not, when it was not. Shown in the profile so a game that did not move
  -- a rating can say which condition it failed rather than looking like a bug.
  unrated_reason text,

  -- What the rating did, so the profile can show it without recomputing an
  -- ordering that no longer exists.
  white_rating_before double precision,
  black_rating_before double precision,
  white_rating_after  double precision,
  black_rating_after  double precision,

  -- The recording. gzipped JSON of {header, events} from server/recorder.py:
  -- about 14 KB for a five minute game, against 41 MB if the snapshots
  -- themselves were kept. bytea rather than jsonb because nothing queries
  -- inside it; it is played back, not searched.
  log           bytea not null,
  log_format    integer not null,   -- recorder.FORMAT, so an old log is refused, not misdrawn

  -- Which civilization table this game was played under (civs.table_fingerprint).
  -- Balance figures group by it: without it a win rate averages games from
  -- before and after a rebalance, which is exactly the comparison being made.
  civ_table     text not null
);

create index games_by_white on games (white_user_id, played_at desc);
create index games_by_black on games (black_user_id, played_at desc);
-- The balance query: win rate per civ per tempo, within one table version.
create index games_balance on games (civ_table, tempo, rated);
