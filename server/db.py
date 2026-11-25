"""
Postgres access.

The server runs perfectly well without a database: every call here is a no-op
when DATABASE_URL is unset, and the game does not consult it for anything.
That is deliberate. Rooms, moves and results all live in memory as they always
have, and persistence is something that happens *after* a game, so a database
that is down, slow or absent must never be able to stop people playing.

One connection pool, a handful of statements, no ORM. The queries here are the
whole data layer.
"""

import asyncio
import gzip
import json
import logging
import os
import time
from pathlib import Path

try:
    import asyncpg
except ImportError:          # the server still runs; persistence is simply off
    asyncpg = None           # type: ignore[assignment]

log = logging.getLogger("db")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

_pool: "asyncpg.Pool | None" = None


def enabled() -> bool:
    return _pool is not None


def load_dotenv(path: Path) -> None:
    """Read KEY=value lines from a .env file into the environment.

    Five lines instead of a dependency. Only for local runs: in production the
    values are Fly secrets, which arrive as real environment variables.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if not value:
            # An empty assignment is a placeholder waiting to be filled in, not
            # a setting. setdefault would otherwise let one shadow a real value
            # further down the file, and the failure looks like a bad password.
            continue
        os.environ.setdefault(key.strip(), value)


async def connect(url: str | None) -> None:
    """Open the pool and bring the schema up to date. Missing URL is not an
    error: it is how the server runs with persistence switched off."""
    global _pool
    if not url:
        log.info("DATABASE_URL not set: games will not be saved")
        return
    if asyncpg is None:
        log.warning("asyncpg is not installed: games will not be saved")
        return
    # statement_cache_size=0 because Supabase's transaction pooler multiplexes
    # connections, so a prepared statement made on one backend is not there on
    # the next. Harmless on a direct connection, required through the pooler.
    _pool = await asyncpg.create_pool(url, min_size=1, max_size=4,
                                      statement_cache_size=0,
                                      command_timeout=10.0)
    await _migrate()
    log.info("database connected")


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _migrate() -> None:
    """Apply any migration file not already recorded, in filename order."""
    assert _pool is not None
    async with _pool.acquire() as conn:
        await conn.execute("""
            create table if not exists schema_migrations (
              version    text primary key,
              applied_at timestamptz not null default now()
            )
        """)
        done = {r["version"] for r in
                await conn.fetch("select version from schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done:
                continue
            log.info("applying migration %s", path.name)
            # One transaction per migration: a half-applied schema is worse
            # than an unapplied one.
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "insert into schema_migrations (version) values ($1)",
                    path.stem)


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------

def pack_log(recording: dict) -> bytes:
    """The stored form of a recording: gzipped JSON, about 14 KB for a five
    minute game. allow_nan because a non-finite number is not JSON and would
    make the row unreadable by anything, silently, long after the game."""
    return gzip.compress(json.dumps(recording, allow_nan=False).encode(), 6)


def unpack_log(blob: bytes) -> dict:
    return json.loads(gzip.decompress(blob))


async def save_game(*, white_user_id, black_user_id, white_civ, black_civ,
                    tempo: str, winner: str, ticks: int, rated: bool,
                    unrated_reason: str | None, recording: dict,
                    log_format: int, civ_table: str, solo: bool = False) -> str | None:
    """Store one finished game. Returns its id, or None if not stored.

    Never raises into the caller: a game that cannot be saved is a game that
    was still played, and the two people who played it are owed a working
    rematch button rather than a stack trace.
    """
    if _pool is None:
        return None
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                insert into games (white_user_id, black_user_id,
                                   white_civ, black_civ, tempo, winner, ticks,
                                   rated, unrated_reason,
                                   log, log_format, civ_table, solo)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                returning id
            """, white_user_id, black_user_id, white_civ, black_civ,
                 tempo, winner, ticks, rated, unrated_reason,
                 pack_log(recording), log_format, civ_table, solo)
        return str(row["id"])
    except Exception:
        log.exception("could not save game")
        return None


