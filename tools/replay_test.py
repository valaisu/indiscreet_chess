"""
Replay fidelity: a recorded event log expands back into the frames it was
recorded from.

server/recorder.py writes down only what changed; web/src/expand.ts rebuilds
every tick from that. If the two disagree, a stored game replays as a game
nobody played, and there is nothing on screen to say so. This plays real games
under the real engine, then asserts the expansion is frame-for-frame identical
to what the server actually broadcast.

The expander is only written once, in TypeScript, because the browser is what
has to run it; node strips the types natively, so this drives it directly.

    python -m tools.replay_test [--games 5] [--ticks 4000] [--seed 1]

Requires node >= 22.6.
"""

import argparse
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from server import civs, params
from server.game import GameState
from server.recorder import Recorder
from server.pieces import Piece, PieceState, PieceType

ROOT = Path(__file__).resolve().parent.parent
EXPAND = ROOT / "web" / "src" / "expand.ts"

RUNNER = """
import { expand } from %s;
let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  process.stdout.write(JSON.stringify(expand(JSON.parse(raw))));
});
"""


def run_node(log: dict) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(RUNNER % json.dumps(str(EXPAND)))
        runner = fh.name
    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", runner],
            input=json.dumps(log).encode(),
            capture_output=True,
        )
    finally:
        Path(runner).unlink(missing_ok=True)
    if proc.returncode != 0:
        print(proc.stderr.decode()[:3000], file=sys.stderr)
        raise SystemExit("node runner failed")
    return json.loads(proc.stdout)


def play(rng: random.Random, max_ticks: int,
         civ_pair: tuple[str | None, str | None] = (None, None)) -> tuple[GameState, list[dict]]:
    """Play a game of random legal moves, keeping every frame the server would
    have broadcast. Random play is what gets captures, promotions, castling and
    en passant into the log without scripting each one by hand.

    Civilizations matter here beyond flavour: one may size a piece type
    differently from its owner's default, so a pawn promoting mid-flight
    changes hitbox, and the two sides run on different speeds and cooldowns."""
    white, black = civ_pair
    game = GameState(params_white=civs.resolve(None, white),
                     params_black=civs.resolve(None, black),
                     civs={"white": white, "black": black})
    game.recorder.start(params.TICK_RATE)
    game.started = True
    dt = 1.0 / params.TICK_RATE
    s = params.SQUARE_SIDE
    # The opening position, tick 0. The live server broadcasts this during the
    # countdown, before any tick has run, and the expander reproduces it from
    # the log header, so the comparison starts there.
    frames: list[dict] = [game.to_dict(None)]

    for i in range(max_ticks):
        # Only finished games are ever recorded, so a game still going at the
        # tick limit is resigned rather than cut off. That also exercises the
        # forfeit path, which is how most real games end.
        if i == max_ticks - 1 and not game.game_over:
            game.forfeit(rng.choice(["white", "black"]))

        # A few move attempts per tick. Most bounce off validate_move, which is
        # fine: the accepted ones are a legal game.
        for _ in range(3):
            idle = [p for p in game.pieces
                    if p.state == PieceState.IDLE and p.type != PieceType.GHOST]
            if not idle:
                break
            piece = rng.choice(idle)
            # Bias forward and long: pawns have to reach the far rank for a
            # promotion to ever appear, and kings have to travel two squares
            # sideways for castling.
            fwd = -1.0 if piece.owner == "white" else 1.0
            if piece.type == PieceType.PAWN and rng.random() < 0.7:
                dx = rng.choice([0.0, 0.0, -s, s]) * rng.uniform(0.9, 1.0)
                dy = fwd * s * rng.choice([1.0, 2.0])
            elif piece.type == PieceType.KING and rng.random() < 0.4:
                dx, dy = rng.choice([-2.0 * s, 2.0 * s]), 0.0
            else:
                dx = rng.uniform(-3.0, 3.0) * s
                dy = rng.uniform(-3.0, 3.0) * s
            if game.queue_move(piece.id, (piece.x + dx, piece.y + dy),
                               piece.owner) is None:
                break

        game._tick(dt)
        frames.append(game.to_dict(None))
        if game.game_over:
            break

    game.recorder.finish(game)
    return game, frames


