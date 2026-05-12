# Technical Plan — Indiscreet Chess

## 1. Architecture

**Host/guest model.** One player runs a local server alongside their client. The other connects as a guest.

```
Host machine:
  server.py  (game loop, authoritative state)
      |
  client.py  (pygame, localhost WebSocket)

Guest machine:
  client.py  (pygame, connects to host IP:port)
```

The server is a headless Python process. Both players run the same `client.py`; the host simply also starts `server.py` first. A small launcher script (`host.py`) can start both in one command.

**Networking:** LAN or manual port forwarding. No relay or matchmaking for now.

---

## 2. Tech Stack

| Layer | Library |
|---|---|
| Server networking | `websockets` (asyncio) |
| Client networking | `websockets` (asyncio, background thread) |
| Rendering & input | `pygame` |
| Language | Python 3.11+ |

No other dependencies. All game logic in plain Python.

---

## 3. File Structure

```
indiscreet_chess/
├── server/
│   ├── main.py          # WebSocket server entry point
│   ├── game.py          # GameState, tick loop, win condition
│   ├── pieces.py        # Piece dataclasses, state machine
│   ├── physics.py       # CCD, collision resolution
│   ├── rules.py         # Special rules: pawn, knight, castling, en passant
│   └── params.py        # All tunable parameters (single source of truth)
├── client/
│   ├── main.py          # Entry point: pygame loop + WebSocket thread
│   ├── renderer.py      # Board, pieces, markers, mana bar
│   ├── input.py         # Mouse click → move command
│   └── interpolator.py  # Smooth piece positions between server ticks
├── shared/
│   └── protocol.py      # Message type constants and JSON schemas
└── host.py              # Convenience launcher: starts server + client together
```

---

## 4. Communication Protocol

All messages are JSON over WebSocket.

### Client → Server

| Message | Fields | Description |
|---|---|---|
| `HELLO` | `player_id` | Client identifies itself on connect |
| `QUEUE_MOVE` | `piece_id`, `destination: [x, y]` | Player queues a move |
| `READY` | — | Player signals ready to start |

### Server → Client

| Message | Fields | Description |
|---|---|---|
| `GAME_STATE` | `tick`, `pieces[]`, `mana[]`, `ghosts[]` | Full state broadcast every tick |
| `MOVE_REJECTED` | `piece_id`, `reason` | Move failed validation |
| `GAME_OVER` | `winner` | King captured (or draw) |

`GAME_STATE` sends the full board state every tick (not deltas). At 32 pieces max and ~20 ticks/s, this is well within reasonable bandwidth.

Each piece in `pieces[]`:
```json
{
  "id": "w_queen",
  "type": "queen",
  "owner": "white",
  "x": 3.5,
  "y": 7.0,
  "state": "moving",       // idle | preparation | moving | cooldown
  "state_remaining": 0.4,  // seconds left in current state
  "dest_x": 3.5,
  "dest_y": 3.0
}
```

---

## 5. Server Systems

### 5.1 Tick Loop (`game.py`)

```
loop at tick_rate Hz:
  dt = time since last tick
  1. regenerate mana for both players
  2. collect all moves queued since last tick
  3. validate and apply queued moves (deduct mana, set preparation timer)
  4. advance piece state machines by dt
  5. run CCD pass for all currently moving pieces
  6. apply capture resolutions
  7. apply special rule updates (ghost expiry, etc.)
  8. check win condition
  9. broadcast GAME_STATE to both clients
```

Steps 5–6 may iterate: after resolving the earliest collision, re-sweep remaining movers.

### 5.2 Piece State Machine (`pieces.py`)

States per piece:

```
IDLE → (move queued) → PREPARATION → (timer expires) → MOVING → (arrives or stops) → COOLDOWN → IDLE
```

Transitions:
- `IDLE → PREPARATION`: move queued, mana deducted, origin/dest markers set
- `PREPARATION → MOVING`: `preparation_period` seconds elapsed
- `MOVING → COOLDOWN`: piece stops (destination reached, blocked, or capture-stopped)
- `COOLDOWN → IDLE`: `cooldown` seconds elapsed

A piece in `PREPARATION` that is captured: move cancelled, no refund.

### 5.3 Move Validator (`game.py` or `rules.py`)

On receiving `QUEUE_MOVE`:
1. Piece exists and belongs to this player
2. Piece is in `IDLE` state
3. Player has sufficient mana
4. Destination is within legal movement sector (±`movement_freedom_degrees`)
5. Distance within cap for Type 2 pieces (King, Pawn)
6. Knight destination is within one of the 8 landing circles
7. Pawn-specific: diagonal only if enemy in range; forward blocked by no-capture rule
8. Castling: neither piece has moved, destination is 1–2 squares sideways

### 5.4 Continuous Collision Detection (`physics.py`)

This is the most complex system.

**Problem:** Given N circles each moving at constant velocity, find the earliest pairwise contact time within the current tick's dt.

**Parametric sweep for two moving circles:**

Circle A at position `pA`, velocity `vA`, radius `rA`.
Circle B at position `pB`, velocity `vB`, radius `rB`.

Relative position: `p = pA - pB`, relative velocity: `v = vA - vB`.

Contact when `|p + v*t|² = (rA + rB)²`.

