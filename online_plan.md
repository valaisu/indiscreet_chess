# Online Deployment Plan

Goal: **strangers open a URL in a browser and play each other.** No install, no
port forwarding, no accounts.

Architecture decision: **browser client (TypeScript + canvas), Python server
kept as-is.** The rationale for not rewriting the server in JS is recorded in
Appendix B - read it before reversing the decision.

---

## 1. What changes and what doesn't

**Untouched** (~1100 lines): `server/physics.py`, `server/rules.py`,
`server/pieces.py`, and nearly all of `server/game.py`. The rules engine is
already server-authoritative and location-agnostic. This is the expensive,
hard-won part of the project and it does not get retyped.

**Rewritten**: `server/main.py` - a single-game process becomes a room-hosting
service.

**Replaced**: the entire pygame client (~1800 lines across `client/main.py`,
`renderer.py`, `menu.py`) becomes a `web/` directory of ~1100 lines of
TypeScript. The codebase gets smaller, mostly because 748 lines of hand-rolled
pygame widgets become HTML form controls.

**Retired, not deleted**: the pygame client stays on a branch as a reference
implementation until the browser client reaches parity. Two clients are not
maintained in parallel.

**Dropped**: UDP LAN discovery (`server/main.py:106-143`, `menu.py:85-115`)
plays no part online and has no browser equivalent.

Current architecture assumptions being broken: *one process = one game; params
fixed at spawn time via CLI args; client picks its own color; client and server
are the same language.*

---

## 2. Architecture

```
   Cloudflare Pages  (static, free)        Fly.io  (always-on, 1 machine)
        index.html + bundle.js       wss://    server/main.py
                |                   ------->        |
            browser                          +------+------+
                                             |             |
                                          Room ABCD     Room WXYZ
                                          GameState     GameState
```

Two deploy targets. The client is static files behind a CDN; the server is one
process, one asyncio loop, N rooms. Each room owns a `GameState` and a task
running `GameState.run(room.broadcast)` - that callback signature already
exists (`server/game.py:106`), so the game loop needs no change.

**Capacity.** A tick is O(pieces²) on ≤32 pieces at 20 Hz - microseconds. The
binding constraint is egress: ~1 Mbps per client (§7). Budget by bandwidth,
not compute; the smallest instance any host sells is oversized.

---

## 3. Protocol additions

`shared/protocol.py` gains lobby messages, mirrored in `web/src/protocol.ts`.
All existing in-game messages (`QUEUE_MOVE`, `GAME_STATE`, `MOVE_REJECTED`,
`GAME_OVER`) are unchanged.

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
| S→C | `OPPONENT_REJOINED` | - |
| C→S | `PING` | `t` |
| S→C | `PONG` | `t`, `server_time` |

**Color is assigned by the server**, never claimed by the client. Creator gets
white, joiner gets black. This replaces `color = msg.get("player_id", "white")`
(`server/main.py:43`), which today lets a stranger take White from you.

Room codes: 4 chars from an unambiguous alphabet (no `0/O`, `1/I`), ~830k
combinations, collision-checked. A room code in the URL fragment
(`/#ABCD`) makes games shareable as links.

---

## 4. Phases

### Phase 1 - Room layer (server only)

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
(`server/main.py:85-96`) moves onto `Room` mostly as-is. The game starts when
both seats fill, replacing the `_ready` event.

One addition to `server/game.py` - the only change to that file:

```python
def forfeit(self, color: str) -> None:
    self.game_over = True
    self.winner = "black" if color == "white" else "white"
```

The `while not self.game_over` loop picks this up next tick and exits cleanly.

**Disconnect handling.** Today a drop only prints (`server/main.py:80-83`)
while the game keeps broadcasting into a dead socket forever. New behaviour:
clear the seat, broadcast `OPPONENT_LEFT`, start a 30s grace timer, forfeit on
expiry. A valid `REJOIN` token cancels the timer. This matters far more with a
browser client - closing a tab is one keystroke, and mobile browsers suspend
sockets on backgrounding.

**Room GC**, sweeping every 30s: `FINISHED` older than 60s, `LOBBY` older than
15 min, `RUNNING` with both seats empty past grace.

New `tools/fake_client.py` (~80 lines): headless websocket client that
creates/joins a room and queues moves. Needed because there is no test suite
and browsers can't be scripted in CI cheaply; doubles as the Phase 5 load
generator.

- *Verify:* two `fake_client` pairs in two rooms play simultaneously without
  cross-talk; killing one client forfeits its game after 30s and leaves the
  other untouched; `rooms` returns to empty after GC.

