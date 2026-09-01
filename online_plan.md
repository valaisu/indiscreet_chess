# Online Deployment Plan

Goal: a single always-on server on the public internet, hosting many concurrent
games, that strangers can connect to and play against each other.

Scope note: this plan keeps the **pygame desktop client**. A browser client is a
separate project (see Appendix B) — every server change below is identical
either way, so the client decision can be deferred.

---

## 1. What changes and what doesn't

**Untouched** (~1100 lines): `server/physics.py`, `server/rules.py`,
`server/pieces.py`, and nearly all of `server/game.py`. The rules engine is
already server-authoritative and location-agnostic.

**Rewritten**: `server/main.py` — currently a single-game process, becomes a
room-hosting service.

**Restructured**: `client/menu.py` and the connection setup in `client/main.py`
— the lobby connection must outlive the menu and carry into the game.

**Deleted**: UDP LAN discovery (`server/main.py:106-143`, `menu.py:85-115`) is
kept for LAN mode but plays no part online.

Current architecture assumption being broken: *one process = one game, params
fixed at spawn time via CLI args, client picks its own color.*

---

## 2. Architecture

```
                    Internet
                       |
                  Caddy (TLS)          wss://play.example.com
                       |
              server/main.py  (one process)
                       |
        +--------------+--------------+
        |              |              |
     Room ABCD      Room WXYZ      Room ...
     GameState      GameState
     white: ws      white: ws
     black: ws      black: ws
```

One process, one asyncio loop, N rooms. Each room owns a `GameState` and a
task running `GameState.run(room.broadcast)` — that callback signature already
exists (`server/game.py:106`), so the game loop itself needs no change.

**Capacity estimate.** A tick is O(pieces²) collision work on ≤32 pieces at
20 Hz — microseconds. The binding constraint is egress: ~1 Mbps per client
(§7). A 1-vCPU VPS handles dozens of rooms on CPU; budget by bandwidth, not
compute.

---

## 3. Protocol additions

`shared/protocol.py` gains lobby messages. All existing in-game messages
(`QUEUE_MOVE`, `GAME_STATE`, `MOVE_REJECTED`, `GAME_OVER`) are unchanged.

| Dir | Message | Payload |
|---|---|---|
| C→S | `CREATE_ROOM` | `params` / `params_white` + `params_black`, `handicap` |
| S→C | `ROOM_CREATED` | `code`, `color`, `token` |
| C→S | `JOIN_ROOM` | `code` |
| S→C | `ROOM_JOINED` | `code`, `color`, `token` |
| C→S | `QUICK_MATCH` | `params` (used if this client opens the room) |
| S→C | `ROOM_STATE` | `players`, `waiting` |
| C→S | `REJOIN` | `code`, `token` |
| S→C | `OPPONENT_LEFT` | `grace_seconds` |
| S→C | `OPPONENT_REJOINED` | — |
| C→S | `PING` | `t` |
| S→C | `PONG` | `t`, `server_time` |

**Color is assigned by the server**, never claimed by the client. Room creator
gets white; the joiner gets black. (Randomising is a one-line change if you'd
rather.) This replaces `color = msg.get("player_id", "white")`
(`server/main.py:43`), which today lets a stranger take White from you.

Room codes: 4 characters from an unambiguous alphabet (no `0/O`, `1/I`).
~830k combinations; collision-check on creation.

---

## 4. Phases

### Phase 1 — Room layer (server only)

New `server/room.py`:

```python
class Room:
    code: str
    game: GameState
    clients: dict[str, ws | None]   # color -> socket, None when disconnected
    tokens: dict[str, str]          # color -> session token
    task: asyncio.Task | None
    state: LOBBY | RUNNING | FINISHED
    created_at: float

class RoomManager:
    rooms: dict[str, Room]
    quick_queue: list[Connection]
```

`server/main.py` becomes a dispatch loop: each connection holds
`{ws, room, color}` and messages route by type. `Server.broadcast`
(`server/main.py:85-96`) moves onto `Room` mostly as-is.

Game starts when both seats fill (replacing the `_ready` event).

