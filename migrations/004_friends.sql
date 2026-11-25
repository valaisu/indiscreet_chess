-- Friends: request and accept.
--
-- One row per pair, stored with the smaller id first, so "are A and B
-- friends" is a primary-key lookup whichever of them is asking, and two
-- requests crossing in the same second cannot make two rows. Who asked is a
-- column rather than the row's direction: the pair is the fact, the request is
-- a detail of how it started.
create table friendships (
  user_lo      uuid not null references users(id) on delete cascade,
  user_hi      uuid not null references users(id) on delete cascade,
  requested_by uuid not null references users(id) on delete cascade,
  status       text not null check (status in ('pending', 'accepted')),
  created_at   timestamptz not null default now(),
  accepted_at  timestamptz,
  primary key (user_lo, user_hi),
  constraint friendships_ordered check (user_lo < user_hi),
  constraint friendships_requester_is_a_member
    check (requested_by = user_lo or requested_by = user_hi)
);

-- The primary key serves lookups by user_lo; this serves user_hi.
create index friendships_by_hi on friendships (user_hi);
