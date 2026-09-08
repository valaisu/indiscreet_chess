"""
Stress test. Two modes, because the two numbers that decide capacity are
measured in different places.

    python tools/stress.py --bench

        No network, no clients. Times the work one game costs the server for
        one tick: advance the board, build a snapshot per seat, serialise it,
        deflate it. Turns that into "games one core can carry at 20 Hz".
        It imports `server` and `shared` and nothing else, so it also runs on
        the deployed machine, which is the only place the speed of the real
        hardware can be measured:

            fly ssh sftp shell -a indiscreet-chess-valaisu
                put tools/stress.py /app/stress.py
            fly ssh console -a indiscreet-chess-valaisu -C "python /app/stress.py --bench"

    PYTHONPATH=. python tools/stress.py --load --rooms 1,2,4,6,8

        Real clients on a real socket, one pair per room, ramped through each
        room count in turn. Reports what a player would feel: the tick rate
        the server actually sustained, how late frames arrived, and how long
        the server took to answer a PING. Add --server-pid to read the
        server's own CPU out of /proc while it runs.

Why the tick rate is the headline number: `GameState.run` advances a fixed dt
per tick and paces itself with sleep, so a server that cannot keep up does not
drop frames, it runs every game on the machine in slow motion. A client cannot
see that in its own frame count either, only in how far the server's `tick`
counter advanced per wall second, which is what this measures.

A bot game that finishes is stored like any other game: the server saves every
finished game, and these are anonymous ones. Against a server with a database
behind it, expect a few bot games in the table, or keep --duration short enough
that none of them end.

Against the deployed server one source address can only reach two games:
main.MAX_CONN_PER_IP is 5 sockets and RoomManager.MAX_ROOMS_PER_IP is 3 rooms.
A larger production ramp needs those raised for the run, or load from several
addresses. The tool says so rather than reporting the refusal as a result.
"""

import argparse
import asyncio
import json
import math
import os
import random
import resource
import statistics
import sys
import time
import zlib

if __package__ is None:                 # python tools/stress.py, and /app/stress.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import params
from server.game import GameState
from server.room import dump

# An unloaded server does not reach 20.0 ticks/s: asyncio's sleep overshoots a
# little every tick, and the shortfall is the machine's, not the load's. So a
# level is judged against the lightest level of the same run rather than
# against 20, and slow motion is what the comparison is called: the loop
# advances a fixed dt, so a server that cannot keep up runs every game slowly
# instead of dropping frames.
MAX_SLOWDOWN = 0.03
HEALTHY_PING_P95_MS = 100.0
# Below this a seat spent most of the window outside a game, and a --bench
# level that slow is already saturated.
HEALTHY_TICK_RATE = 19.0

# There is no socket correction here on purpose. Ramping both modes on one
# machine put the ceilings one level apart (--bench 4.1% slow at 16 games,
# --load 3.6% at 16 rooms), so the sockets are not what runs out: the snapshot
# work is. An earlier version scaled the bench by 1.8, from comparing a single
# game in each mode, and that factor was the clock and the caches rather than
# the sockets. Per-game cost is not a constant, so only ceilings compare.


def pct(values: list[float], q: float) -> float:
    """One percentile without a dependency. Empty is nan rather than an
    exception: a level that collected nothing still has to print its row."""
    if not values:
        return math.nan
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


# ----------------------------------------------------------------------------
# Bench: what one game costs the machine this runs on
# ----------------------------------------------------------------------------

def _random_moves(game: GameState, per_side: int) -> None:
    for color in ("white", "black"):
        pieces = [p for p in game.pieces if p.owner == color]
        random.shuffle(pieces)
        for piece in pieces[:per_side]:
            game.queue_move(piece.id,
                            (piece.x + random.uniform(-2.0, 2.0),
                             piece.y + random.uniform(-2.0, 2.0)),
                            color)