Expand: `(v·v)t² + 2(p·v)t + (p·p - R²) = 0` where `R = rA + rB`.

Solve quadratic; take smallest positive `t ≤ dt`.

**Resolution loop:**

```
while True:
  find earliest (t, pieceA, pieceB) across all moving pairs
  if none: break
  advance all movers to time t
  resolve collision between A and B (capture or block)
  remove resolved pieces from mover set; re-add any that continue moving
  remaining dt -= t
```

**Collision resolution rules:**

| A moving | B moving | A can capture B | Result |
|---|---|---|---|
| yes | yes | yes (and B can capture A) | mutual capture |
| yes | yes | yes (but B cannot capture A) | A captures B, A continues to B's position |
| yes | no | yes | A captures B, A continues to B's position |
| yes | no/yes | no (friendly or budget used) | A stops at contact point |

Knight: immune to capture and cannot capture during movement; captures all overlapping pieces on arrival.

### 5.5 Special Rules (`rules.py`)

**En passant ghost:**
- Created when a pawn completes a double move; stored separately from pieces
- Position: centerpoint of the 3rd/6th rank centerline crossing
- Expiry condition: opponent queues *any* move other than capturing the ghost, after the double-moved pawn finishes moving
- Ghost capture: treated as a normal piece for collision purposes; removing it also removes the original pawn

**Castling:**
- Triggered by king move of 1–2 squares sideways with unmoved rook on that side
- Both pieces enter MOVING simultaneously when king's PREPARATION expires
- Rook velocity calculated so it arrives one square from king's dest at the same time
- Overlap exception: king and castling rook do not block each other during movement
- If rook is stopped, king continues until it contacts the rook

**Knight jump:**
- Knight in MOVING state: excluded from CCD entirely (cannot be hit, cannot hit)
- On arrival: check all pieces with overlapping hitboxes
  - Remove all of them
  - If any removed piece was in MOVING state: remove the knight too
  - If knight survives: enters COOLDOWN at destination

**Pawn promotion:**
- Triggered the instant the pawn's hitbox enters the last rank (during MOVING)
- Piece type changes to queen in-place; movement vector continues under queen rules

---

## 6. Client Systems

### 6.1 Main Loop (`client/main.py`)

Two threads:
- **Main thread**: pygame event loop + rendering at 60 fps
- **Network thread**: asyncio WebSocket receive loop; pushes received states to a thread-safe queue

Main thread pulls latest `GAME_STATE` from queue each frame, interpolates, renders.

### 6.2 Renderer (`renderer.py`)

- Board: 8×8 grid scaled to window, alternating colors
- Pieces: filled circles with Unicode chess symbols centered
- Move markers: colored dots at origin and destination for in-flight moves
- En passant ghost: semi-transparent circle
- Mana bars: one per player
- Cooldown indicator: dimmed piece or overlay

### 6.3 Input (`input.py`)

1. Click a piece → select it (if owned and IDLE)
2. Click a destination → compute destination in board coordinates
3. Validate locally for fast feedback (sector check, range); send `QUEUE_MOVE`
4. Server is authoritative; client shows `MOVE_REJECTED` if server disagrees

### 6.4 Interpolator (`interpolator.py`)

Between received game states, pieces in MOVING state are rendered at linearly interpolated positions based on elapsed time since last tick and their known velocity. Pieces in PREPARATION or COOLDOWN stay at their reported position.

---

## 7. Parameters (`server/params.py`)

All values in one place, easily tunable:

```python
TICK_RATE              = 20      # ticks per second
MANA_REFILL_RATE       = 1.0    # mana per second
MAXIMUM_MANA           = 10.0
BASE_MOVE_COST         = 2.0
DISTANCE_COST          = 0.2    # per board unit
MOVEMENT_FREEDOM_DEG   = 5.0    # degrees
DIAMETER_PIECE         = 0.6    # in square units
SQUARE_SIDE_LENGTH     = 1.0    # board units (everything scales from this)
PREPARATION_PERIOD     = 0.5    # seconds
MOVEMENT_SPEED         = 4.0    # board units per second
COOLDOWN               = 0.8    # seconds
```

---

## 8. Development Phases

### Phase 1 — Server core
1. `params.py`, `pieces.py` (dataclasses + state machine)
2. Tick loop with mana regen and state machine advancement
3. Move validator (all piece types)
4. WebSocket server, HELLO/READY handshake, GAME_STATE broadcast
- **Verify:** two clients connect, pieces advance through states, mana depletes and refills

### Phase 2 — Physics
5. CCD engine (parametric sweep, resolution loop)
6. Standard capture and blocking
7. Continued movement after capture
- **Verify:** pieces collide correctly in isolation, mutual capture works, blocking works

### Phase 3 — Special rules
8. Pawn (forward no-capture, diagonal conditional, double move)
9. En passant ghost
10. Knight jump (immunity + arrival burst)
11. Castling overlap
12. Pawn promotion
- **Verify:** each rule in isolation with scripted test scenarios

### Phase 4 — Client
13. Board and piece renderer
14. Move input (click-to-move)
15. WebSocket integration
16. Interpolation
17. UI (mana bar, move markers, cooldown display)
- **Verify:** full game playable end-to-end on localhost

### Phase 5 — Integration & tuning
18. `host.py` launcher
19. Parameter tuning for feel
20. Win/draw screen
