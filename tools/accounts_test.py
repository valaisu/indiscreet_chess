"""
Accounts, sessions and rating movement, against the real database.

    .venv/bin/python -m tools.accounts_test      (needs DATABASE_URL, from .env)

Creates its own throwaway accounts, exercises them, and deletes everything it
made. Not in deploy.sh: it needs a live database, and deploy.sh must keep
working on a machine with none.
"""

import asyncio
import os
import secrets
import sys
import time
from pathlib import Path

from server import accounts, db, rating

ROOT = Path(__file__).resolve().parent.parent
FAILS: list[str] = []
MADE: list[str] = []          # user ids to delete at the end


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def ok(name: str, condition: bool) -> None:
    check(name, bool(condition), True)


async def main() -> int:
    db.load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set")
        return 2
    await db.connect(url)

    tag = secrets.token_hex(4)
    alice, bob = f"t{tag}alice", f"t{tag}bob"
    PW = "correct horse battery"

    # --- sign up ----------------------------------------------------------
    a, err = await accounts.sign_up(alice, PW)
    check("sign up succeeds", err, None)
    ok("sign up returns a session token", a and len(a["token"]) > 30)
    if a:
        MADE.append(a["id"])

    _, err = await accounts.sign_up(alice, PW)
    check("the same name twice is refused", err, "that name is taken")

    _, err = await accounts.sign_up(alice.upper(), PW)
    check("names are case insensitive", err, "that name is taken")

    _, err = await accounts.sign_up("no", PW)
    ok("a short name is refused", err is not None)
    _, err = await accounts.sign_up(f"t{tag}x", "short")
    check("a short password is refused", err,
          f"password must be at least {accounts.MIN_PASSWORD} characters")

    # --- the hash ---------------------------------------------------------
    stored = await db.find_user(alice)
    ok("the password is not stored", PW not in (stored or {}).get("password_hash", ""))
    ok("the hash is argon2id", (stored or {})["password_hash"].startswith("$argon2id$"))

    # --- sign in ----------------------------------------------------------
    _, err = await accounts.sign_in(alice, "wrong password")
    check("a wrong password is refused", err, "wrong name or password")
    _, err = await accounts.sign_in(f"t{tag}nobody", PW)
    check("an unknown name gives the same message", err, "wrong name or password")

    signed, err = await accounts.sign_in(alice, PW)
    check("the right password signs in", err, None)

    # --- sessions ---------------------------------------------------------
    resumed = await accounts.resume(signed["token"])
    check("a session token resumes", (resumed or {}).get("name"), alice)
    ok("the raw token is not stored",
       await db.session_user(signed["token"].encode()[:32].ljust(32, b"0")) is None)

    await accounts.sign_out(signed["token"])
    check("signing out kills the session", await accounts.resume(signed["token"]), None)
    ok("the other session still works", await accounts.resume(a["token"]) is not None)

    # --- personal settings ------------------------------------------------
    # The account's half of the pair. The device keeps its own copy in the
    # browser; this is only what the account has an opinion about, so a fresh
    # one has none and must not arrive looking like a set of defaults.
    check("a new account has no settings",
          await db.get_user_settings(a["id"]), {})
    ok("they ride along with the identity", "settings" in a)

    await db.set_user_settings(a["id"], {"showHints": False, "preciseKey": "q"})
    check("they come back as they went in",
          await db.get_user_settings(a["id"]),
          {"showHints": False, "preciseKey": "q"})

    resumed_settings = await accounts.resume(a["token"])
    check("and a resumed session carries them",
          (resumed_settings or {}).get("settings"),
          {"showHints": False, "preciseKey": "q"})

    # A replace, not a merge: a setting the player stopped having an opinion
    # about has to be able to leave.
    await db.set_user_settings(a["id"], {"showHints": True})
    check("storing them again replaces the lot",
          await db.get_user_settings(a["id"]), {"showHints": True})

    # --- hashing stays off the event loop ---------------------------------
    # A hash on the loop would stall every live game on the machine. This
    # asserts the loop keeps running while one is in flight, rather than
    # asserting on a duration, which would be flaky on a loaded machine.
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    t0 = time.perf_counter()
    await accounts.hash_password(PW)
    elapsed = time.perf_counter() - t0
    beat.cancel()
    print(f"       (one hash took {elapsed * 1000:.0f} ms, "
          f"loop ticked {ticks} times meanwhile)")
    ok("the event loop keeps running during a hash", ticks >= 2)

    # --- rating -----------------------------------------------------------
    b, _ = await accounts.sign_up(bob, PW)
    MADE.append(b["id"])
    check("a new account has no ratings yet", await db.get_ratings(b["id"]), {})

    game_id = await db.save_game(
        white_user_id=a["id"], black_user_id=b["id"],
        white_civ="roman", black_civ="norse", tempo="rapid",
        winner="white", ticks=100, rated=True, unrated_reason=None,
        recording={"header": {}, "events": []}, log_format=1, civ_table="test",
    )
    ok("a rated game is stored", game_id is not None)

    moved = await db.apply_rating(game_id=game_id, white_user_id=a["id"],
                                  black_user_id=b["id"], tempo="rapid",
                                  winner="white", update_fn=rating.update)
    check("the winner gains", moved["white"], {"before": 1200.0, "after": 1216.0})
    check("the loser drops", moved["black"], {"before": 1200.0, "after": 1184.0})

    after = await db.get_ratings(a["id"])
    check("the rating is persisted", after["rapid"], {"rating": 1216.0, "games": 1})
    check("only the played tempo exists", sorted(after), ["rapid"])

    # --- one page of games ------------------------------------------------
    # A history row is now the whole game rather than the opponent's half of
    # it: the same rows draw your own profile and somebody else's card, and a
    # result means nothing without the two ratings it was played between.
    second = await db.save_game(
        white_user_id=b["id"], black_user_id=a["id"],
        white_civ=None, black_civ="greek", tempo="rapid",
        winner="draw", ticks=50, rated=False,
        unrated_reason="the host chose an unrated game",
        recording={"header": {}, "events": []}, log_format=1, civ_table="test")
    games = [game_id, second]

    page = await db.recent_games(a["id"], limit=20, offset=0)
    check("both games are listed", page["total"], 2)
    check("newest first", page["games"][0]["id"], second)
    check("the seat is the side the page's owner played",
          page["games"][0]["seat"], "black")
    check("the other player is named", page["games"][0]["players"]["white"]["name"],
          bob)
    check("an anonymous civilization stays null",
          page["games"][0]["players"]["white"]["civ"], None)
    rated_row = page["games"][1]
    check("the rating before the game is carried",
          rated_row["players"]["white"]["rating_before"], 1200.0)
    check("and the rating after it",
          rated_row["players"]["white"]["rating_after"], 1216.0)

    page2 = await db.recent_games(a["id"], limit=1, offset=1)
    check("a page holds one row", len(page2["games"]), 1)
    check("and it is the next one down", page2["games"][0]["id"], game_id)
    check("the total is of everything, not the page", page2["total"], 2)
    check("the offset comes back with the page", page2["offset"], 1)

    profile = await db.public_profile(name=alice)
    check("a public profile is found by name", profile["id"], a["id"])
    check("and carries the rating", profile["ratings"]["rapid"]["rating"], 1216.0)

    async with db._pool.acquire() as conn:                # noqa: SLF001
        await conn.execute("delete from games where id = any($1::uuid[])", games)
        await conn.execute("delete from users where id = any($1::uuid[])", MADE)
        left = await conn.fetchval(
            "select count(*) from users where id = any($1::uuid[])", MADE)
    check("test accounts removed", left, 0)

    await db.close()
    if FAILS:
        print(f"\nFAIL: {len(FAILS)} check(s)")
        for f in FAILS:
            print(f"  {f}")
        return 1
    print("\nPASS: accounts, sessions and rating")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
