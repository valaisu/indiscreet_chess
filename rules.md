# Continuous Chess - Rules

## Parameters

The following values are left open as tunable parameters:

| Parameter | Description |
|---|---|
| `mana_refill_rate` | Mana regenerated per second |
| `maximum_mana` | Cap on the mana pool |
| `base_move_cost` | Flat mana cost component per move |
| `distance_cost` | Mana cost per unit of move distance |
| `movement_freedom_degrees` | Angular tolerance (±degrees) around legal movement directions |
| `diameter_piece` | Diameter of each piece's circular hitbox. May vary per piece type. Rejected if the opening position would have any two pieces touching, which means the sum of two neighbours' diameters must be under one square |
| `square_side_length` | Side length of one board square |
| `preparation_period` | Seconds between queuing a move and the piece starting to move |
| `movement_speed` | Speed at which a piece travels to its destination |
| `cooldown` | Seconds a piece must rest after completing a move |

---

## Core Systems

### The Board
The board is a continuous 2D plane corresponding to an 8×8 grid of squares, each with side length `square_side_length`. All positions, distances, and movements are fully continuous - there are no discrete squares or turns.

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

### Type 1 - Infinite Range: Rook, Bishop, Queen
These pieces move in the directions they would in standard chess:
- **Rook**: orthogonal (horizontal and vertical)
- **Bishop**: diagonal
- **Queen**: orthogonal and diagonal

Movement direction is defined as a sector of ±`movement_freedom_degrees` around each legal direction. The player specifies a destination; the piece moves in a straight line toward it, provided that line falls within a legal sector. There is no cap on distance.

### Type 2 - Capped Range: King, Pawn
These pieces follow the same directional sector rules as Type 1, but with a maximum movement distance:
- Horizontal or vertical: at most `square_side_length`
- Diagonal: at most `square_side_length × √2`

