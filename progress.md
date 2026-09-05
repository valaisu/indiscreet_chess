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

## Part 19 - The diagonal pawn captures on contact [done]

- [x] A pawn's diagonal capture is an ordinary move: it runs in `_ccd_loop`
      with everything else and takes the first enemy its hitbox touches, so a
      piece that steps into the path is what dies and the piece it was aimed at
      survives. It could previously be walked straight through.
- [x] With the immunity gone, `physics` lost `_advance_diagonal_pawns`,
      `_diagonal_pawn_burst`, `_friendly_block` and `_is_diagonal_pawn`.
      Friendly blocking, mutual capture and continuing after a capture are all
      the paths the bishop already used; the knight's burst no longer passes
      the pawn over.
- [x] The move is still legal only when an enemy sits on the landing point at
      queue time. Nothing about which destinations can be chosen changed.
- [x] The stamp on `Piece` is inverted: `diagonal_capture` became
      `forward_pawn_move`, because the one rule that still cannot be derived
      from the piece's type is "this move captures nothing". A forward pawn
      that promotes in mid-flight now keeps that, where the velocity-derived
      test in `physics._is_forward_pawn` had dropped it on becoming a queen.
- [x] "A forward pawn captures nothing" now holds from both sides of the
      contact. `_resolve_collision` asked it of `a` and never of `b`, so a
      forward push into a moving enemy traded both, and the piece that ran into
      a forward pawn was taken by it. A forward pawn now dies alone, and two of
      them meeting head-on stop against each other and trade nothing.

## Part 20 - Rules text for en passant and the knight [done]

- [x] The En Passant section says how the capture is actually made: the ghost
      counts as an enemy for the diagonal capture's legality, so it is taken by
      aiming an ordinary diagonal capture at it. It said only that the ghost
      existed.
- [x] A ghost is captured on the same terms as a real piece: it spends the
      pawn's one capture and stops it under Continued Movement After Capture.
      `_resolve_collision` used to return early for a ghost, which made en
      passant free and let one move take the ghost and then a real piece.
- [x] A pawn with no capture left is not blocked by a ghost either. The
      `_sweep_time` guard grew `a.capture_remaining <= 0`, so a spent pawn
      generates no collision event with a marker and comes to rest overlapping
      it; without that, refusing the capture would have stopped it dead on a
      thing that is not a body.
- [x] Only an enemy pawn's diagonal capture interacts with a ghost. Nothing is
      blocked by one and a Knight landing on one does not remove it.
- [x] The Knight's arrival is stated as part of its move: it counts as moving
      when it lands, so Mutual Capture applies, and it still clears its landing
      point in full before going down with whatever was moving.
- [x] The Rules tab's promotion paragraph still promised "same immunity", which
      Part 19 removed.

## Part 21 - Ready state and civilizations on screen [done]

- [x] The pre-game screen carries a sticky bar, one cell per seat, saying
      Ready / still choosing / not here yet. It stays on screen over the nine
      civ cards, which are a long scroll. `ROOM_STATE` already carried `ready`
      and `seated` for both seats: nothing new goes over the wire.
- [x] Your own cell is the Ready button. The control and the state it changes
      are one thing: it is gold and says "press when ready" until pressed, then
      green and "Ready". It reports the server's view, not the click, so it
      cannot claim a readiness the room has not recorded, and a rejected press
      leaves it pressable.
- [x] `#pregame` lost its top padding to `#pregame > h1`. A scroll container's
      padding is inside the scrollport, so a sticky bar at `top: 0` stopped
      2rem down and the cards scrolled through the gap above it.
- [x] Solo owns both seats and readies them together, so it shows one
      full-width cell and hides the opponent's.
- [x] The lede says the game starts once both have pressed Ready.
- [x] Each side's civilization is named on its own mana bar, and a bottom-right
      panel lists both, with the piece-specific effects and their percentages.
      Bottom right was the one free corner: Precise has the top left, resign
      and exit the top right, the ping is drawn bottom left.
- [x] A piece its owner's civilization singles out has a quarter of its own
      outline thickened: gold across the top for what the civ improves, red
      across the bottom for what it costs. Position carries the meaning as
      much as colour, so a piece with both shows both, and the corner panel
      uses the same two colours behind a matching arrow. Drawn in screen
      coordinates, so "up" stays up on a board flipped for black.
- [x] A picked civ card gets a thick inset gold edge, not only a warmer
      background. Inset, so the card does not resize and the grid never
      reflows when the pick moves.
- [x] `civs.pieceMarks` and `civs.pieceEffects` feed the board and the panel
      from one table, so a mark and the line explaining it cannot disagree.
- [x] `--good-lit` / `--bad-lit` join the palette. The existing `--good` and
      `--bad` are ink for parchment cards and vanish on the wood ground these
      two new surfaces sit on.