Add to `server/game.py` — the only change to that file:

```python
def forfeit(self, color: str) -> None:
    self.game_over = True
    self.winner = "black" if color == "white" else "white"
```

The `while not self.game_over` loop picks this up on the next tick and exits
cleanly.

**Disconnect handling.** Today a drop only prints (`server/main.py:80-83`)
while the game keeps broadcasting into a dead socket forever. New behaviour:
clear the seat, broadcast `OPPONENT_LEFT`, start a 30s grace timer; on expiry
call `forfeit()`. A `REJOIN` with a valid token cancels the timer.

**Room GC**, sweeping every 30s:
- `FINISHED` older than 60s → drop
- `LOBBY` older than 15 min → drop
- `RUNNING` with both seats empty past grace → forfeit and drop

New test tool `tools/fake_client.py` (~80 lines): headless websocket client
that creates/joins a room and queues random legal-ish moves. Needed because
pygame can't be scripted in CI, and it doubles as the load generator in Phase 4.

- *Verify:* two `fake_client` pairs in two rooms play simultaneously without
  cross-talk; killing one client forfeits its game after 30s and leaves the
  other room untouched; `rooms` dict returns to empty after GC.

**Size: ~250 new lines, ~120 rewritten.**

### Phase 2 — Hardening

Params now arrive over the wire, so they must be validated. Add to
`server/params.py`:

```python
LIMITS = {
    "mana_refill_rate": (0.05, 5.0),
    "maximum_mana":     (1.0, 50.0),
    "movement_speed":   (0.5, 40.0),
    ...
}
def validate_params(d: dict) -> str | None: ...
```

Reject out-of-range rather than clamping — the client UI already constrains
these, so an out-of-range value means a tampered client and should be visible.
`TICK_RATE` stays server-side and is not client-settable.

Also:
- **Token bucket on `QUEUE_MOVE`**: 10 tokens, refill 3/s. Legitimate play
  can't exceed ~5 instant moves then ~0.3/s (mana-bound), so this is generous
  while stopping a flood — each `queue_move` walks the piece list.
- Max message size (websockets `max_size=4096`) and a global per-connection
  message rate cap.
- Max 3 rooms and 5 connections per IP.
- Replace `print` with `logging`.

- *Verify:* `fake_client --hostile` sending oversized params, 1000 msg/s, and
  20 room creations gets rejected/throttled without affecting a concurrent
  real game.

**Size: ~150 lines.**

### Phase 3 — Client

The connection must be opened in the menu and survive into the game. Today
`_game_loop` opens it (`client/main.py:398-412`) and `main()` spawns a local
server first (`client/main.py:550-568`).

- New `client/net.py` (~130 lines): owns the websocket thread, the send/recv
  queues, and connection state. Created by the menu, passed into `_game_loop`.
- `client/main.py`: `_game_loop` takes an existing connection instead of a URL.
  `_spawn_server` stays, used only by Solo and LAN Host.
- `client/menu.py`: new **Online** screen — server URL field, then
  Create Room / Join by Code / Quick Match. Existing Solo and LAN Host/Join
  screens are untouched.
- Server URL persisted to `~/.indiscreet_chess.json` so it isn't retyped.
- `wss://` support: the URL scheme comes from config rather than the hardcoded
  `ws://` at `client/main.py:401`.

- *Verify:* two clients on different machines, via the public URL, create and
  join a room by code and play a full game to a king capture.

**Size: ~200 new lines, ~150 changed in `menu.py`.**

### Phase 4 — Deployment

The server imports only `websockets` + stdlib — **no pygame**. So:

- `requirements-server.txt`: just `websockets>=13.0`.
- `Dockerfile` on `python:3.12-slim`, copying `server/` and `shared/` only.
- `Caddyfile` (TLS + wss reverse proxy is ~4 lines; Caddy handles Let's Encrypt
  automatically).
- `docker-compose.yml` for server + Caddy.
- `/health` endpoint via the websockets `process_request` hook, returning JSON
  with active room and connection counts — serves both uptime checks and basic
  metrics without adding a web framework.