The **Pawn** is additionally restricted to moving forward only (toward the opponent's side), except under the diagonal capture rule described below.

### Type 3 - Knight
The Knight has 8 legal landing zones, one for each standard L-shape destination on a continuous board. Each landing zone is a circle:
- Centered on the exact L-shape point (2 squares in one axis, 1 square in the other)
- With radius equal to the lateral deviation permitted at distance `√5 × square_side_length` under the `movement_freedom_degrees` rule: `radius = √5 × square_side_length × tan(movement_freedom_degrees)`

The player specifies any point inside one of these circles as the destination. The Knight moves in a straight line toward it. Move distance - and therefore mana cost - varies slightly depending on the exact destination point chosen within the circle.

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
A moving piece captures an enemy piece the instant their hitboxes first touch. A Pawn executing a diagonal capture is no exception: it captures the first enemy it touches, which need not be the piece it was aimed at.

### Mutual Capture
If both pieces are moving at the moment their hitboxes touch, both pieces are removed simultaneously. **Exception**: a Pawn moving forward captures nothing, and that holds from both sides of the contact. A forward-moving Pawn that meets a moving enemy is removed alone: the enemy takes it, spends its one capture on it and continues under Continued Movement After Capture, and the Pawn takes nothing with it. Two forward-moving Pawns meeting head-on remove neither: both stop at the point of contact.

### Continued Movement After Capture
If a moving piece captures an enemy piece and is not itself captured, it continues moving in the same direction - stopping at whichever comes first: the point where the captured piece's centerpoint lies on a perpendicular to the direction of movement, or the piece's original destination. It then stops (and enters cooldown).

### Capture Limit
Each piece may capture at most one enemy piece per move execution. A ghost counts as that one enemy. **Exception**: the Knight (see Knight rules below), which clears everything it lands on.

### Friendly Pieces
Non-Knight pieces cannot capture friendly pieces. A moving piece that would collide with a friendly piece stops at the point of contact (hitboxes touching) instead.

### Blocking by Uncapturable Pieces
A moving piece also stops at the point of contact if it would collide with any piece it cannot capture at that moment - including pieces it has already passed its capture budget for.

### Capture During Preparation
If a piece is captured while it is in its preparation period, the queued move is cancelled and the mana cost is not refunded.

---

## Special Piece Rules

### Pawn - Forward Movement
The Pawn moves strictly forward (toward the opponent's side). It **cannot** capture pieces by moving forward, and this is a property of the move rather than of the contact: it captures nothing whether it ran into the other piece or the other piece ran into it. If a Pawn moving forward makes contact with a piece it cannot capture - which is every piece - it stops at that point. The Pawn itself can still be captured by an enemy that moves into it, so a forward push into a moving enemy loses the Pawn and takes nothing with it.

### Pawn - Double Move
If the Pawn's centerpoint has never left its starting square, it may move forward up to two squares' worth of distance instead of one.

### Pawn - Diagonal Capture
The Pawn has two diagonal capture landing zones, one for each diagonal forward direction. Each landing zone is a circle:
- Centered on the exact diagonal square (1 square sideways, 1 square forward), at distance √2 × `square_side_length` from the Pawn's center.
- With radius √2 × `square_side_length` × tan(`movement_freedom_degrees`).

The player specifies any point inside one of these circles as the destination, subject to the legality constraint below.

**Legality:** A destination point D is legal if and only if, at the moment the move is queued, at least one **enemy** piece has its center within `diameter_piece` of D - i.e. the Pawn's hitbox would overlap that piece upon landing. Friendly pieces do not satisfy this condition. Only the portion of the circle satisfying this condition is available; the remainder is illegal. If no enemy piece exists anywhere near the circle, the move cannot be queued at all.

**During travel:** The move is an ordinary one, resolved exactly as a Bishop's is. The Pawn captures the first enemy piece its hitbox touches, spending its one capture, and then continues under Continued Movement After Capture. If that enemy was itself moving, both are removed. A friendly piece stops it at the point of contact with nothing captured, and so does any piece it cannot capture at that moment.

The enemy that made the move legal is therefore not necessarily the one taken: anything that gets between the Pawn and its destination is touched first and dies instead.

**If targets move away:** If nothing is touched along the way, the Pawn completes its move and remains at the destination having captured nothing.

### Pawn - En Passant
A double move can be answered by capturing the Pawn at the point it passed through rather than where it ended up.

**The ghost:** The moment a double-moving Pawn's centerpoint has travelled one square from where it started, a **ghost** is created there - at that exact point, so its sideways position is wherever the Pawn's own cone took it, not the centre of a square. The ghost belongs to the moving player, is the same size as a piece, and appears while the Pawn is still travelling.

**What may touch it:** Only an enemy Pawn executing a **diagonal capture**. A forward push cannot take it, and neither can any other piece: a Knight landing on top of a ghost does not remove it, and nothing is ever blocked by one. It is a marker, not a body.

**Capturing it:** The ghost satisfies the diagonal capture's legality condition like any other enemy piece, so it is targeted by aiming an ordinary diagonal capture at it. Taking the ghost removes the Pawn that left it, wherever that Pawn has since got to - including while it is still moving.

**It costs the capture.** A ghost is captured on the same terms as a real piece: it spends the Pawn's one capture, and the Pawn then continues under Continued Movement After Capture. A Pawn that has already captured something earlier in the move therefore cannot take a ghost.

**A spent Pawn passes through.** A ghost is a marker, so a Pawn that cannot capture it is not blocked by it either: it moves through and may come to rest overlapping the marker, which survives, along with the Pawn that left it.

**If the Pawn dies first:** If the double-moving Pawn is captured by other means, its ghost is removed with it.

**How long it lasts:** Moves queued while the double-moving Pawn is still preparing or still travelling do not affect the ghost. The first move the opponent queues after that Pawn has finished expires it - unless that move is a Pawn move aimed at the ghost, which uses the window: the ghost then stops expiring altogether and remains until it is captured or the Pawn that left it dies. So the answer must be the opponent's very next move, but it may be queued while the Pawn is still on its way.

### Pawn - Promotion
When a Pawn's **centerpoint** enters the last rank, it promotes to a Queen. Any movement already in progress continues uninterrupted, completing the previously queued move vector.

Promotion changes the piece, never the move it is executing. A move queued as a forward push still captures nothing, and still cannot capture what runs into it, after the Pawn has become a Queen on the way, and the capture budget already spent by a diagonal capture is not refilled - only the next move refills it.

### King - Castling
Castling is available when neither the King nor the relevant Rook has previously moved. It is initiated by queuing a King move of more than 1 and at most 2 squares directly sideways along its rank.

- Both the King and the Rook begin moving simultaneously the moment their movement phase starts.
- The Rook is timed to arrive at the square directly adjacent to the King's destination (on the side it came from) at the same moment the King arrives. The two pieces will briefly overlap during transit.
- Overlap only begins if both the King and Rook are actively moving. If either piece is blocked before they begin to overlap, neither overlap phase begins.
- Each piece stops if it contacts a friendly piece that is not the other castling piece. If the Rook is blocked and stops, the King continues until it contacts the now-stationary Rook, then also stops. The pieces may remain overlapped in this case.
- If the King is captured mid-castling, the game ends. If the Rook is captured mid-castling, the King continues its move unaffected.

### Knight - Jump
The Knight moves in a straight line toward its destination. During movement (while its position is changing):
- The Knight **cannot** be captured.
- The Knight **cannot** capture.

On arrival at its destination, the Knight captures **all** pieces - friend or foe - whose hitboxes overlap with it, however many. Ghosts are not pieces and are not removed (see Pawn - En Passant).

**Landing on a moving piece:** The arrival is still part of the Knight's move, so the Knight counts as moving at that instant and Mutual Capture applies: if any piece it lands on was itself moving, the Knight is removed too. This does not spare anything else. The Knight clears its landing point in full first - every overlapping piece, moving or not, friendly or not - and only then goes down with the moving one. One moving enemy and three stationary ones means all four are captured and the Knight dies alongside them.

The Knight is never blocked mid-movement. It always completes its trajectory and resolves captures only upon arrival.

---

## Victory and Draw

- The game is won by capturing the enemy King.
- There are no check or checkmate rules. The King may move freely to any position, including positions where it could be captured.
- A King may capture an enemy King directly (by moving into it), resulting in a draw.
- A Knight that lands on its own King removes the King, resulting in a loss for the Knight's owner.
- There are no other draw conditions.