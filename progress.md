# Implementation Progress

## Part 1 — Server Foundation [done]
- [x] shared/protocol.py — message type constants
- [x] server/params.py — all tunable parameters
- [x] server/pieces.py — PieceType, PieceState, Piece, initial_board
- [x] server/game.py — GameState, tick loop, mana, state machines
- [x] server/main.py — WebSocket server, handshake, solo mode
- Tested: solo server boots, client connects, move queued, state machine cycles correctly

## Part 2 — Move Validation [done]
- [x] server/rules.py — direction sector checks, range caps, all piece types
- [x] game.py updated to validate before mana deduction
- Tested: all piece types accept legal moves, reject illegal ones; pawn diagonal, double move, castling preconditions verified

## Part 3 — Physics (CCD) [done]
- [x] server/physics.py — parametric sweep, event-driven resolution loop
- [x] server/pieces.py — added capture_remaining field
- [x] server/game.py — _tick updated to use physics.advance_and_resolve
- Tested: enemy capture (rook stops at target's center), mutual capture, friendly blocking, knight burst capture on arrival

## Part 4 — Special Rules [done]
- [x] Pawn promotion — type changes to queen when hitbox enters last rank
- [x] En passant ghost — ghost piece created when double-moving pawn crosses ±1-square threshold; captured by enemy pawns only; expiry tied to opponent's first post-pawn-move queue
- [x] Ghost capture removes original pawn (verified end-to-end)
- [x] Castling — rook starts MOVING simultaneously with king, speed adjusted to arrive same time, CCD overlap exception
- [x] Knight jump immunity + arrival burst — done in Part 3
- Tested: all four rules verified with targeted unit tests

## Part 5 — Client [done]
- [x] client/interpolator.py — advances MOVING piece positions by vel*elapsed, clamped to state_timer
- [x] client/renderer.py — board (with rank/file labels), pieces (filled circles + letter), dest markers, ghost overlay, mana bars, game-over screen
- [x] client/main.py — 60fps pygame loop, asyncio WebSocket in background thread, two-click move input, right-click deselect, Escape to quit
- [x] server/pieces.py — added vel_x/vel_y to to_dict() for interpolation
- Tested: headless render + interpolation verified numerically

## Part 6 — Integration
- [ ] host.py — convenience launcher (server + client in one command)
- [ ] Win/draw screen
- [ ] Parameter tuning

---

## How to Run (once Part 5 is done)

```bash
# Solo mode (one window, control both sides):
python -m server.main --solo &
python -m client.main

# Multiplayer — host:
python -m server.main
python -m client.main --color white

# Multiplayer — guest (replace IP):
python -m client.main --host 192.168.x.x --color black
```