async def get_game(game_id: str) -> dict | None:
    """One stored game with its recording, for replay."""
    if _pool is None:
        return None
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                select id, played_at, white_civ, black_civ, tempo, winner,
                       ticks, rated, unrated_reason, log, log_format,
                       white_user_id, black_user_id
                from games where id = $1
            """, game_id)
    except Exception:
        log.exception("could not read game %s", game_id)
        return None
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "played_at": row["played_at"].timestamp(),
        "civs": {"white": row["white_civ"], "black": row["black_civ"]},
        "tempo": row["tempo"],
        "winner": row["winner"],
        "ticks": row["ticks"],
        "rated": row["rated"],
        "unrated_reason": row["unrated_reason"],
        "log_format": row["log_format"],
        # Who played it. Any signed-in player may watch a finished game, so
        # this is no longer a permission - it is who the replay is of.
        "players": {str(row[c]) for c in ("white_user_id", "black_user_id")
                    if row[c] is not None},
        "recording": unpack_log(row["log"]),
    }


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

async def create_user(name: str, password_hash: str) -> dict | None:
    """Insert a new local account. Returns None if the name is taken.

    The uniqueness check is the unique index, not a prior SELECT: two people
    claiming the same name in the same second is exactly the case a check-then-
    insert loses.
    """
    if _pool is None:
        return None
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                insert into users (name, password_hash) values ($1, $2)
                on conflict (name) do nothing
                returning id, name
            """, name, password_hash)
    except Exception:
        log.exception("could not create user")
        return None
    return None if row is None else {"id": str(row["id"]), "name": row["name"]}


async def find_user(name: str) -> dict | None:
    """Look up a local account by name, with its hash, for sign-in."""
    if _pool is None:
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            select id, name, password_hash from users
            where name = $1 and provider = 'local'
        """, name)
    if row is None:
        return None
    return {"id": str(row["id"]), "name": row["name"],
            "password_hash": row["password_hash"]}


async def create_session(user_id: str, token_hash: bytes, ttl_days: int) -> None:
    if _pool is None:
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            insert into sessions (token_hash, user_id, expires_at)
            values ($1, $2, now() + ($3 || ' days')::interval)
        """, token_hash, user_id, str(ttl_days))


async def session_user(token_hash: bytes) -> dict | None:
    """Whose session this is, or None if unknown or expired.

    Expired rows are left for the sweep rather than deleted here: a read path
    that writes turns every page load into a write, and the row is already
    being ignored.
    """
    if _pool is None:
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            select u.id, u.name from sessions s
            join users u on u.id = s.user_id
            where s.token_hash = $1 and s.expires_at > now()
        """, token_hash)
    return None if row is None else {"id": str(row["id"]), "name": row["name"]}


async def delete_session(token_hash: bytes) -> None:
    if _pool is None:
        return
    async with _pool.acquire() as conn:
        await conn.execute("delete from sessions where token_hash = $1", token_hash)


async def sweep_sessions() -> int:
    """Drop expired sessions. Nothing depends on this running promptly."""
    if _pool is None:
        return 0
    async with _pool.acquire() as conn:
        result = await conn.execute("delete from sessions where expires_at <= now()")
    return int(result.split()[-1]) if result else 0


# ---------------------------------------------------------------------------
# Personal settings
# ---------------------------------------------------------------------------
# The account's half of the pair. The device keeps its own copy in
# localStorage; where a key exists here it overrides that one.

async def get_user_settings(user_id: str) -> dict:
    """What this account has an opinion about. Empty means "no opinion", which
    leaves whatever the browser it signs in on already had."""
    if _pool is None:
        return {}
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("select settings from users where id = $1",
                                  user_id)
    return {} if row is None else json.loads(row["settings"])


async def set_user_settings(user_id: str, values: dict) -> None:
    """Replace them. A replace and not a merge, because the client sends the
    whole object it holds and a setting it dropped has to be able to go."""
    if _pool is None:
        return
    async with _pool.acquire() as conn:
        await conn.execute("update users set settings = $2 where id = $1",
                           user_id, json.dumps(values))


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------

async def get_ratings(user_id: str) -> dict[str, dict]:
    """Every tempo this player has a rating in. Absent tempos are unplayed."""
    if _pool is None:
        return {}
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            select tempo, rating, games_played from ratings where user_id = $1
        """, user_id)
    return {r["tempo"]: {"rating": round(r["rating"], 1),
                         "games": r["games_played"]} for r in rows}


