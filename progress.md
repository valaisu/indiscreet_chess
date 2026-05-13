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

## How to Run

```bash
# Install dependencies:
pip install -r requirements.txt

# Solo mode or host (menu handles everything):
python host.py

# Multiplayer — guest:
python host.py   # pick Join, enter host's IP
```
