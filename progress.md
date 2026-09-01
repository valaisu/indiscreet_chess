# Implementation Progress

## Part 1 — Server Foundation [done]
- [x] shared/protocol.py — message type constants
- [x] server/params.py — all tunable parameters
- [x] server/pieces.py — PieceType, PieceState, Piece, initial_board
- [x] server/game.py — GameState, tick loop, mana, state machines
- [x] server/main.py — WebSocket server, handshake, solo mode

## Part 2 — Move Validation [done]
- [x] server/rules.py — direction sector checks, range caps, all piece types
- [x] game.py updated to validate before mana deduction

## Part 3 — Physics (CCD) [done]
- [x] server/physics.py — parametric sweep, event-driven resolution loop
- [x] server/pieces.py — added capture_remaining field
- [x] server/game.py — _tick updated to use physics.advance_and_resolve

## Part 4 — Special Rules [done]
- [x] Pawn promotion — type changes to queen when hitbox enters last rank
- [x] En passant ghost — ghost piece created when double-moving pawn crosses ±1-square threshold; captured by enemy diagonal pawns only; expiry tied to opponent's first post-pawn-move queue
- [x] Ghost capture removes original pawn
- [x] Castling — rook starts MOVING simultaneously with king, speed adjusted to arrive same time, CCD overlap exception
- [x] Knight jump immunity + arrival burst

## Part 5 — Client [done]
- [x] client/interpolator.py — advances MOVING piece positions by vel*elapsed, clamped to state_timer
- [x] client/renderer.py — board, pieces, dest markers, ghost overlay, mana bars, game-over screen
- [x] client/main.py — 60fps pygame loop, asyncio WebSocket in background thread, two-click move input
- [x] server/pieces.py — added vel_x/vel_y to to_dict() for interpolation

## Part 6 — Integration [done]
- [x] host.py — thin launcher, delegates entirely to client.main
- [x] client/menu.py — pygame start menu: Solo / Host / Join mode selector, all params configurable with +/− buttons, IP and port inputs
- [x] client/main.py — shows menu on start, spawns server subprocess (Solo/Host), connects as correct color
- [x] server/main.py — accepts all tunable params as CLI args, sets params module before game starts
- [x] Win/draw overlay — rendered in client on game_over
- [x] requirements.txt

## Bug fixes applied post-integration
- [x] Capture teleport — after capturing, piece now continues in original direction and stops when captured piece's center is perpendicular to movement axis (capped at original destination)
- [x] Pawn forward capture — forward-moving pawns cannot capture; they stop on contact; moving enemies that hit a stopped pawn capture it normally
- [x] Pawn diagonal validation — move allowed only if an enemy hitbox overlaps the destination at queue time (not path-based)
- [x] En passant pass-through — capturing pawn continues to its queued destination after touching ghost; ghost removal triggers original pawn removal
- [x] Forward pawn / ghost interaction — forward-moving pawns now pass through en passant ghosts
- [x] Click behavior — clicking an enemy piece while a piece is selected sends a move command instead of switching selection
- [x] Mana bar scaling — bar scales to actual max_mana sent by server, not hardcoded 10.0

---

## Online Multiplayer (branch: web-client)

See `online_plan.md` for the full plan and the rationale behind each choice.

### Part 6 — Room layer [done]
- [x] server/room.py — Room, RoomManager, room codes, quick match, GC sweep
- [x] server/main.py — rewritten as a message dispatch hub
- [x] Server-assigned colours (clients no longer claim a seat)
- [x] Disconnect grace window, forfeit on timeout, REJOIN by session token
- [x] GameState.forfeit() — the only change to game.py

### Part 7 — Hardening [done]
- [x] params.LIMITS + validate_params — params are untrusted input now
- [x] Origin restriction, per-IP room and connection caps
- [x] QUEUE_MOVE token bucket, message rate cap, 4 KB frame limit
- [x] logging instead of print; /health reports room and connection counts

### Part 8 — Browser client [done]
- [x] web/ — TypeScript + canvas, Vite, no framework (~17 KB bundle)
- [x] geometry.ts — port of _snap_destination
- [x] Move-legality hints, drag-to-move, click-to-move, board flip
- [x] Jitter buffer (100 ms render delay), RTT display
- [x] Auto-rejoin when a backgrounded tab wakes
- [x] Room codes in the URL fragment for shareable links

### Part 9 — Deployment [config written, not deployed]
- [x] Dockerfile (python:3.12-slim, websockets only — no pygame)
- [x] fly.toml pinned to one machine (rooms are in-memory)
- [ ] Actually deploy: needs Fly and Cloudflare credentials

### Testing
- `tools/fake_client.py` — headless client: concurrent rooms, forfeit on
  disconnect, seat reclaim, hostile input. No test suite existed before this.
- `tools/parity_test.py` — asserts every destination geometry.ts produces is
  accepted by server/rules.py. Found two real bugs the pygame client shares:
  castling offered with no rook, and a pawn snapping onto its own square.

### Known gaps
- The pygame client in `client/` does not work against the new server; the
  handshake changed. It still works on `main`.
- Its two geometry bugs above are unfixed there (it is being retired).

---

## How to Run

```bash
# Install dependencies:
pip install -r requirements.txt

# Solo mode or host (menu handles everything):
python host.py

# Multiplayer — guest:
python host.py   # pick Join, enter host's IP
```

### Online (branch: web-client)

```bash
# Server (only needs: pip install -r requirements-server.txt)
python -m server.main --port 8765 --verbose

# Client
cd web && npm install && npm run dev

# Tests
python -m tools.parity_test --samples 20000
python -m tools.fake_client --pair --rooms 3 --hostile
# Load tests share one address, so raise the per-IP cap on the server:
#   python -m server.main --max-conn-per-ip 40
```
