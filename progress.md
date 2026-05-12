# Implementation Progress

## Part 1 — Server Foundation [done]
- [x] shared/protocol.py — message type constants
- [x] server/params.py — all tunable parameters
- [x] server/pieces.py — PieceType, PieceState, Piece, initial_board
- [x] server/game.py — GameState, tick loop, mana, state machines
- [x] server/main.py — WebSocket server, handshake, solo mode
- Tested: solo server boots, client connects, move queued, state machine cycles correctly

## Part 2 — Move Validation
- [ ] server/rules.py — direction sector checks, range caps, all piece types

## Part 3 — Physics (CCD)
- [ ] server/physics.py — parametric sweep, resolution loop, captures

## Part 4 — Special Rules
- [ ] En passant ghost
- [ ] Castling (simultaneous movement, overlap exception)
- [ ] Knight jump immunity + arrival burst
- [ ] Pawn promotion mid-move

## Part 5 — Client
- [ ] client/renderer.py — board, pieces, markers, mana bar
- [ ] client/input.py — click-to-move
- [ ] client/main.py — pygame loop + WebSocket thread
- [ ] client/interpolator.py — smooth positions between ticks

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
