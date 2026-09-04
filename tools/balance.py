"""
Civilization win rates, from the games actually played.

    .venv/bin/python -m tools.balance                 (needs DATABASE_URL)
    .venv/bin/python -m tools.balance --tempo rapid
    .venv/bin/python -m tools.balance --all-tables    (ignore rebalances)

Three things are deliberately excluded, because each would make the number mean
something other than "is this civilization too strong":

  - solo games: one person playing both sides is not evidence about anything;
  - custom tempos: the settings are the variable, not the civilization;
  - games played under a different version of the civilization table, which is
    what `civ_table` records. Averaging across a rebalance hides exactly the
    change the rebalance was making.

The last one is why win rates for the current table can look thin at first.
That is honest: they are thin, until enough games are played under it.
"""

import argparse
import asyncio
import math
import os
import sys
from pathlib import Path

from server import civs, db

ROOT = Path(__file__).resolve().parent.parent


def wilson(wins: float, n: int) -> tuple[float, float]:
    """95% confidence interval for a proportion.

    A bare win rate off nine games invites conclusions it cannot support. The
    interval is what says "we do not know yet", which for a hobby project's
    first weeks is the honest reading almost everywhere.
    """
    if n == 0:
        return (0.0, 1.0)
    z = 1.96
    p = wins / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - spread), min(1.0, centre + spread))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tempo", help="bullet, rapid or slow; default is all three")
    ap.add_argument("--all-tables", action="store_true",
                    help="include games from before the current civ table, "
                         "which mixes pre- and post-rebalance results")
    ap.add_argument("--rated-only", action="store_true")
    args = ap.parse_args()

    db.load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set")
        return 2
    await db.connect(url)

    table = civs.table_fingerprint()
    where = ["not solo", "tempo <> 'custom'"]
    params: list = []
    if not args.all_tables:
        params.append(table)
        where.append(f"civ_table = ${len(params)}")
    if args.tempo:
        params.append(args.tempo)
        where.append(f"tempo = ${len(params)}")
    if args.rated_only:
        where.append("rated")

    async with db._pool.acquire() as conn:            # noqa: SLF001
        rows = await conn.fetch(f"""
            select white_civ, black_civ, winner, tempo from games
            where {' and '.join(where)}
        """, *params)
    await db.close()

    print(f"civ table {table}"
          f"{' (ignored)' if args.all_tables else ''}, "
          f"{len(rows)} game(s)"
          f"{f' at {args.tempo}' if args.tempo else ''}"
          f"{', rated only' if args.rated_only else ''}")
    if not rows:
        print("\nNothing to report yet.")
        return 0

    # A civilization's record is its games from both seats. Keeping the seat
    # split visible matters: white moving first is an advantage that has
    # nothing to do with the civilization, and a civ that happened to be
    # picked more often by whoever opened the room would otherwise look strong.
    played: dict[str, int] = {}
    won: dict[str, float] = {}
    as_white: dict[str, int] = {}
    white_wins = draws = 0

    for r in rows:
        if r["winner"] == "draw":
            draws += 1
        elif r["winner"] == "white":
            white_wins += 1
        for seat in ("white", "black"):
            civ = r[f"{seat}_civ"] or "none"
            played[civ] = played.get(civ, 0) + 1
            as_white[civ] = as_white.get(civ, 0) + (seat == "white")
            if r["winner"] == seat:
                won[civ] = won.get(civ, 0) + 1
            elif r["winner"] == "draw":
                won[civ] = won.get(civ, 0) + 0.5

    decided = len(rows) - draws
    print(f"white won {white_wins} of {decided} decided "
          f"({white_wins / decided:.0%})" if decided else "no decided games")
    print(f"\n{'civ':<11}{'games':>6}{'as white':>10}{'win rate':>10}"
          f"{'  95% interval':>16}")
    print("-" * 55)
    for civ in sorted(played, key=lambda c: -(won.get(c, 0) / played[c])):
        n, w = played[civ], won.get(civ, 0.0)
        lo, hi = wilson(w, n)
        print(f"{civ:<11}{n:>6}{as_white.get(civ, 0):>10}"
              f"{w / n:>9.0%}{f'  {lo:.0%} to {hi:.0%}':>16}")

    thin = [c for c, n in played.items() if n < 30]
    if thin:
        print(f"\n{len(thin)} civilization(s) under 30 games: "
              f"{', '.join(sorted(thin))}.")
        print("The intervals above are wide for a reason; do not rebalance on these.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
