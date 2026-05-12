# RTS Chess — Rules

## Parameters

The following values are left open as tunable parameters:

| Parameter | Description |
|---|---|
| `mana_refill_rate` | Mana regenerated per second |
| `maximum_mana` | Cap on the mana pool |
| `base_move_cost` | Flat mana cost component per move |
| `distance_cost` | Mana cost per unit of move distance |
| `movement_freedom_degrees` | Angular tolerance (±degrees) around legal movement directions |
| `diameter_piece` | Diameter of each piece's circular hitbox |
| `square_side_length` | Side length of one board square |
| `preparation_period` | Seconds between queuing a move and the piece starting to move |
| `movement_speed` | Speed at which a piece travels to its destination |
| `cooldown` | Seconds a piece must rest after completing a move |

---

## Core Systems

### The Board
The board is a continuous 2D plane corresponding to an 8×8 grid of squares, each with side length `square_side_length`. All positions, distances, and movements are fully continuous — there are no discrete squares or turns.

### Pieces
Every piece is a circle with diameter `diameter_piece`. A piece's position is defined by its centerpoint.

### Mana
Each player has a mana pool that continuously refills at `mana_refill_rate` per second, capped at `maximum_mana`. Mana is the sole resource for queuing moves.

### Move Cost
Queuing a move costs `base_move_cost + distance_cost × move_distance` mana, deducted immediately upon submission, regardless of outcome.

### Ticks
The game runs in discrete ticks. All moves queued during a tick are submitted simultaneously at the end of that tick. Piece movement itself is smooth and continuous between ticks.

---

## Movement Types

### Type 1 — Infinite Range: Rook, Bishop, Queen
These pieces move in the directions they would in standard chess:
- **Rook**: orthogonal (horizontal and vertical)
- **Bishop**: diagonal
- **Queen**: orthogonal and diagonal

Movement direction is defined as a sector of ±`movement_freedom_degrees` around each legal direction. The player specifies a destination; the piece moves in a straight line toward it, provided that line falls within a legal sector. There is no cap on distance.

### Type 2 — Capped Range: King, Pawn
These pieces follow the same directional sector rules as Type 1, but with a maximum movement distance:
- Horizontal or vertical: at most `square_side_length`
- Diagonal: at most `square_side_length × √2`