async def public_profile(*, user_id: str | None = None,
                         name: str | None = None) -> dict | None:
    """One player as anyone is allowed to see them: name, id and ratings.

    Their games are a separate query (recent_games), because they are paged
    and this is not: a card is one row, a history is as long as the player has
    been playing.
    """
    if _pool is None:
        return None
    if not user_id and not name:
        return None
    async with _pool.acquire() as conn:
        if user_id:
            row = await conn.fetchrow(
                "select id, name from users where id = $1", user_id)
        else:
            row = await conn.fetchrow(
                "select id, name from users where name = $1", name)
    if row is None:
        return None
    uid = str(row["id"])
    return {"id": uid, "name": row["name"], "ratings": await get_ratings(uid)}


async def apply_rating(*, game_id: str, white_user_id: str, black_user_id: str,
                       tempo: str, winner: str, update_fn) -> dict | None:
    """Move both ratings for one finished game, atomically.

    Read, compute and write happen inside one transaction with the two rows
    locked, because the same player can finish two games at nearly the same
    moment on two devices, and a read-modify-write race would silently drop
    one of the results.

    `update_fn` is rating.update, passed in so this file keeps no opinion about
    the arithmetic.
    """
    if _pool is None:
        return None
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                # Create the rows on first play, then lock both. Ordered by id
                # so two games sharing a player cannot deadlock against
                # each other by taking the same locks in opposite orders.
                for uid in sorted([white_user_id, black_user_id]):
                    await conn.execute("""
                        insert into ratings (user_id, tempo) values ($1, $2)
                        on conflict (user_id, tempo) do nothing
                    """, uid, tempo)
                rows = await conn.fetch("""
                    select user_id, rating, games_played from ratings
                    where user_id = any($1::uuid[]) and tempo = $2
                    order by user_id
                    for update
                """, sorted([white_user_id, black_user_id]), tempo)
                by_id = {str(r["user_id"]): r for r in rows}
                w, b = by_id[white_user_id], by_id[black_user_id]

                new_w, new_b = update_fn(w["rating"], b["rating"], winner,
                                         w["games_played"], b["games_played"])

                for uid, new in ((white_user_id, new_w), (black_user_id, new_b)):
                    await conn.execute("""
                        update ratings set rating = $3, games_played = games_played + 1,
                                           updated_at = now()
                        where user_id = $1 and tempo = $2
                    """, uid, tempo, new)

                await conn.execute("""
                    update games set white_rating_before = $2, black_rating_before = $3,
                                     white_rating_after = $4, black_rating_after = $5
                    where id = $1
                """, game_id, w["rating"], b["rating"], new_w, new_b)
    except Exception:
        log.exception("could not apply rating for game %s", game_id)
        return None
    return {
        "tempo": tempo,
        "white": {"before": round(w["rating"], 1), "after": round(new_w, 1)},
        "black": {"before": round(b["rating"], 1), "after": round(new_b, 1)},
    }


async def recent_games(user_id: str, limit: int = 20, offset: int = 0) -> dict:
    """One page of a player's finished games, newest first, without recordings.

    The log is deliberately not selected: it is most of the row, and a page of
    them would be a megabyte to send so somebody can read the results. The
    recording is fetched one game at a time, when a replay is actually opened.

    Both players are described, not just the opponent, because this list is
    now also how one player looks at another's profile - and because a result
    means nothing without the two ratings it was played between. `seat` is
    which side the player whose page this is was on; everything the caller
    wants about "them" and "the other one" follows from that and `players`,
    rather than being sent twice and drifting.

    Returns the page with the total, so a pager can say how far it goes.
    """
    if _pool is None:
        return {"games": [], "offset": 0, "total": 0}
    async with _pool.acquire() as conn:
        total = await conn.fetchval("""
            select count(*) from games
            where white_user_id = $1 or black_user_id = $1
        """, user_id)
        rows = await conn.fetch("""
            select g.id, g.played_at, g.white_user_id, g.black_user_id,
                   g.white_civ, g.black_civ, g.tempo, g.winner, g.ticks,
                   g.rated, g.unrated_reason,
                   g.white_rating_before, g.black_rating_before,
                   g.white_rating_after,  g.black_rating_after,
                   w.name as white_name, b.name as black_name
            from games g
            left join users w on w.id = g.white_user_id
            left join users b on b.id = g.black_user_id
            where g.white_user_id = $1 or g.black_user_id = $1
            order by g.played_at desc
            limit $2 offset $3
        """, user_id, limit, offset)

    def side(r, color: str) -> dict:
        before, after = r[f"{color}_rating_before"], r[f"{color}_rating_after"]
        return {
            # Null is the answer for an anonymous seat, not a missing value.
            "name": r[f"{color}_name"],
            "civ": r[f"{color}_civ"],
            "rating_before": None if before is None else round(before, 1),
            "rating_after": None if after is None else round(after, 1),
        }

    games = []
    for r in rows:
        games.append({
            "id": str(r["id"]),
            "at": r["played_at"].timestamp(),
            "seat": "white" if str(r["white_user_id"]) == user_id else "black",
            "tempo": r["tempo"],
            "winner": r["winner"],
            "ticks": r["ticks"],
            "rated": r["rated"],
            "unrated_reason": r["unrated_reason"],
            "players": {c: side(r, c) for c in ("white", "black")},
        })
    return {"games": games, "offset": offset, "total": int(total or 0)}


