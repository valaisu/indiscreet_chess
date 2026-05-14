# Indiscreet Chess

![Gameplay screenshot](images/snapshot_2.png)

A real-time chess variant where both players move simultaneously on a continuous board. Instead of taking turns, you spend mana to queue moves and everything happens simultaneously.

## How It's Different From Regular Chess

- **Real-time, not turn-based.** Both players act simultaneously.
- **Mana economy.** Every move costs mana (base cost + distance). Mana regenerates over time. You share a pool of up to 5.0 mana.
- **Continuous positions.** Pieces glide smoothly rather than snapping between squares.
- **Capture by collision.** A moving piece captures an enemy when their hitboxes touch. If both pieces are moving when they collide, both are removed (mutual capture).
- **No check or checkmate.** The goal is to physically capture the enemy King.

## Rules

### The Board and Pieces

The board is a continuous 2D plane equivalent to an 8×8 grid. There are no discrete squares, rather pieces have circular hitboxes and can occupy any position. All distances and movements are fully continuous.

### Mana

Each player has a mana pool that regenerates over time up to a maximum of 5.0. Every move costs **base cost + distance × rate** mana, deducted immediately when the move is queued. If you don't have enough mana, you can't queue the move.

### Move Phases

Every queued move goes through four phases:

1. **Deduction** — mana is taken immediately.
2. **Preparation** — a brief delay before the piece starts moving. The piece stays put. If it's captured here, the move is cancelled and mana is not refunded.
3. **Movement** — the piece travels in a straight line to its destination at constant speed.
4. **Cooldown** — after arriving, the piece cannot move and cannot receive new orders for a short period.

A new move can only be queued when the piece is idle (not in any phase) and you have sufficient mana.

### Capture

- A moving piece captures an enemy the instant their hitboxes touch.
- If **both** pieces are moving when they touch, both are removed (mutual capture).
- After capturing, a piece continues toward its original destination (stopping at the captured piece's perpendicular or the destination, whichever comes first).
- Each piece can capture at most **one** enemy per move, except Knights and Pawns doing a diagonal capture.
- A moving piece that would hit a friendly piece (or has already used its capture for this move) stops at the point of contact instead.
- If a piece is captured during its preparation period, the queued move is cancelled, and mana is not refunded.

### Piece Movement

**Rook, Bishop, Queen** — move in their standard chess directions (orthogonal, diagonal, or both) with no distance limit.

**King** — moves in any direction, up to 1 square orthogonally or √2 squares diagonally.

**Pawn** — moves strictly forward, up to 1 square. Cannot capture pieces by moving forward. If it contacts any piece while moving forward, it stops. It can be captured by enemies moving into it.

**Knight** — jumps to one of 8 L-shaped zones. While in transit it cannot be captured and cannot capture. On arrival it captures **all** pieces (friend or foe) whose hitboxes overlap with it. If any of those pieces were moving at the moment of arrival, the Knight is also removed.

### Special Moves

**Pawn — Double Move**
If the pawn's center has never left its starting square, it may move forward up to two squares instead of one.

**Pawn — Diagonal Capture**
A pawn can be sent to one of two forward-diagonal landing zones (one square sideways, one square forward). This move is only legal if an enemy piece is already close enough to that landing point when the move is queued. While traveling diagonally, the pawn cannot be captured, cannot capture, and is never blocked — it passes through all pieces. On arrival, it captures every piece (friend or foe) whose hitbox overlaps the landing position. If any of those pieces were moving on arrival, the pawn is also removed. If all targets moved away before the pawn lands, it arrives safely and captures nothing.

**En Passant**
When a pawn executes a double move, it leaves a ghost at the point where it crossed the 3rd rank (White) or 6th rank (Black). An enemy pawn can capture this ghost, which also removes the original pawn. The ghost disappears the next time the opponent queues any move other than capturing it.

**Pawn Promotion**
When a pawn's hitbox enters the last rank, it immediately promotes to a Queen. Any movement already in progress continues under Queen rules.

**Castling**
When neither the King nor a Rook has previously moved, the King can castle by moving 1–2 squares directly sideways toward that Rook. Both pieces begin moving simultaneously. The Rook is timed to arrive adjacent to the King's destination at the same moment the King arrives causing them to briefly overlap during transit. If either piece is blocked before the overlap begins, neither overlaps. If the Rook is blocked after that, the King continues until it contacts the stopped Rook. If the Rook is captured, the King continues unaffected.

### Victory and Draw

- Win by capturing the enemy King.
- There is no check or checkmate, the King is free to move into danger.
- If two Kings capture each other simultaneously, the game is a draw.
- A Knight that lands on its own King removes it, losing the game.
- There are no other draw conditions.

## Install

Requires Python 3.11+ and pip.

```bash
pip install -r requirements.txt
```

## Play

```bash
python host.py
```

Choose a mode from the menu:

| Mode | Description |
|------|-------------|
| **Solo** | Control both colors yourself — good for learning |
| **Host** | Start a server and play as White; share your IP and port with your opponent |
| **Join** | Enter a host's IP and port to connect and play as Black |

Click a piece to select it, then click a destination to queue a move. The mana bar shows your current pool.