The **Pawn** is additionally restricted to moving forward only (toward the opponent's side), except under the diagonal capture rule described below.

### Type 3 — Knight
The Knight has 8 legal landing zones, one for each standard L-shape destination on a continuous board. Each landing zone is a circle:
- Centered on the exact L-shape point (2 squares in one axis, 1 square in the other)
- With radius equal to the lateral deviation permitted at distance `√5 × square_side_length` under the `movement_freedom_degrees` rule: `radius = √5 × square_side_length × tan(movement_freedom_degrees)`

The player specifies any point inside one of these circles as the destination. The Knight moves in a straight line toward it. Move distance — and therefore mana cost — varies slightly depending on the exact destination point chosen within the circle.

---

## Move Execution

When a move is queued:

1. **Deduction**: Mana is deducted immediately.
2. **Marking**: The piece's origin and destination are visually marked on the board.
3. **Preparation**: A `preparation_period`-second delay begins. The piece does not move and its position does not change.
4. **Movement**: The piece travels in a straight line to its destination at `movement_speed`.
5. **Cooldown**: Upon stopping, the piece enters a `cooldown`-second period during which it cannot move and no new moves can be queued for it.

A move can only be queued when:
- The piece is not in cooldown.
- The player has sufficient mana.

A piece is considered **moving** only when its position is actively changing (i.e., during step 4). The preparation period does not count as moving.

---

## Capture

### Standard Capture
A moving piece captures an enemy piece the instant their hitboxes first touch.

### Mutual Capture
If both pieces are moving at the moment their hitboxes touch, both pieces are removed simultaneously.

### Continued Movement After Capture
If a moving piece captures an enemy piece and is not itself captured, it continues moving — but only until its centerpoint reaches the position where the captured piece's centerpoint was at the moment of capture. It then stops (and enters cooldown).

### Capture Limit
Each piece may capture at most one enemy piece per move execution. **Exception**: the Knight (see Knight rules below).

### Friendly Pieces
Non-Knight pieces cannot capture friendly pieces. A moving piece that would collide with a friendly piece stops at the point of contact (hitboxes touching) instead.

### Blocking by Uncapturable Pieces
A moving piece also stops at the point of contact if it would collide with any piece it cannot capture at that moment — including pieces it has already passed its capture budget for.

### Capture During Preparation
If a piece is captured while it is in its preparation period, the queued move is cancelled and the mana cost is not refunded.

---

## Special Piece Rules

### Pawn — Forward Movement
The Pawn moves strictly forward (toward the opponent's side). It **cannot** capture pieces by moving forward. If a Pawn moving forward makes contact with any piece (friend or foe), it stops at that point. The Pawn itself can be captured by enemy pieces that move into it.

### Pawn — Double Move
If the Pawn's centerpoint has never left its starting square, it may move forward up to two squares' worth of distance instead of one.

### Pawn — Diagonal Capture
A Pawn may move diagonally forward if and only if an enemy piece occupies a position reachable by that diagonal move at the time the move is queued. If the target piece moves out of the way before the Pawn's hitbox reaches it, the Pawn completes its move regardless, arriving at the queued destination having captured nothing. The Pawn remains at that diagonal position.

### Pawn — En Passant
When a Pawn executes a double move, it leaves a **ghost** at the point where its centerpoint crosses the centerline of the 3rd rank (White) or 6th rank (Black). An enemy Pawn may capture this ghost; doing so also removes the original Pawn.

The ghost disappears when the opponent queues a move for the first time after the double-moving Pawn has finished its movement, and that queued move is not one targeting the ghost. Until this window closes, the double-moved Pawn may continue moving and can still be removed via en passant.

### Pawn — Promotion
When a Pawn's hitbox enters the last rank, it immediately promotes to a Queen. Any movement already in progress continues uninterrupted, completing the previously queued move vector under Queen rules.

### King — Castling
Castling is available when neither the King nor the relevant Rook has previously moved. It is initiated by queuing a King move of more than 1 and at most 2 squares directly sideways along its rank.

- Both the King and the Rook begin moving simultaneously the moment their movement phase starts.
- The Rook is timed to arrive at the square directly adjacent to the King's destination (on the side it came from) at the same moment the King arrives. The two pieces will briefly overlap during transit.
- Overlap only begins if both the King and Rook are actively moving. If either piece is blocked before they begin to overlap, neither overlap phase begins.
- Each piece stops if it contacts a friendly piece that is not the other castling piece. If the Rook is blocked and stops, the King continues until it contacts the now-stationary Rook, then also stops. The pieces may remain overlapped in this case.
- If the King is captured mid-castling, the game ends. If the Rook is captured mid-castling, the King continues its move unaffected.

### Knight — Jump
The Knight moves in a straight line toward its destination. During movement (while its position is changing):
- The Knight **cannot** be captured.
- The Knight **cannot** capture.

On arrival at its destination, the Knight captures **all** pieces — friend or foe — whose hitboxes overlap with it. If any of those pieces were themselves moving at the moment of the Knight's arrival, the Knight is also removed (in addition to capturing them).

The Knight is never blocked mid-movement. It always completes its trajectory and resolves captures only upon arrival.

---

## Victory and Draw

- The game is won by capturing the enemy King.
- There are no check or checkmate rules. The King may move freely to any position, including positions where it could be captured.
- A King may capture an enemy King directly (by moving into it), resulting in a draw.
- A Knight that lands on its own King removes the King, resulting in a loss for the Knight's owner.
- There are no other draw conditions.