async def touch_user(user_id: str) -> None:
    """Record that this account was here. Once per sign-in or resumed session,
    not per message: it is for the admin's "last seen", which needs a day, not
    a second."""
    if _pool is None:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute("update users set last_seen_at = now() where id = $1",
                               user_id)
    except Exception:
        log.exception("could not touch user %s", user_id)


# ---------------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------------
# One row per pair, smaller id first (migrations/004_friends.sql).

MAX_FRIENDS = 200
MAX_PENDING_OUT = 50


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


async def friend_request(me: str, other: str) -> str | None:
    """Ask `other` to be friends. Returns a reason it was refused, or None.

    A request to somebody who has already asked you is an acceptance: two
    people who each pressed "add friend" want the same thing, and making the
    second one wait for the first to come back and press Accept is a step for
    nobody's benefit.
    """
    if _pool is None:
        return "accounts are not available on this server"
    if me == other:
        return "you cannot add yourself"
    lo, hi = _pair(me, other)
    async with _pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""
                select requested_by, status from friendships
                where user_lo = $1 and user_hi = $2 for update
            """, lo, hi)
            if row is not None:
                if row["status"] == "accepted":
                    return "you are already friends"
                if str(row["requested_by"]) == me:
                    return "you have already asked"
                await conn.execute("""
                    update friendships set status = 'accepted', accepted_at = now()
                    where user_lo = $1 and user_hi = $2
                """, lo, hi)
                return None
            friends = await conn.fetchval("""
                select count(*) from friendships
                where (user_lo = $1 or user_hi = $1) and status = 'accepted'
            """, me)
            if friends >= MAX_FRIENDS:
                return f"you already have {MAX_FRIENDS} friends"
            pending = await conn.fetchval("""
                select count(*) from friendships
                where requested_by = $1 and status = 'pending'
            """, me)
            if pending >= MAX_PENDING_OUT:
                return (f"you have {MAX_PENDING_OUT} requests nobody has "
                        "answered yet; cancel some first")
            await conn.execute("""
                insert into friendships (user_lo, user_hi, requested_by, status)
                values ($1, $2, $3, 'pending')
            """, lo, hi, me)
    return None


async def friend_respond(me: str, other: str, accept: bool) -> bool:
    """Answer a request `other` sent. False if there was none to answer."""
    if _pool is None:
        return False
    lo, hi = _pair(me, other)
    async with _pool.acquire() as conn:
        if accept:
            result = await conn.execute("""
                update friendships set status = 'accepted', accepted_at = now()
                where user_lo = $1 and user_hi = $2
                  and status = 'pending' and requested_by = $3
            """, lo, hi, other)
        else:
            result = await conn.execute("""
                delete from friendships
                where user_lo = $1 and user_hi = $2
                  and status = 'pending' and requested_by = $3
            """, lo, hi, other)
    return result.endswith(" 1")


async def friend_remove(me: str, other: str) -> None:
    """End a friendship, or take back a request you sent. Either way the row
    goes: the two are the same fact, that this pair is not friends."""
    if _pool is None:
        return
    lo, hi = _pair(me, other)
    async with _pool.acquire() as conn:
        await conn.execute("""
            delete from friendships
            where user_lo = $1 and user_hi = $2
              and (status = 'accepted' or requested_by = $3)
        """, lo, hi, me)


async def friends_of(me: str) -> list[dict]:
    """Every row this account is part of, as the other person and the state of
    things between you: 'friends', 'incoming' or 'outgoing'."""
    if _pool is None:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            select u.id, u.name, f.status, f.requested_by
            from friendships f
            join users u on u.id = case when f.user_lo = $1
                                        then f.user_hi else f.user_lo end
            where f.user_lo = $1 or f.user_hi = $1
            order by lower(u.name::text)
        """, me)
    out = []
    for r in rows:
        if r["status"] == "accepted":
            rel = "friends"
        else:
            rel = "outgoing" if str(r["requested_by"]) == me else "incoming"
        out.append({"id": str(r["id"]), "name": r["name"], "rel": rel})
    return out