- *Verify:* deployed to a VPS; `wss://` connection succeeds from a machine
  outside the network; 10 `fake_client` pairs sustain 20 Hz with no tick
  overruns in the logs; container restart doesn't wedge the port.

**Size: ~80 lines of config.**

### Phase 5 — Latency polish

- **Ping display**: `PING`/`PONG` every 2s, show RTT in the corner. Players
  will want to know, and you'll want it for bug reports.
- **Jitter buffer**: `client/interpolator.py` currently extrapolates from the
  last packet using time-since-receipt, so network jitter becomes visible
  micro-stutter. Buffer two states and render at `now - 100ms`, interpolating
  between them. ~40 lines.
- **Reconnect UI**: "Opponent disconnected — 27s" overlay driven by
  `OPPONENT_LEFT`.

- *Verify:* under `tc netem delay 80ms 30ms` piece motion stays smooth;
  pulling the network cable for 10s and reconnecting resumes the same game.

**Size: ~120 lines.**

---

## 5. Deferred

**Bandwidth optimisation.** ~1 Mbps down per client today (§7). Delta encoding
plus dropping never-changing fields (`type`, `owner`, `has_moved`) would cut
5–10×. Do this only if the hosting bill or player reports demand it — it
complicates the client for no gameplay gain.

**Accounts, ratings, spectating, match history.** All require a database. None
are needed for "strangers can play each other."

---

## 6. Order of work

Phases 1 and 2 are pure server work, testable headlessly, and are prerequisites
for everything else. Phase 3 is the largest client edit. Phase 4 can be done
early against a stub if you want the deployment pipeline proven before the
client is ready.

1 → 2 → 4 → 3 → 5 is the lowest-risk order: it gets a real server on a real
domain before the client rework, so Phase 3 is developed against production.

---

## 7. Known numbers

- **Egress**: 32 pieces × ~200 B JSON × 20 Hz ≈ 128 KB/s ≈ 1 Mbps per client.
  Four concurrent games ≈ 8 Mbps sustained. Fine on flat-rate VPS bandwidth,
  costly on metered cloud egress.
- **Latency**: at 60–120 ms RTT, input lands ~1–2 ticks late against a 0.5 s
  preparation period. Playable, but the higher-ping player is strictly
  disadvantaged in a simultaneous-move game. Not fixable without input-delay
  equalisation, which is not worth it here.
- **Tick rate**: 20 Hz, server-fixed, not client-configurable.

---

## Appendix A — Files touched

| File | Change |
|---|---|
| `shared/protocol.py` | + lobby message constants |
| `server/room.py` | **new** — Room, RoomManager |
| `server/main.py` | rewritten — dispatch loop, no single game |
| `server/game.py` | + `forfeit()` (~6 lines) |
| `server/params.py` | + `LIMITS`, `validate_params` |
| `client/net.py` | **new** — persistent lobby connection |
| `client/main.py` | connection lifecycle moved out of `_game_loop` |
| `client/menu.py` | + Online screen |
| `client/interpolator.py` | + jitter buffer (Phase 5) |
| `tools/fake_client.py` | **new** — headless test/load client |
| `Dockerfile`, `Caddyfile`, `docker-compose.yml` | **new** |

Untouched: `physics.py`, `rules.py`, `pieces.py`, `renderer.py`.

## Appendix B — If the client moves to the browser

The server work above is unchanged; only Phase 3 is replaced. Porting cost is
`main.py` + `renderer.py` + `menu.py` ≈ 1800 lines of pygame, of which the real
difficulty is `_snap_destination` (`client/main.py:122-309`) — ~190 lines of
input-snapping geometry that any client must reimplement. Everything else is
canvas drawing and UI.

Best options, in order: a hand-written canvas/TS client (best reach, no runtime
download); Godot 4 (one codebase exporting to web *and* desktop); pygbag
(cheapest port, worst result — ~10 MB WASM runtime, slow start, and the
`threading`+`queue` net model in `client/main.py:36-69` needs an asyncio
rewrite regardless).