## Part 22 - Rematch, and an empty code box [done]

- [x] The Join box no longer holds a code by default. `ROOM_CREATED` wrote the
      code of the room you had just *created* into the box for joining someone
      else's, where it outlived the room and offered a dead code every time you
      came back to the lobby. The placeholder is "ABCD", which says the shape.
      The URL fragment still carries the code, so share links are unaffected.
- [x] `REMATCH` plays the same room again: same seats, same tempo, readiness
      and civilizations cleared, back to LOBBY. Both sides then pick and press
      Ready through the one path that starts a game, which is also the point -
      changing civilization is most of why you want a rematch.
- [x] It is idempotent. Both players press the button, so the second press
      lands on a room already in the lobby; answering that with an error would
      put a failure on the screen of whoever was slower. Only a room whose
      other seat is empty refuses, and that refusal is shown in the postgame
      panel where the button was pressed, with the button left pressable.
- [x] `FINISHED_TTL` 60s -> 300s: a rematch offer that expires while you watch
      the replay is not an offer. Finished rooms with nobody in them are now
      reaped at once instead, so the longer TTL cannot leave husks around.
- [x] Protocol VERSION 6 -> 7. Deployed clients holding a stale bundle will say
      "reload" rather than fail on an unknown message.
- [x] `tools/fake_client.py --rematch` plays a game, resigns, rematches from
      both sides and plays the second game. The room lifecycle changed, so the
      headless client had to change with it.

## Part 23 - Balanced rooms, finding a game, and two capture bugs [done]

(The "Not built: accounts and rating" note further up is superseded: accounts,
ratings and stored replays shipped in `Add database`.)

- [x] **A pawn's diagonal capture measured the wrong distance.** Legality asked
      whether an enemy centre was within `pawn.diameter` of the destination.
      That equals the sum of the two radii only when both pieces are the same
      size, and civilizations size piece types apart - so a large pawn was
      offered captures it could not reach: the move validated, the mana was
      spent, the pawn flew out and landed on nothing. Now `pawn.radius +
      other.radius`, in `server/rules.py` and in `web/src/geometry.ts`, which
      has to predict it. `rules.md` said `diameter_piece` too, and said in the
      same sentence that the intent was "the Pawn's hitbox would overlap that
      piece" - the prose was right and the formula was wrong, so the formula
      changed.
- [x] `tools/parity_test.py` gives pieces sizes from a pool instead of building
      every case at 0.6, which is why it never saw the above. That alone still
      found nothing: the diagonal capture circle has radius 0.124, so a click
      drawn over the whole board lands in one about once in 3000 tries.
      `make_pawn_capture_case` aims at the branch, and finds 858 failures
      against the old code where the uniform sweep found zero.
- [x] **A second, older bug the aimed sweep exposed**: the arc snap computed
      its half-angle at radius `diagR` and then placed the point at
      `0.99 * diagR`. The safe half-angle depends on the radius, so the point
      could fall outside the target's hitbox and the server rejected a
      destination the client had just offered - a dead click, with ordinary
      default-sized pieces, in the shipped game. The radius is settled first
      and the angle measured at it.
- [x] **Balanced mode**: a room may give the two seats different parameters, as
      a handicap. This existed once and was deleted, because ROOM_STATE
      announced one `base_params` and a joiner could be seated into a weakened
      side without ever being shown it. It is back with that closed: both
      columns ride in ROOM_STATE with a `balanced` flag, and the pre-game
      screen prints the rows that differ, colours which side each favours, and
      marks which column is yours. Never rated - the result measures the
      handicap.
- [x] `params_black` in CREATE_ROOM overrides black's column; absent, both
      seats share one. Validated and start-overlap checked per side, because
      piece size is per side now too.
- [x] **Game creation is its own screen.** The tempo, visibility and movement
      parameter panels moved off the Play tab into `#create`, which opens with
      "Who can join": Anyone (listed), Invite code, or Just me. Solo practice
      is that third option rather than a separate button - it is the same
      decision.
- [x] **`#find`, the open-game list.** `LIST_ROOMS` returns public rooms that
      have a free seat and have not started, with tempo, balanced flag, the
      host's name if they are signed in, and how long they have waited.
- [x] **Who is online**, on the Play tab: a count of sockets and the names of
      signed-in players, each opening a public card (`GET_PROFILE`) with their
      ratings. Polled every 30s while that tab is in front, not pushed: a
      broadcast to everybody on every connect is a lot of traffic for a number
      in a corner. Replays stayed private to the people who played them at
      this point; Part 24 opened them to any signed-in player, deliberately.
- [x] **Both civilizations, in full, during the match.** The one corner legend
      became two panels: economy on the left edge, per-piece on the right, each
      listing both players. Below 700px they move to the bottom, half width
      each, because a phone has no room beside the board.
