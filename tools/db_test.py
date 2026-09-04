"""
End-to-end database check: migrate, store a real game, read it back, replay it.

    .venv/bin/python -m tools.db_test          (needs DATABASE_URL, from .env)

This is the one that proves the whole pipeline rather than any single piece of
it: the schema applies, a recording survives gzip and a bytea round trip, and
what comes back out of Postgres still expands to the exact frames the server
broadcast. It cleans up the row it wrote.

Nothing here runs in deploy.sh. It needs a live database, and deploy.sh has to
keep working on a machine that has none.
"""

import asyncio
import os
import random
import sys
from pathlib import Path

from server import civs, db, presets, rating
from tools.replay_test import compare, play, run_node

ROOT = Path(__file__).resolve().parent.parent


async def main() -> int:
    db.load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. Put it in .env (already gitignored):")
        print('  DATABASE_URL=postgresql://...')
        return 2

    # Never print the URL: it carries the password.
    host = url.split("@")[-1].split("/")[0] if "@" in url else "?"
    print(f"connecting to {host}")
    await db.connect(url)
    if not db.enabled():
        print("FAIL: pool did not open")
        return 1
    print("connected, migrations applied")

    print("playing a game...")
    game, live = play(random.Random(11), 1200, ("roman", "norse"))
    recording = game.recorder.to_dict()

    tempo = presets.tempo_name(game._pp["white"]) or "custom"
    reason = rating.rated_reason(False, None, None, False, False)
    game_id = await db.save_game(
        white_user_id=None, black_user_id=None,
        white_civ="roman", black_civ="norse",
        tempo=tempo, winner=game.winner or "draw", ticks=game.tick,
        rated=False, unrated_reason=reason,
        recording=recording, log_format=game.recorder.format,
        civ_table=civs.table_fingerprint(),
    )
    if not game_id:
        print("FAIL: game was not stored")
        await db.close()
        return 1
    stored = len(db.pack_log(recording))
    print(f"stored game {game_id} ({stored / 1024:.1f} KB compressed)")

    got = await db.get_game(game_id)
    if got is None:
        print("FAIL: stored game could not be read back")
        await db.close()
        return 1

    # The real assertion: what came out of Postgres still replays exactly.
    diff = compare(live, run_node(got["recording"]))
    if diff:
        print(f"FAIL: the stored recording does not replay identically\n  {diff}")
        await db.close()
        return 1
    print(f"read back and replayed {len(live)} frames identically")

    async with db._pool.acquire() as conn:               # noqa: SLF001 - test cleanup
        await conn.execute("delete from games where id = $1", game_id)
    print("test row deleted")

    await db.close()
    print("\nPASS: schema, storage and replay all work end to end")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