**Size: ~250 new lines, ~120 rewritten.**

### Phase 2 - Hardening

Params now arrive over the wire from a public web page, so they are
attacker-controlled input. Add to `server/params.py`:

```python
LIMITS = {
    "mana_refill_rate": (0.05, 5.0),
    "maximum_mana":     (1.0, 50.0),
    "movement_speed":   (0.5, 40.0),
    ...
}
def validate_params(d: dict) -> str | None: ...
```

Reject out-of-range rather than clamping - the UI already constrains these, so
an out-of-range value means a tampered client and should be visible in logs.
`TICK_RATE` stays server-side and is not client-settable.

Also:
- **Origin check on the websocket handshake.** New requirement with a browser
  client: without it, any web page can open sockets against your server.
- **Token bucket on `QUEUE_MOVE`**: 10 tokens, refill 3/s. Legitimate play is
  mana-bound to ~5 instant moves then ~0.3/s, so this is generous while
  stopping a flood - each `queue_move` walks the piece list.
- `max_size=4096` on the socket; per-connection message rate cap.
- Max 3 rooms and 5 connections per IP.
- Replace `print` with `logging`.

- *Verify:* `fake_client --hostile` (oversized params, 1000 msg/s, 20 room
  creations, forged Origin) is rejected or throttled without affecting a
  concurrent real game.

**Size: ~150 lines.**

### Phase 3 - Browser client

New `web/` directory, built with Vite. **No UI framework** - this is a canvas
plus a handful of form controls; React would be more code, not less.

| File | Replaces | Est. |
|---|---|---|
| `index.html` | `menu.py` (748 lines) | ~150 lines HTML+CSS |
| `src/protocol.ts` | `shared/protocol.py` | ~30 |
| `src/net.ts` | `main.py:36-69` | ~80 |
| `src/geometry.ts` | `main.py:122-322` | ~250 |
| `src/render.ts` | `renderer.py` (669 lines) | ~600 |
| `src/interp.ts` | `interpolator.py` | ~60 |
| `src/main.ts` | `main.py` game loop | ~150 |

**The renderer port is mechanical.** An audit of `renderer.py` found only
circles, rects, polygons, polylines, text and alpha fills - every one is a
direct canvas 2D primitive. The five `SRCALPHA` scratch surfaces disappear
entirely, since canvas takes `rgba()` fills without needing a surface to blit.

Porting notes:
- **HiDPI**: scale the canvas backing store by `devicePixelRatio` or lines
  will look soft.
- **Piece glyphs**: currently `DejaVuSans.ttf` for ♔♕♖. Embed via `@font-face`
  rather than relying on system fallback, or metrics shift between platforms.
- **Threading disappears.** The `threading` + `queue` model in
  `client/main.py:36-69` has no browser analogue and is simply deleted; the
  browser WebSocket is event-driven.
- **Pointer events, not mouse events** - touch support comes free, and the
  existing click-select-then-click-destination input maps onto touch cleanly.
- `interpolate()` currently `deepcopy`s state every frame
  (`interpolator.py:11`); write the port non-mutating over a scratch buffer.
- Resize via `ResizeObserver`, fullscreen via the Fullscreen API.

**Geometry parity is the main risk.** `geometry.ts` must agree with
`server/rules.py:validate_move`, and disagreement means a player clicks and
nothing happens. The existing `0.99` / `0.9999` fudge factors in
`_snap_destination` document three past instances of exactly this bug.
Mitigation - `tools/parity_test.py`: generate N random (piece, click) pairs,
run them through the TS snap via `node`, feed the results to the Python
validator, assert every one is accepted. Runs in CI. **Write this first, port
the geometry against it.**

- *Verify:* parity test passes at 10k samples; two browsers on different
  machines play a full game to a king capture; the same works on a phone.

**Size: ~1100 lines TS, replacing ~1800 lines of Python.**

### Phase 4 - Deployment

The server imports only `websockets` + stdlib - **no pygame**. So:

- `requirements-server.txt`: just `websockets>=13.0`.
- `Dockerfile` on `python:3.12-slim`, copying `server/` and `shared/` only.
- `fly.toml` with `min_machines_running = 1` and a hard cap of one machine -
  rooms live in memory, so a second instance would strand players in different
  worlds. Fly's `*.fly.dev` hostname provides TLS with no domain purchase.
- Client to Cloudflare Pages, deployed from the repo. Server URL injected at
  build time (`VITE_SERVER_URL`).
