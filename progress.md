# Implementation Progress

## Part 1 - Server Foundation [done]
- [x] shared/protocol.py - message type constants
- [x] server/params.py - all tunable parameters
- [x] server/pieces.py - PieceType, PieceState, Piece, initial_board
- [x] server/game.py - GameState, tick loop, mana, state machines
- [x] server/main.py - WebSocket server, handshake, solo mode

## Part 2 - Move Validation [done]
- [x] server/rules.py - direction sector checks, range caps, all piece types
- [x] game.py updated to validate before mana deduction

## Part 3 - Physics (CCD) [done]
- [x] server/physics.py - parametric sweep, event-driven resolution loop
- [x] server/pieces.py - added capture_remaining field
- [x] server/game.py - _tick updated to use physics.advance_and_resolve

## Part 4 - Special Rules [done]
- [x] Pawn promotion - type changes to queen when hitbox enters last rank
- [x] En passant ghost - ghost piece created when double-moving pawn crosses ±1-square threshold; captured by enemy diagonal pawns only; expiry tied to opponent's first post-pawn-move queue
- [x] Ghost capture removes original pawn
- [x] Castling - rook starts MOVING simultaneously with king, speed adjusted to arrive same time, CCD overlap exception
- [x] Knight jump immunity + arrival burst

## Part 5 - Client [done]
- [x] client/interpolator.py - advances MOVING piece positions by vel*elapsed, clamped to state_timer
- [x] client/renderer.py - board, pieces, dest markers, ghost overlay, mana bars, game-over screen
- [x] client/main.py - 60fps pygame loop, asyncio WebSocket in background thread, two-click move input
- [x] server/pieces.py - added vel_x/vel_y to to_dict() for interpolation

## Part 6 - Integration [done]
- [x] host.py - thin launcher, delegates entirely to client.main
- [x] client/menu.py - pygame start menu: Solo / Host / Join mode selector, all params configurable with +/− buttons, IP and port inputs
- [x] client/main.py - shows menu on start, spawns server subprocess (Solo/Host), connects as correct color
- [x] server/main.py - accepts all tunable params as CLI args, sets params module before game starts
- [x] Win/draw overlay - rendered in client on game_over
- [x] requirements.txt

## Bug fixes applied post-integration
- [x] Capture teleport - after capturing, piece now continues in original direction and stops when captured piece's center is perpendicular to movement axis (capped at original destination)
- [x] Pawn forward capture - forward-moving pawns cannot capture; they stop on contact; moving enemies that hit a stopped pawn capture it normally
- [x] Pawn diagonal validation - move allowed only if an enemy hitbox overlaps the destination at queue time (not path-based)
- [x] En passant pass-through - capturing pawn continues to its queued destination after touching ghost; ghost removal triggers original pawn removal
- [x] Forward pawn / ghost interaction - forward-moving pawns now pass through en passant ghosts
- [x] Click behavior - clicking an enemy piece while a piece is selected sends a move command instead of switching selection
- [x] Mana bar scaling - bar scales to actual max_mana sent by server, not hardcoded 10.0

---

## Online Multiplayer (branch: web-client)

See `online_plan.md` for the full plan and the rationale behind each choice.

### Part 6 - Room layer [done]
- [x] server/room.py - Room, RoomManager, room codes, quick match, GC sweep
- [x] server/main.py - rewritten as a message dispatch hub
- [x] Server-assigned colours (clients no longer claim a seat)
- [x] Disconnect grace window, forfeit on timeout, REJOIN by session token
- [x] GameState.forfeit() - the only change to game.py

### Part 7 - Hardening [done]
- [x] params.LIMITS + validate_params - params are untrusted input now
- [x] Origin restriction, per-IP room and connection caps
- [x] QUEUE_MOVE token bucket, message rate cap, 4 KB frame limit
- [x] logging instead of print; /health reports room and connection counts

### Part 8 - Browser client [done]
- [x] web/ - TypeScript + canvas, Vite, no framework (~17 KB bundle)
- [x] geometry.ts - port of _snap_destination
- [x] Move-legality hints, drag-to-move, click-to-move, board flip
- [x] Jitter buffer (100 ms render delay), RTT display
- [x] Auto-rejoin when a backgrounded tab wakes
- [x] Room codes in the URL fragment for shareable links

### Part 9 - Deployment [live]
- [x] Dockerfile (python:3.12-slim, websockets only - no pygame). 43 MB image.
- [x] Server: wss://indiscreet-chess-valaisu.fly.dev  (Fly, ~$4/month)
- [x] Client: https://indiscreet-chess.indiscreet-chess-web.workers.dev  (free)
- [x] Origin restricted via the ALLOWED_ORIGINS env var in fly.toml
- [x] Verified over wss: concurrent rooms, forfeit, hostile input, origin refusal

Cloudflare Pages project creation failed with an opaque API error on a fresh
account, so the client is served as Workers static assets instead. Same free
tier, same result: `cd web && npx wrangler deploy`.

Deploy with `fly deploy --remote-only --ha=false`. Without `--ha=false` Fly
adds a second machine, which would split rooms across two processes.