- [x] **Deselecting is easier**: spacebar puts the piece down, and so does a
      press anywhere off the board (the canvas is larger than the eight
      squares, so there is always a margin). Keyboard shortcuts now ignore
      keystrokes aimed at a text field, which "f" did not - typing a name in
      the sign-in box flipped the board behind it.
- [x] Protocol VERSION 8 -> 9.
- [x] `tools/lobby_test.py`: 24 checks over balanced rooms, the listing and
      presence, including that the *joiner* is sent both columns and that a
      balanced room still hides the opponent's mana.

## Part 24 - Seats, quick match, profiles and a rematch both sides agree [done]

- [x] **A seat comes back to the room.** `Room.release_seat` and the grace
      timer that calls it. A seat was held by its token for as long as the
      room lived, which is right during a game and wrong once it is not: a
      phone that slept lost the token with the page, so its own reservation
      answered "room is full", while the player still waiting was told "not
      here yet" forever. Leaving on purpose (`Room.leave`) gives the seat back
      at once; losing the socket in the lobby gives it back after the same
      grace window a game uses, so a reload still reclaims its colour.
- [x] **The same bug split quick match players into two rooms.** A player who
      left and asked again could not be put back into the room they had left,
      because it still counted as full, so the server opened a second one and
      the two of them sat in different rooms.
- [x] **A rejoin re-sends ROOM_STATE.** The player coming back had ROOM_JOINED
      and nothing else, so their screen drew an opponent it had never been
      told about: "not here yet", about somebody sitting right there.
- [x] **Quick match asks for a tempo**, one of the three, and the server builds
      the room from its own presets. It used to send whatever the create
      screen had last been left on - a screen the player had no reason to have
      opened - so nobody knew what they were agreeing to. It pairs only with
      rooms that changed nothing else (`Room.matches_quick`), which also makes
      a quick game rateable by construction.
- [x] **A game can be unrated because the host said so.** `unrated` on
      CREATE_ROOM, a checkbox on the create screen, and `rating.settings_reason`
      split out of `rated_reason` so the open-game list can ask about the room
      without asking about who is sitting in it. The pre-game screen prints
      the server's answer ("Not rated: ...") before anyone readies.
- [x] **A rematch takes both players.** One press used to reset the room, which
      pulled the other player off the result they were still reading into the
      civilization screen. Both seats now ask, like readying up. A press that
      lands in the moment between the last frame and the room being marked
      finished is recorded rather than dropped: `Room.maybe_rematch` runs again
      when the room actually finishes.
- [x] **Which seat is which, on screen.** The ready cells say "You - White" and
      "Opponent - Black" with each player's name and their rating at this
      room's tempo, carried per seat in ROOM_STATE (`Room.seat_card`). It
      matters most in a balanced room, where the colour decides which column of
      numbers you play under.
- [x] **Name plates during the match**, at the end of the board each player
      plays from, so both names and ratings are visible while playing.
- [x] **Four civilization panels, not two lists of two.** Left and right say
      what a box is about (economy, pieces); near and far say whose it is,
      following the board's orientation - so the pair at your end is yours,
      flipped or not.
- [x] **The base civilization is a civilization.** "Classical - Vanilla", with
      a card the same shape as the other eight, a name on the mana bar and a
      row in the rules reference. "None" read as having failed to pick.
- [x] **Profiles are public, and paged.** GET_PROFILE carries a page of that
      player's games, twenty at a time, and so does LIST_GAMES for your own.
      A history row is the whole game now - both names, both civilizations and
      both ratings as they stood before it - so the same rows draw your own
      history and somebody else's card.
- [x] **Any player may watch any finished game.** GET_GAME no longer
      asks whether you played in it. Worth knowing what that gives away: a
      recording holds the preparation and destinations the room's visibility
      settings hid while it was being played, so past games can be studied in a
      detail the live game refused.
- [x] The top bar says whether you are signed in, and is the way to the profile.
- [x] Protocol VERSION 9 -> 10.
- [x] `tools/lobby_test.py` covers quick match (pairing, tempo, leaving and
      returning, an unknown tempo) and unrated rooms;
      `fake_client --seat` covers the lobby seat coming back;
      `--rematch` covers the two-sided agreement; `tools/accounts_test.py`
      covers the paged history rows.

## Part 25 - Replays show the whole game [done]

- [x] **A replay is watched from the stored log, not from what your client was
      sent.** The two ways into a replay showed different games: from the
      profile, the server's log expanded into complete frames; straight after
      the game, the frames this client had kept - which with the default
      settings carry no opponent mana and no destinations for their moves.
      `GAME_SAVED` tells both seats where the finished game was stored, and the
      post-game button asks for that. The frames in memory are still the
      fallback, for a server with no database behind it.