def process_cpu() -> float:
    """This process's own user + system seconds."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


async def bench_level(games: int, seconds: int, seed: int) -> dict:
    """Run real `GameState.run` loops with a callback that does everything the
    room does except touch a socket, and read this process's own CPU.

    Timing the same work in a tight loop instead measures the wrong machine:
    the server ticks at a 5% duty cycle, so its caches are cold and its clock
    is not boosted, and a tight loop reported a third of the true cost.
    """
    random.seed(seed)
    frames: list[int] = []
    wire: list[int] = []
    counted = [0]
    running: list[GameState] = []
    tasks = []

    for _ in range(games):
        game = GameState()
        deflate = {c: zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
                   for c in ("white", "black")}
        ticks = [0]

        async def broadcast(state: GameState, deflate=deflate, ticks=ticks) -> None:
            if state.countdown is not None:
                return
            ticks[0] += 1
            # Mana refills at 0.3/s and a move costs at least 1.0, so a player
            # cannot start more than about one move every four seconds. Three
            # pieces a side that often is a busy game, not a bot flood.
            if ticks[0] % (params.TICK_RATE * 4) == 0:
                _random_moves(state, 3)
            for color, zip_ in deflate.items():
                data = dump(state.to_dict(color)).encode()
                packed = zip_.compress(data) + zip_.flush(zlib.Z_SYNC_FLUSH)
                if color == "white":
                    frames.append(len(data))
                    wire.append(len(packed))
            counted[0] += 1

        running.append(game)
        tasks.append(asyncio.create_task(game.run(broadcast)))

    await asyncio.sleep(4.0)            # the countdown is 3.5 s and broadcasts at 1 Hz
    frames.clear(); wire.clear(); counted[0] = 0
    start_cpu, start_tick = process_cpu(), [g.tick for g in running]
    t0 = time.monotonic()
    await asyncio.sleep(seconds)
    wall = time.monotonic() - t0
    cpu = process_cpu() - start_cpu
    ticked = min(g.tick - t for g, t in zip(running, start_tick))

    for game in running:
        game.game_over = True
    await asyncio.gather(*tasks, return_exceptions=True)

    return {
        "games": games,
        "cpu": 100.0 * cpu / wall,
        "per_game": 100.0 * cpu / wall / games,
        "rate": ticked / wall,
        "raw": statistics.mean(frames) if frames else math.nan,
        "wire": statistics.mean(wire) if wire else math.nan,
    }


async def bench(args) -> int:
    levels = [int(n) for n in args.games.split(",")]
    print(f"python     {sys.version.split()[0]} on {os.uname().machine}, "
          f"{os.cpu_count()} cpu(s) visible")
    print(f"per level  {args.seconds} s of real 20 Hz game loops, no sockets")
    print()
    print("games   cpu   per game   tick/s    slow")
    rows: list[dict] = []
    baseline = None
    stopped = False
    for games in levels:
        row = await bench_level(games, args.seconds, args.seed)
        if baseline is None:
            baseline = row["rate"]
        row["slow"] = max(0.0, 1.0 - row["rate"] / baseline)
        rows.append(row)
        print(f"{row['games']:5d} {row['cpu']:5.0f}% {row['per_game']:9.2f}% "
              f"{row['rate']:8.1f} {100 * row['slow']:6.1f}%")
        if row["cpu"] > 90.0 or row["slow"] > MAX_SLOWDOWN:
            stopped = True
            break

    held = [r for r in rows if r["slow"] <= MAX_SLOWDOWN]
    basis = max(held, key=lambda r: r["games"]) if held else rows[0]
    over = [r for r in rows if r["slow"] > MAX_SLOWDOWN]
    print()
    print(f"frame      {basis['raw']:.0f} B raw, {basis['wire']:.0f} B deflated, "
          f"{basis['wire'] * params.TICK_RATE / 1000:.1f} KB/s per client")
    print(f"held       {basis['games']} game(s) ({2 * basis['games']} players) within "
          f"{100 * MAX_SLOWDOWN:.0f}% of real time, at {basis['cpu']:.0f}% of a core")
    if over:
        first = min(over, key=lambda r: r["games"])
        print(f"lost it at {first['games']} game(s): {100 * first['slow']:.0f}% slow "
              f"motion, which every game on the machine feels at once")
    elif not stopped:
        print("           the list ran out before the machine did: ask for more games")
    else:
        print(f"           stopped at {basis['cpu']:.0f}% of a core, "
              f"with the tick rate still holding")
    print()
    print("The server is one thread, so more cores do not raise this. The board is")
    print("full at 32 pieces, which is the expensive case: captures make frames")
    print("smaller. Sockets are not corrected for and do not need to be: --load on")
    print("the same machine reached the same ceiling.")
    return 0


# ----------------------------------------------------------------------------
# Load: what clients actually receive
# ----------------------------------------------------------------------------

class Seat:
    """One socket playing one side, recording when each frame arrived.

    Every non-countdown frame is stored as (arrival, server tick). The
    measurement window is applied afterwards rather than by a flag flipped
    mid-run, so nothing has to be coordinated across tasks, and a room that
    restarts inside the window is handled by segmenting on the tick counter
    going backwards.
    """

    def __init__(self, url: str, origin: str | None, move_interval: float) -> None:
        self.url = url
        self.origin = origin
        self.move_interval = move_interval
        self.color: str | None = None
        self.code: str | None = None
        self.state: dict | None = None
        self.errors: list[str] = []
        self.joined = asyncio.Event()
        self.game_over = asyncio.Event()
        self.ws = None
        self.frames: list[tuple[float, int]] = []    # (monotonic, server tick)
        self.rtts: list[tuple[float, float]] = []    # (monotonic, ms)
        self.accepted = 0
        self.rejected = 0
        self.restarts = 0

    async def send(self, msg: dict) -> None:
        try:
            await self.ws.send(json.dumps(msg))
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
            self.game_over.set()

    async def _read(self) -> None:
        from shared import protocol
        async for raw in self.ws:
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == protocol.GAME_STATE:
                self.state = msg
                if msg.get("countdown") is None:
                    self.frames.append((time.monotonic(), msg.get("tick", 0)))
                if msg.get("game_over"):
                    self.game_over.set()
            elif kind == protocol.PONG:
                now = time.monotonic()
                self.rtts.append((now, (now - msg["t"]) * 1000.0))
            elif kind in (protocol.ROOM_CREATED, protocol.ROOM_JOINED):
                self.code = msg.get("code")
                self.color = msg.get("color")
                # Rooms do not start until both seats confirm.
                await self.send({"type": protocol.SET_READY, "ready": True,
                                 "civ": None})
                self.joined.set()
            elif kind == protocol.MOVE_REJECTED:
                self.rejected += 1
            elif kind == protocol.ERROR:
                self.errors.append(msg.get("reason", "?"))
                self.joined.set()
                self.game_over.set()
            elif kind == protocol.OPPONENT_LEFT:
                self.game_over.set()

    async def _ping(self) -> None:
        from shared import protocol
        while True:
            await asyncio.sleep(1.0)
            await self.send({"type": protocol.PING, "t": time.monotonic()})

    async def _move(self) -> None:
        from shared import protocol
        from tools.fake_client import legal_destination
        while True:
            await asyncio.sleep(self.move_interval)
            state = self.state
            if not state or state.get("countdown") is not None:
                continue
            mine = [p for p in state["pieces"]
                    if p["owner"] == self.color and p["state"] == "idle"
                    and p["type"] != "ghost"]
            if not mine:
                continue
            piece = random.choice(mine)
            dest = legal_destination(piece)
            if dest is None:
                continue
            await self.send({"type": protocol.QUEUE_MOVE, "piece_id": piece["id"],
                             "destination": list(dest)})
            self.accepted += 1

    def reset(self) -> None:
        """Clear what belongs to the last game. The caller does this before the
        task starts, not the task itself: a coroutine clears its event only
        once it is scheduled, and until then `joined` is still set from the
        previous game, so the guest was handed the code of a room that had
        already started. The server said "game already started" and the pair
        died, quietly lowering the load a level claimed to be applying."""
        self.joined.clear()
        self.game_over.clear()
        self.state = None
        self.code = None

    async def play(self, action: str, code: str | None, deadline: float) -> None:
        """Sit in one game until it ends or the run does."""
        import websockets
        from shared import protocol
        opener = (websockets.connect(self.url, origin=self.origin) if self.origin
                  else websockets.connect(self.url))
        try:
            async with opener as ws:
                self.ws = ws
                if action == "create":
                    await self.send({"type": protocol.CREATE_ROOM, "params": {}})
                else:
                    await self.send({"type": protocol.JOIN_ROOM, "code": code})
                tasks = [asyncio.create_task(coro) for coro in
                         (self._read(), self._ping(), self._move())]
                try:
                    await asyncio.wait_for(
                        self.game_over.wait(),
                        timeout=max(0.1, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    pass
                finally:
                    for task in tasks:
                        task.cancel()
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            self.joined.set()
            self.game_over.set()

    # -- what the recording adds up to ---------------------------------------

    def measure(self, since: float) -> tuple[float, list[float]]:
        """Ticks per wall second, and the gaps between frames, from `since`.

        Segmented on the tick counter: a pair that finishes a game and opens a
        new room starts counting from zero again, and that reset is not a
        seven hundred tick stall.
        """
        window = [f for f in self.frames if f[0] >= since]
        ticks = seconds = 0.0
        gaps: list[float] = []
        for (t0, tick0), (t1, tick1) in zip(window, window[1:]):
            if tick1 < tick0:
                continue
            ticks += tick1 - tick0
            seconds += t1 - t0
            gaps.append((t1 - t0) * 1000.0)
        return (ticks / seconds if seconds else math.nan), gaps


# A refusal that will not go away on the next attempt: the address has hit a
# limit, and retrying only makes the log longer.
FATAL_ERRORS = ("too many", "room is full")


async def play_pair(host: Seat, guest: Seat, deadline: float) -> None:
    """One room, replayed until the run ends.

    Bots do finish games, and a pair that stopped when its game ended would
    quietly lower the load the level claims to apply. The same goes for a
    transient refusal, so those are counted and retried; only an address limit
    ends the pair, because that one will refuse the next attempt too.
    """
    while time.monotonic() < deadline:
        host.reset()
        guest.reset()
        before = len(host.errors) + len(guest.errors)
        host_task = asyncio.create_task(host.play("create", None, deadline))
        try:
            await asyncio.wait_for(host.joined.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            host.errors.append("no reply to CREATE_ROOM")
        if host.code is not None:
            guest_task = asyncio.create_task(guest.play("join", host.code, deadline))
            await asyncio.gather(host_task, guest_task)
        else:
            await host_task

        new = (host.errors + guest.errors)[before:]
        if any(any(f in e for f in FATAL_ERRORS) for e in new):
            return
        if new:
            host.restarts += 1
            await asyncio.sleep(0.5)      # do not spin on a server that is refusing


def read_cpu(pid: int) -> float:
    """utime + stime in seconds from /proc. Linux, and only meaningful when the
    server is on this machine."""
    with open(f"/proc/{pid}/stat") as fh:
        fields = fh.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


async def run_level(args, rooms: int) -> dict:
    deadline = time.monotonic() + args.duration
    seats = [(Seat(args.url, args.origin, args.move_interval),
              Seat(args.url, args.origin, args.move_interval))
             for _ in range(rooms)]
    running = asyncio.gather(*[play_pair(h, g, deadline) for h, g in seats])

    # Connecting, readying and the 3-2-1 countdown are setup, not load.
    await asyncio.sleep(args.warmup)
    since = time.monotonic()
    cpu0 = read_cpu(args.server_pid) if args.server_pid else 0.0
    gen0 = process_cpu()
    await running
    cpu = read_cpu(args.server_pid) - cpu0 if args.server_pid else math.nan
    gen = process_cpu() - gen0
    wall = time.monotonic() - since

    flat = [s for pair in seats for s in pair]
    rates, gaps, rtts = [], [], []
    live = 0
    for seat in flat:
        rate, seat_gaps = seat.measure(since)
        if not math.isnan(rate):
            rates.append(rate)
        gaps += seat_gaps
        rtts += [ms for t, ms in seat.rtts if t >= since]
        # A seat that spent most of the window outside a game was not applying
        # the load this row is named after, so the row has to say so.
        if len([f for f in seat.frames if f[0] >= since]) >= 0.5 * HEALTHY_TICK_RATE * wall:
            live += 1
    errors = sorted({e for s in flat for e in s.errors})
    return {
        "rooms": rooms,
        "clients": len(flat),
        "live": live / 2.0,
        "restarts": sum(s.restarts for s in flat),
        "rate_med": statistics.median(rates) if rates else math.nan,
        "gap_p50": pct(gaps, 0.50), "gap_p95": pct(gaps, 0.95),
        "gap_p99": pct(gaps, 0.99),
        "ping_p50": pct(rtts, 0.50), "ping_p95": pct(rtts, 0.95),
        "cpu": 100.0 * cpu / wall if wall and not math.isnan(cpu) else math.nan,
        # Every bot in this run shares one process and one thread. If that
        # thread is the one that is saturated, the row measures the load
        # generator, not the server.
        "gen": 100.0 * gen / wall if wall else math.nan,
        "moves": sum(s.accepted for s in flat),
        "rejected": sum(s.rejected for s in flat),
        "errors": errors,
    }


def print_header(args) -> None:
    print(f"url            {args.url}")
    print(f"per level      {args.duration:.0f} s, first {args.warmup:.0f} s ignored")
    print(f"move interval  {args.move_interval:.1f} s per seat")
    print()
    print("An unloaded server reports about 19.6 ticks/s, not 20.0: asyncio's sleep")
    print("overshoots every tick. The first level is the baseline; slow is measured")
    print("against it, and is how much slower than real time every game on the")
    print("machine ran. `live` is how many rooms were actually in a game.")
    print()
    print("rooms live  tick/s   slow   gap p95   p99    ping p50    p95"
          + ("     cpu" if args.server_pid else "") + "     gen")


def print_row(row: dict, with_cpu: bool) -> None:
    slow = row.get("slow")
    slow_txt = "  base" if slow is None else f"{100 * slow:5.1f}%"
    line = (f"{row['rooms']:5d} {row['live']:4.0f}  {row['rate_med']:6.2f}  "
            f"{slow_txt}  {row['gap_p95']:8.0f} {row['gap_p99']:5.0f}  "
            f"{row['ping_p50']:9.1f} {row['ping_p95']:6.1f}")
    if with_cpu:
        line += f"  {row['cpu']:6.0f}%"
    line += f"  {row['gen']:6.0f}%"
    print(line)
    notes = []
    if row["restarts"]:
        notes.append(f"{row['restarts']} room restart(s)")
    if row["live"] < row["rooms"]:
        notes.append(f"only {row['live']:.0f} of {row['rooms']} rooms were playing")
    if row["errors"]:
        notes.append("errors: " + ", ".join(row["errors"]))
    for note in notes:
        print(f"            {note}")


def healthy(row: dict) -> bool:
    """A level nobody would notice: the games ran at close to real time, the
    server answered promptly, and every room the level asked for was playing."""
    return ((row.get("slow") or 0.0) <= MAX_SLOWDOWN
            and not (row["ping_p95"] > HEALTHY_PING_P95_MS)
            and row["live"] >= row["rooms"])


async def load(args) -> int:
    levels = sorted(int(n) for n in args.rooms.split(","))
    print_header(args)
    rows: list[dict] = []
    baseline = None
    for rooms in levels:
        row = await run_level(args, rooms)
        # The lightest level is the baseline: it measures this machine and this
        # network with nothing to contend with.
        if baseline is None:
            baseline = row["rate_med"]
        else:
            row["slow"] = max(0.0, 1.0 - row["rate_med"] / baseline)
        rows.append(row)
        print_row(row, args.server_pid is not None)
        if any("too many" in e or "full" in e for e in row["errors"]):
            print()
            print("The server's per-address limits stopped this level: 5 sockets and 3")
            print("rooms per address. Start the server under test with --max-conn-per-ip")
            print("and --max-rooms-per-ip raised, or drive it from several addresses.")
            break
        await asyncio.sleep(2.0)      # let the seats be released before the next level

    print()
    good = [r for r in rows if healthy(r)]
    bad = [r for r in rows if not healthy(r)]
    if good:
        best = max(good, key=lambda r: r["rooms"])
        print(f"Healthy through {best['rooms']} concurrent game(s), "
              f"{best['clients']} players.")
    else:
        print("No level was healthy. The first row is already degraded.")
    if bad:
        first = min(bad, key=lambda r: r["rooms"])
        print(f"First degraded at {first['rooms']} game(s): "
              f"{100 * (first.get('slow') or 0.0):.0f}% slow motion, "
              f"ping p95 {first['ping_p95']:.0f} ms"
              + (f", {first['live']:.0f} of {first['rooms']} rooms playing"
                 if first["live"] < first["rooms"] else "") + ".")
    else:
        print("Nothing degraded: the ramp did not reach this server's limit.")

    hot = [r for r in rows if r["gen"] > 80.0]
    if hot:
        print()
        print(f"Warning: from {min(h['rooms'] for h in hot)} room(s) on, this tool's "
              f"own process used more than 80% of a core.")
        print("Those rows may be measuring the load generator rather than the server.")
        print("Split the bots across processes or machines before believing them.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", action="store_true",
                    help="measure this machine's cost per game, no network")
    ap.add_argument("--games", default="2,4,8,12,16,20,24,28,32",
                    help="game counts for --bench to ramp through")
    ap.add_argument("--seconds", type=int, default=10,
                    help="seconds per --bench level")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--load", action="store_true", help="drive real clients")
    ap.add_argument("--url", default="ws://localhost:8765")
    ap.add_argument("--origin", default=None,
                    help="a deployed server restricts Origin and non-browser "
                         "clients send none, so production runs need this")
    ap.add_argument("--rooms", default="1",
                    help="room counts to ramp through, e.g. 1,2,4,8")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="seconds per level, warmup included")
    ap.add_argument("--warmup", type=float, default=6.0,
                    help="seconds to ignore while rooms connect and count down")
    ap.add_argument("--move-interval", type=float, default=4.0,
                    help="seconds between one seat's moves. The default is what "
                         "mana allows a human; lower it to load the message path")
    ap.add_argument("--server-pid", type=int, default=None,
                    help="sample this pid's CPU from /proc (local runs only)")
    args = ap.parse_args()

    if args.bench:
        return asyncio.run(bench(args))
    if not args.load:
        ap.error("choose --bench or --load")
    return asyncio.run(load(args))


if __name__ == "__main__":
    sys.exit(main())