def scenario_capture_continue() -> tuple[GameState, list[dict]]:
    """A rook takes a pawn in mid-flight and carries on.

    physics._continue_after_capture rewrites the mover's destination and its
    arrival time while leaving its state, position and velocity alone, so a
    recorder watching only for state changes sees nothing and the replay flies
    on to a destination the piece abandoned. Random play reaches this often but
    not reliably, and the window before the arrival corrects it is one or two
    ticks, so it gets its own game rather than being left to chance.
    """
    game = GameState()
    s = params.SQUARE_SIDE

    def at(pid, kind, owner, x, y):
        pc = Piece(id=pid, type=kind, owner=owner, x=x, y=y)
        pc.diameter = game._pf(pc)["diameter_piece"]
        return pc

    # Kings kept well apart so the game does not end while this plays out; a
    # board with no kings at all is an immediate draw.
    game.pieces = [
        at("w_rook_0",  PieceType.ROOK, "white", 0.5 * s, 4.5 * s),
        at("b_pawn_3",  PieceType.PAWN, "black", 3.5 * s, 4.5 * s),
        at("w_king_4",  PieceType.KING, "white", 4.5 * s, 7.5 * s),
        at("b_king_4",  PieceType.KING, "black", 4.5 * s, 0.5 * s),
    ]
    game.recorder = Recorder(game)
    game.recorder.start(params.TICK_RATE)
    game.started = True

    dt = 1.0 / params.TICK_RATE
    frames = [game.to_dict(None)]
    rejected = game.queue_move("w_rook_0", (7.5 * s, 4.5 * s), "white")
    if rejected:
        raise SystemExit(f"scenario setup failed: {rejected['reason']}")

    for i in range(200):
        if i == 199 and not game.game_over:
            game.forfeit("black")
        game._tick(dt)
        frames.append(game.to_dict(None))
        if game.game_over:
            break

    if any(p["id"] == "b_pawn_3" for p in frames[-1]["pieces"]):
        raise SystemExit("scenario did not capture the pawn; it proves nothing")

    game.recorder.finish(game)
    return game, frames


def compare(live: list[dict], back: list[dict]) -> str | None:
    """First difference between the broadcast frames and the expanded ones."""
    if len(back) < len(live):
        return f"expanded {len(back)} frames, server broadcast {len(live)}"

    for i, want in enumerate(live):
        got = back[i]
        if got["tick"] != want["tick"]:
            return f"frame {i}: tick {got['tick']} != {want['tick']}"
        if [p["id"] for p in got["pieces"]] != [p["id"] for p in want["pieces"]]:
            missing = {p["id"] for p in want["pieces"]} - {p["id"] for p in got["pieces"]}
            extra = {p["id"] for p in got["pieces"]} - {p["id"] for p in want["pieces"]}
            return (f"tick {want['tick']}: piece list differs "
                    f"(missing {sorted(missing)}, extra {sorted(extra)})")
        for a, b in zip(want["pieces"], got["pieces"]):
            for field in ("type", "owner", "x", "y", "state", "state_timer",
                          "dest_x", "dest_y", "vel_x", "vel_y", "has_moved", "d"):
                if a[field] != b[field]:
                    return (f"tick {want['tick']} piece {a['id']}: "
                            f"{field} {b[field]!r} != {a[field]!r}")
        for c in ("white", "black"):
            if want["mana"][c] != got["mana"][c]:
                return (f"tick {want['tick']}: {c} mana "
                        f"{got['mana'][c]} != {want['mana'][c]}")
        if want["game_over"] != got["game_over"] or want["winner"] != got["winner"]:
            return (f"tick {want['tick']}: result "
                    f"{got['game_over']}/{got['winner']} != "
                    f"{want['game_over']}/{want['winner']}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=5)
    ap.add_argument("--ticks", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    # The first game is plain; the rest pair civilizations off against each
    # other, including a civ against itself and a civ against none.
    pairs: list[tuple[str | None, str | None]] = [(None, None)]
    for i in range(args.games - 1):
        a = civs.CIV_NAMES[i % len(civs.CIV_NAMES)]
        b = civs.CIV_NAMES[(i * 3 + 1) % len(civs.CIV_NAMES)] if i % 3 else None
        pairs.append((a, b))

    failures = 0

    game, live = scenario_capture_continue()
    log = game.recorder.to_dict()
    diff = compare(live, run_node(log))
    print(f"scenario: mid-flight capture   {len(live):5d} ticks  "
          f"{len(log['events']):5d} events  {'ok' if diff is None else 'FAIL'}")
    if diff:
        print(f"    {diff}")
        failures += 1

    for n in range(args.games):
        rng = random.Random(args.seed + n)
        game, live = play(rng, args.ticks, pairs[n])
        log = game.recorder.to_dict()
        raw = json.dumps(log, allow_nan=False)
        back = run_node(log)

        snapshot_bytes = len(json.dumps(live[0])) * len(live)
        diff = compare(live, back)
        status = "ok" if diff is None else "FAIL"
        label = "/".join(c or "none" for c in pairs[n])
        print(f"game {n + 1}: {label:<20s} {len(live):5d} ticks  "
              f"{len(log['events']):5d} events  "
              f"{len(raw) / 1024:7.1f} KB log  "
              f"vs {snapshot_bytes / 1e6:5.1f} MB of snapshots  "
              f"winner={game.winner}  {status}")
        if diff:
            print(f"    {diff}")
            failures += 1

    if failures:
        print(f"FAIL: {failures}/{args.games} recordings did not replay identically")
        return 1
    print("PASS: every recording expands to the frames it was recorded from")
    return 0


if __name__ == "__main__":
    sys.exit(main())