- [x] **Nothing is hidden in a replay.** The hiding is a rule of playing: both
      mana pools, both sides' preparation, cooldowns and destinations are in
      the stored log and all of it is drawn.
- [x] **The civilization panels are off while watching.** Both civilizations
      are already named on their own mana bars, and four panels of percentages
      around a board that has also given up a strip to the replay bar is more
      than fits. They come back on returning to the finished game.
- [x] **The replay bar is a strip the board makes room for**, not a box over
      it. Floating at `bottom: 1rem` it sat on the lower mana bar - the very
      thing the replay is for - and at `max-width: 95vw` a phone pushed its own
      controls off the side. `#game.replaying` takes the bar's height off the
      canvas instead, so nothing overlaps at any size. Checked from 320px up:
      no overflow, and the slider keeps 95px at the narrowest.
- [x] **Three controls: back, the tempo, forward.** The tempo button reads the
      speed and is also the pause, because pause here is a speed of zero. The
      arrows step through -4x, -1x, -0.5x, 0x, 0.5x, 1x, 4x, so a replay runs
      backwards as well as forwards; it stops at whichever end it reaches, and
      asking to run on from that end starts again from the other one.
- [x] `Player.playing` is derived from the speed rather than stored beside it.
- [x] The seek slider's release is handled on the window: a touch that ends
      anywhere else never gave the slider its `pointerup`, and the bar then
      stopped following the replay and looked frozen.
- [x] **A reload after the game ends no longer lies.** It rejoins a finished
      room, which is not "waiting", and the pre-game screen was drawn from
      defaults: "not here yet", about an opponent sitting right there. The
      seats are drawn from ROOM_STATE whatever the room is doing, and the
      screen says the game has finished.
- [x] `fake_client --accounts` asserts GAME_SAVED reaches both seats, that the
      recording comes back, and that it holds both sides.

## Part 26 - Anonymous games are ordinary games [done]

- [x] **A game with nobody signed in was already stored; it just could not be
      read back.** The row has null user ids and has had since the first
      migration, but `GET_GAME` asked for an account, so a player without one
      fell back to the frames their own client had kept - which the room's
      visibility settings had already stripped of the opponent's mana and
      destinations. Two ways into a replay, two different games, exactly the
      split Part 25 set out to remove.
- [x] **The sign-in requirement on GET_GAME is gone.** It bought nothing:
      `GET_PROFILE` needs no account either and answers with a page of game
      ids, so the ids were already readable anonymously and only the
      recordings behind them were not. What is left is that an id is
      unguessable, which is the model the schema always described: replayable
      by whoever holds the link.
- [x] **The local history row carries the server's id.** `GAME_SAVED` arrives
      a moment after the final frame, so `logMatch` writes the row and the
      handler names the stored game on it. Without an account this browser is
      the only index there is - nothing can list games with no user id on them
      - but the game itself is kept in full like every other one, so a replay
      now survives a reload instead of living in memory until the tab closes.
- [x] **Fetching a replay connects first.** The socket is opened lazily and
      boots itself only when a token is stored, so a signed-out player who had
      reloaded had no connection: the button sent nothing and said nothing.
      `openStoredGame` is the one path both entry points use.
- [x] `fake_client --accounts` gained `run_anonymous`: two players with no
      accounts, a stored game announced to both, and the recording fetched
      back by a client that never signed in.
- [x] Known and not fixed: two tabs of one browser finishing the same game
      both write to `localStorage` and can clobber each other's id, because a
      read-modify-write there is not atomic across tabs. It needs two players
      in one browser profile, which is a test setup and not a game.

## Part 27 - Both key bindings are settable [done]

- [x] **The unselect key was never mentioned anywhere.** It was two hardcoded
      bindings - Escape, and Space while a game was playable - and Settings
      listed only the precise key. Both are now rows in Settings, and both
      hold any key on the keyboard rather than a menu of three modifiers.
- [x] **A binding is a `KeyboardEvent.key`,** so a modifier ("Shift") and a
      letter ("q") are the same kind of value. `settings.keyMatches` compares
      single characters without case, because the same physical key reports
      "q" or "Q" depending on whether shift is down - and with the precise key
      on Shift, that is most of the time.
- [x] **The button is the control and the value.** Press it, it says "Press a
      key…", and the next keydown binds. Captured on the window in the capture
      phase, so the keystroke being bound never reaches the game's own
      shortcuts - it would otherwise flip the board on its way to becoming the
      flip key. Tab is refused (the page needs it), and so is the key the
      other binding already holds, with the reason said in place.
- [x] **Space no longer unselects unless it is bound to.** The configured key
      is the unselect key, defaulting to Escape; Space stays play/pause in a
      replay. Anyone who wants the old second binding can set it, and a bound
      Space calls `preventDefault` so it does not also scroll the page or
      press a focused button.