### Testing
- `tools/fake_client.py` - headless client: concurrent rooms, forfeit on
  disconnect, seat reclaim, hostile input. No test suite existed before this.
- `tools/parity_test.py` - asserts every destination geometry.ts produces is
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

# Multiplayer - guest:
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

---

## Part 10 - Naming, information, replay, input [done]

- [x] Renamed to **Continuous Chess** in every user-facing string and doc.
      Deployed hostnames, the Fly app and the npm package keep the old name:
      changing those changes the URL people have and re-provisions the app.
- [x] `diameter_piece` travels in `GAME_STATE` (`"d"` per piece), so the client
      draws and snaps with the real hitbox. It is now a tempo-preset row and a
      civilization dimension; a promoted pawn takes the queen's size.
- [x] Room-level visibility options (`view` in CREATE_ROOM / QUICK_MATCH):
      enemy mana, preparation, cooldown, destination. Filtered in
      `GameState.to_dict(viewer)` - the server sends each seat only what it may
      see, so `Room.broadcast_game` serialises once per player.
- [x] Post-game screen: replay at 0.5/1/2/4x with a scrubber, or a new game.
      The recording is the received snapshots, client-side only.
- [x] Personal settings in localStorage: move mode, drag threshold, hints, and
      a precise mode (Shift, or an on-screen button for touch) that lets any
      drag distance count.
- [x] `tools/visibility_test.py` - asserts hidden information is absent from
      the snapshot, not merely undrawn. Wired into `deploy.sh` alongside the
      civilization budget check.
- Protocol VERSION 3 → 4.

### Not built: accounts and rating

`docs/accounts.md` is a design with a recommendation (OAuth, no stored
passwords, Supabase free tier, Elo per tempo mode, anonymous play unchanged).
It needs four answers before any of it is written, one of which involves
spending.

## Part 11 - Pawn rework and opening geometry [done]

- [x] A diagonal capture takes **one** enemy - the nearest overlapping the
      landing point - instead of clearing the square. It spends
      `capture_remaining` like every other capture; only the Knight still
      bursts.
- [x] It can no longer take friendly pieces at all. `physics._friendly_block`
      sweeps the flight against the pawn's own side and stops it at the contact
      surface, capturing nothing - the same ending every other blocked move
      has, and it avoids a pawn coming to rest overlapping its own piece.
      Enemies still cannot touch it, and ghosts never block.
- [x] Promotion fires on the **centerpoint** reaching the last rank, not the
      hitbox edge. The old rule promoted a large pawn most of a square early,
      so promotion depended on piece size.
- [x] `Piece.diagonal_capture` is stamped when the move is queued
      (`rules.is_forward_pawn_move`) and read by the physics, replacing a test
      derived from `type == PAWN`. A pawn that promotes in mid-flight kept its
      immunity and its arrival burst only because of this; before it, the queen
      dropped out of the immune set halfway across and never fired.
- [x] `pieces.start_overlap_reason` refuses any settings whose opening position
      has two pieces touching, checked on the real layout because sizes are per
      piece type. Enforced at CREATE_ROOM/QUICK_MATCH and at SET_READY - a base
      size can be legal while a civilization's pawn modifier pushes it over.
- [x] Errors are now shown on the pre-game screen, which is where that second
      rejection lands; the lobby's status line is off-screen by then.
- [x] Greek pawns +25% -> +20%, distance cost left at 10%. Greek therefore sits
      at +0.15 points and the budget check gained a tolerance
      (`BUDGET_TOLERANCE = 0.25`) rather than a corrected decimal: per-piece
      rows are priced with estimates, so exact zero was false precision.
      `civ_table.mjs` prints the drift instead of hiding it.
- [x] `tools/pawn_test.py` - 18 checks over capture count, promotion timing,
      mid-flight promotion and opening geometry. In `deploy.sh`.

## Part 12 - Wood and parchment [done]

- [x] One palette across the page and the canvas: dark wood for the table,
      parchment for anything that is "paper" (the civilization cards), gold for
      whatever is currently chosen. The board's own squares were already the
      classic wood pair, so the change was to stop the surrounding chrome from
      being grey - including the pieces, which are now ivory and walnut rather
      than white and near-black.
- [x] `web/src/civicons.ts` - a line-drawn icon per civilization, inline SVG in
      `currentColor` so a card colours and sizes its own. Not emoji: the replay
      bar's pause glyph already rendered as tofu once, and a missing glyph in a
      row of eight choices is not recoverable.
- [x] `civs.TITLE` replaces the sentence of flavour with a Civ-style epithet of
      one or two words ("Horse Archers", "Royal Roads"), set in small caps under
      the name. The effects list underneath is what actually explains the civ.

## Part 13 - Solo, exits, and one box per decision [done]

- [x] Solo practice really is both sides now. The server always allowed it
      (`GameState.solo` skips the ownership check); the client was the limit,
      because it inferred solo from `net.color === null` and a solo seat is
      dealt white. It now carries the room's `solo` flag, and stores it with
      the seat so a reload does not silently drop back to one colour.