async def friendship(me: str, other: str) -> str:
    """'friends', 'incoming', 'outgoing' or 'none', from `me`'s side."""
    if _pool is None or me == other:
        return "none"
    lo, hi = _pair(me, other)
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            select status, requested_by from friendships
            where user_lo = $1 and user_hi = $2
        """, lo, hi)
    if row is None:
        return "none"
    if row["status"] == "accepted":
        return "friends"
    return "outgoing" if str(row["requested_by"]) == me else "incoming"


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
# Read-only. The callers check that the socket is an admin's; nothing here
# does, so nothing here may be reachable from anywhere else.

async def admin_stats() -> dict:
    """Totals across the whole database, and games per day for two weeks."""
    if _pool is None:
        return {}
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            select
              (select count(*) from users) as users,
              (select count(*) from users
                 where created_at > now() - interval '7 days') as users_7d,
              (select count(*) from users
                 where created_at > now() - interval '30 days') as users_30d,
              (select count(*) from users
                 where last_seen_at > now() - interval '7 days') as active_7d,
              (select count(*) from games) as games,
              (select count(*) from games
                 where played_at > now() - interval '1 day') as games_1d,
              (select count(*) from games
                 where played_at > now() - interval '7 days') as games_7d,
              (select count(*) from games where rated) as games_rated,
              (select count(*) from games where solo) as games_solo
        """)
        days = await conn.fetch("""
            select to_char(d, 'YYYY-MM-DD') as day, count(g.id) as n
            from generate_series(current_date - 13, current_date, interval '1 day') d
            left join games g on g.played_at >= d and g.played_at < d + interval '1 day'
            group by d order by d
        """)
    return {**{k: int(v) for k, v in row.items()},
            "per_day": [{"day": d["day"], "games": int(d["n"])} for d in days]}


async def admin_games(limit: int, offset: int) -> dict:
    """Every stored game, newest first, without recordings."""
    if _pool is None:
        return {"games": [], "offset": 0, "total": 0}
    async with _pool.acquire() as conn:
        total = await conn.fetchval("select count(*) from games")
        rows = await conn.fetch("""
            select g.id, g.played_at, g.tempo, g.winner, g.ticks, g.rated,
                   g.solo, g.white_civ, g.black_civ,
                   w.name as white_name, b.name as black_name
            from games g
            left join users w on w.id = g.white_user_id
            left join users b on b.id = g.black_user_id
            order by g.played_at desc
            limit $1 offset $2
        """, limit, offset)
    games = [{
        "id": str(r["id"]),
        "at": r["played_at"].timestamp(),
        "tempo": r["tempo"],
        "winner": r["winner"],
        "ticks": r["ticks"],
        "rated": r["rated"],
        "solo": r["solo"],
        "white": {"name": r["white_name"], "civ": r["white_civ"]},
        "black": {"name": r["black_name"], "civ": r["black_civ"]},
    } for r in rows]
    return {"games": games, "offset": offset, "total": int(total or 0)}


async def admin_users(limit: int, offset: int) -> dict:
    """Every account, newest first, with how much it has played."""
    if _pool is None:
        return {"users": [], "offset": 0, "total": 0}
    async with _pool.acquire() as conn:
        total = await conn.fetchval("select count(*) from users")
        rows = await conn.fetch("""
            select u.id, u.name, u.created_at, u.last_seen_at,
                   (select count(*) from games g
                     where g.white_user_id = u.id or g.black_user_id = u.id) as games
            from users u
            order by u.created_at desc
            limit $1 offset $2
        """, limit, offset)
    users = [{
        "id": str(r["id"]),
        "name": r["name"],
        "created": r["created_at"].timestamp(),
        "last_seen": r["last_seen_at"].timestamp() if r["last_seen_at"] else None,
        "games": int(r["games"]),
    } for r in rows]
    return {"users": users, "offset": offset, "total": int(total or 0)}