- `/health` via the websockets `process_request` hook returning JSON with room
  and connection counts - serves uptime checks and basic metrics without
  adding a web framework.

- *Verify:* a phone on cellular data loads the page and plays someone on
  desktop wifi; 10 `fake_client` pairs sustain 20 Hz with no tick overruns in
  the logs; container restart doesn't wedge the port.

**Size: ~80 lines of config.**

### Phase 5 - Latency and polish

- **Ping display**: `PING`/`PONG` every 2s, RTT in the corner. Players want it
  and you want it for bug reports.
- **Jitter buffer**: `interpolator.py` extrapolates from the last packet using
  time-since-receipt, so jitter becomes visible micro-stutter. Buffer two
  states, render at `now - 100ms`, interpolate between them.
- **Reconnect UI**: "Opponent disconnected - 27s" overlay from `OPPONENT_LEFT`;
  auto-`REJOIN` when a backgrounded mobile tab wakes.
- **Shareable links**: `/#ABCD` joins a room directly.

- *Verify:* under `tc netem delay 80ms 30ms` motion stays smooth; backgrounding
  a mobile browser for 10s and returning resumes the same game.

**Size: ~120 lines.**

---

## 5. Deferred

**Bandwidth optimisation.** ~1 Mbps down per client (§7). Delta encoding plus
dropping never-changing fields (`type`, `owner`, `has_moved`) would cut 5-10×.
Do it only if the bill or player reports demand it - it complicates the client
for no gameplay gain.

**Accounts, ratings, spectating, match history.** All need a database. None are
needed for "strangers can play each other."

---

## 6. Order of work

1 → 2 → 4 → 3 → 5.

Phases 1 and 2 are pure server work, headlessly testable, and prerequisites for
everything else. Doing Phase 4 *before* Phase 3 means the browser client is
developed against the real deployed server over real TLS from day one - which
is where the awkward bugs live (mixed content, Origin checks, proxy timeouts),
and they are much cheaper to find before the client exists than after.

---

## 7. Known numbers

- **Egress**: 32 pieces × ~200 B JSON × 20 Hz ≈ 128 KB/s ≈ 1 Mbps per client;
  ~150 MB per 10-minute game. 100 games/month ≈ 15 GB. Free on flat-bandwidth
  hosts, single-digit dollars on Fly, expensive only on AWS-tier egress rates.
- **Latency**: at 60-120 ms RTT input lands ~1-2 ticks late against a 0.5 s
  preparation period. Playable, but the higher-ping player is strictly
  disadvantaged in a simultaneous-move game. Not fixable without input-delay
  equalisation, which is not worth it here.
- **Tick rate**: 20 Hz, server-fixed, not client-configurable.

---

## Appendix A - Files touched

| File | Change |
|---|---|
| `shared/protocol.py` | + lobby message constants |
| `server/room.py` | **new** - Room, RoomManager |
| `server/main.py` | rewritten - dispatch loop, no single game |
| `server/game.py` | + `forfeit()` (~6 lines) |
| `server/params.py` | + `LIMITS`, `validate_params` |
| `web/` | **new** - browser client (~1100 lines TS) |
| `tools/fake_client.py` | **new** - headless test/load client |
| `tools/parity_test.py` | **new** - TS/Python geometry agreement |
| `Dockerfile`, `fly.toml` | **new** |
| `client/` | retired to a branch once `web/` reaches parity |

Untouched: `physics.py`, `rules.py`, `pieces.py`.

## Appendix B - Why the server stays Python

The tempting alternative is rewriting everything in TS so client and server
share one language. The real prize would be **sharing the geometry module**:
the client must predict what `validate_move` will accept, and today that logic
is implemented twice. The three `0.99` / `0.9999` fudge factors in
`_snap_destination` exist solely to paper over float disagreement between the
two implementations, and shared code would delete two of them. (The third - for
"physics can nudge idle pieces between snapshot and validation" - is a
staleness problem that exists in any language.)

It still loses, for one reason: **there is no test suite.** Rewriting ~1100
lines of continuous collision detection, mutual capture, castling overlap
timing and en-passant ghost lifecycles without tests means re-debugging it by
hand, unable to distinguish a regression from an intended rules change. That is
the most expensive and least visible work in the project, and it is already
done and working.

`tools/parity_test.py` (Phase 3) buys most of the shared-code benefit for a
fraction of the risk.

Revisit this only if a real test suite for the physics engine appears first.
