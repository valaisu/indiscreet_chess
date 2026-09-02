"""
Geometry parity: every destination web/src/geometry.ts produces must be
accepted by server/rules.py:validate_move.

The two implementations are the same maths in different languages, and when
they disagree the player clicks and nothing happens. The 0.99 / 0.9999
pullback factors in both files exist because this has gone wrong before.

    python -m tools.parity_test [--samples 5000] [--seed 1]

Requires node >= 22.6 (TypeScript is stripped natively; no build step).
"""

import argparse
import json
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from server.pieces import Piece, PieceType, PieceState
from server.rules import validate_move

ROOT = Path(__file__).resolve().parent.parent
GEOMETRY = ROOT / "web" / "src" / "geometry.ts"

SNAP_MAX = 0.625      # mirrors _MOVE_SNAP_MAX in the client
FREEDOM_DEG = 5.0

TYPES = ["pawn", "rook", "knight", "bishop", "queen", "king"]

RUNNER = """
import { snapDestination } from %s;
let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  const cases = JSON.parse(raw);
  const out = cases.map((c) =>
    snapDestination(c.bx, c.by, c.piece, c.freedom, c.pieces)
  );
  process.stdout.write(JSON.stringify(out));
});
"""


def make_case(rng: random.Random) -> dict:
    ptype = rng.choice(TYPES)
    owner = rng.choice(["white", "black"])
    piece = {
        "id": "subject",
        "type": ptype,
        "owner": owner,
        "x": round(rng.uniform(0.6, 7.4), 4),
        "y": round(rng.uniform(0.6, 7.4), 4),
        "state": "idle",
        "has_moved": rng.random() < 0.5,
    }
    others = []
    for i in range(rng.randint(0, 4)):
        others.append({
            "id": f"other{i}",
            "type": rng.choice(TYPES),
            "owner": rng.choice(["white", "black"]),
            "x": round(rng.uniform(0.4, 7.6), 4),
            "y": round(rng.uniform(0.4, 7.6), 4),
            "state": "idle",
            "has_moved": True,
        })
    # Pawn diagonals only open when an enemy sits near the landing circle, so
    # plant one there sometimes or that branch never gets exercised.
    if ptype == "pawn" and rng.random() < 0.6:
        fwd = -1.0 if owner == "white" else 1.0
        others.append({
            "id": "bait",
            "type": "rook",
            "owner": "black" if owner == "white" else "white",
            "x": piece["x"] + rng.choice([-1.0, 1.0]) + rng.uniform(-0.15, 0.15),
            "y": piece["y"] + fwd + rng.uniform(-0.15, 0.15),
            "state": "idle",
            "has_moved": True,
        })
    return {
        "piece": piece,
        "pieces": [piece] + others,
        "bx": round(rng.uniform(0.0, 8.0), 4),
        "by": round(rng.uniform(0.0, 8.0), 4),
        "freedom": FREEDOM_DEG,
    }


def to_piece(d: dict) -> Piece:
    p = Piece(id=d["id"], type=PieceType(d["type"]), owner=d["owner"],
              x=d["x"], y=d["y"])
    p.state = PieceState.IDLE
    p.has_moved = d["has_moved"]
    return p


def run_node(cases: list[dict]) -> list[dict]:
    # The runner goes in a file rather than through stdin, which the cases need.
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(RUNNER % json.dumps(str(GEOMETRY)))
        runner = fh.name
    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", runner],
            input=json.dumps(cases).encode(),
            capture_output=True,
        )
    finally:
        Path(runner).unlink(missing_ok=True)
    if proc.returncode != 0:
        print(proc.stderr.decode()[:2000], file=sys.stderr)
        raise SystemExit("node runner failed")
    return json.loads(proc.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--show", type=int, default=5, help="failures to print")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    cases = [make_case(rng) for _ in range(args.samples)]
    results = run_node(cases)

    checked = skipped = 0
    failures = []

    for case, res in zip(cases, results):
        d = res["d"]
        if d is None or not math.isfinite(d) or d > SNAP_MAX:
            skipped += 1          # client would ignore this click
            continue
        piece = to_piece(case["piece"])
        others = [to_piece(p) for p in case["pieces"] if p["id"] != piece.id]
        reason = validate_move(piece, res["x"], res["y"], [piece] + others,
                               FREEDOM_DEG)
        checked += 1
        if reason is not None:
            failures.append((case, res, reason))

    print(f"samples={args.samples} checked={checked} "
          f"ignored_by_client={skipped} failures={len(failures)}")

    for case, res, reason in failures[: args.show]:
        p = case["piece"]
        print(f"  {p['type']:6s} {p['owner']:5s} at ({p['x']:.3f},{p['y']:.3f}) "
              f"has_moved={p['has_moved']} click=({case['bx']:.3f},{case['by']:.3f}) "
              f"-> ({res['x']:.4f},{res['y']:.4f}) d={res['d']:.4f}: {reason}")

    if failures:
        print(f"FAIL: {len(failures)}/{checked} snapped destinations rejected")
        return 1
    print("PASS: every snapped destination accepted by validate_move")
    return 0


if __name__ == "__main__":
    sys.exit(main())