- [x] A civilization per seat in solo. `SET_READY` gained an optional `color`,
      honoured only in a solo room, and `Room.set_ready` sets one seat instead
      of filling both. The pre-game screen grows a "Choosing for White/Black"
      switch, and each card is tagged with the seats that took it.
- [x] Resigning: `RESIGN` ends a running game through `GameState.forfeit`, so
      the result reaches both clients as an ordinary GAME_OVER instead of a
      30 second disconnect timeout. Every way out is now labelled "Exit to
      lobby", on the pre-game screen, during the game, and after it.
      Leaving a live game asks once, on the button itself.
- [x] The lobby is four panels: Play, Game settings, Your controls, Reference.
      Tempo options are just the three names, with what each is for in a line
      under the select rather than inside the option labels.
- [x] No em dashes anywhere in the repository, prose or code comments.
- [x] `tools/solo_test.py` - both seats moving from one socket, a civilization
      each, and resignation seen by both players. It needs a running server, so
      it is in CHEATSHEET.md rather than deploy.sh, which is all offline checks.
- Protocol VERSION 4 -> 5.

## Part 14 - Navigation, rules, profile [done]

- [x] The server address field is gone. It was a developer affordance in the
      player's way, and a footgun: whatever was typed persisted in
      localStorage, so a typo left a client that could not connect and no way
      back except finding the field again. Development now uses
      `?server=ws://localhost:8765`, which lives in the URL and persists
      nothing.
- [x] Four tabs: Play, Settings, Rules, Profile. Play holds how to start a game
      and the tempo; Settings holds the personal controls, the visibility rules
      and the parameter grid; Rules is prose; Profile is history.
- [x] Rules tab: the game explained in nine short sections. `rules.md` stays
      the specification; this is the version a player reads.
- [x] Profile tab: every finished game, with its result, sides, tempo and
      length. A row is small and lives in localStorage. The recording it names
      is the snapshots themselves, kept in memory, so a replay lasts as long as
      the tab does; older rows say "replay expired" rather than pretending. A
      game left early is kept too, marked Unfinished, because the recording is
      the point.
- [x] Exiting a game no longer reloads the page. It leaves the room, clears
      what the game owned and shows the lobby, which is what makes in-memory
      recordings survive from one game to the next.
- [x] Two bugs found by doing that: a solo room kept running and kept
      broadcasting after its only client left, because the client was seated
      twice and only one seat was cleared; and a snapshot still in flight when
      the room was left dragged the player back onto the board. Fixed on both
      sides, with a test.

## Part 15 - Corrections [done]

- [x] Settings holds only personal controls. Everything that decides how a game
      plays (tempo, what players can see, the parameters) is game balance and
      lives on the Play tab with the buttons that start a game.
- [x] The precise key is a choice of Shift, Control or Alt, not a constant.
- [x] `enemy_dest` now defaults to off: where the opponent's moves are headed is
      hidden unless the room turns it on. Both copies of VIEW_DEFAULTS changed,
      and `visibility_test.py` now asserts the two agree, since the checkboxes
      come from the client's copy and the rules from the server's.
- [x] The rules page said "a piece may take at most one enemy per move" and
      "you cannot capture your own pieces" as though they were universal. Both
      are wrong for two of the six piece types, which are exactly the two whose
      exceptions decide games. Rewritten against `physics.py`: forward pawns
      capture nothing at all and stop on contact with anything; a knight's
      arrival removes every overlapping piece, friendly ones included, with no
      limit on how many. En passant and the double move were missing entirely
      and are now described.

## Part 16 - Top bar [done]

- [x] The tabs are a bar across the top of the window: the brand on the left,
      the tabs on the right, a border and a shadow under it, and the page body
      scrolling beneath. `#topbar` is a column containing one `.bar` row, so a
      second row of sub-options can be added under the tabs without touching
      the layout around it.
- [x] Switching tabs scrolls the body back to the top. There is one scroll
      container behind all four, so without it a tab opened wherever the last
      one was left.
- [x] The nav can shrink and scroll sideways (`min-width: 0`), and the brand
      hides under 30rem. The page itself cannot scroll horizontally, so an
      overflowing bar would have put the last tab out of reach on a phone.

## Part 17 - Start a game, tidied [done]

- [x] Four options, one per line, all the same size, none of them highlighted:
      Quick match, Create room, Solo practice, and Join.
- [x] The paragraph explaining solo practice is a question mark at the right
      edge of that button instead, with the text on hover or keyboard focus. It
      is a CSS tooltip rather than a `title` attribute so it appears at once and
      is styled like the rest of the page.
- [x] Join comes before the code box and takes three quarters of the line, with
      the four characters in a centred box after it. The "Room code" label is
      gone: the button says what the row does.

## Part 18 - Tempo as a bar [done]

- [x] The tempo select is a four-way segmented bar, with one line under it that
      always starts "Enough time to ...". The `<select>` is gone: `setTempo`
      holds the choice, paints the bar and writes the parameter fields, and
      editing a field by hand calls it with the preset write suppressed.
- [x] "Applies to a room you open" moved onto a question mark beside the
      heading, the same hover used for solo practice.
