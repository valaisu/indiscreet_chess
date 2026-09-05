# How it works

A technical tour of the online game: what runs where, who decides what, and
which parts are duplicated on purpose. For the rules of play see `rules.md`
and the Rules tab in the client.

## The shape of it

```
  browser                       Fly (one machine)              Supabase
  ---------------               -------------------            ------------
  web/src/*.ts                  server/main.py                 Postgres
  vite build            wss     websocket server      asyncpg
  Cloudflare Workers  <------>  rooms live in RAM   <-------->  users
  (static assets)               20 Hz game loop                 sessions
                                                                ratings
                                                                games
```

Three facts follow from that picture and explain most of the design:

- **A room is memory, not a row.** Rooms, pieces and mana exist only in the
  server process. Nothing about a game in progress is written down, so a
  restart ends every game being played, and running a second machine would put
  the two halves of a game in different worlds. This is why `deploy.sh` passes
  `--ha=false` and why a deploy cannot be made seamless.
- **The database is optional.** Every call in `server/db.py` is a no-op when
  `DATABASE_URL` is unset. Persistence happens *after* a game, so a database
  that is down, slow or absent must never stop anybody playing.
- **The client is static.** The browser bundle is served by Cloudflare Workers
  and talks to Fly over one WebSocket. The server address is baked in at build
  time from `VITE_SERVER_URL`, which is what forces the deploy order below.

## The modules

| Server | |
|---|---|
| `main.py` | WebSocket entry point, message dispatch, rate limits, origin checks |
| `room.py` | Many concurrent games in one process: seats, tokens, readiness, rematch |
| `game.py` | One game: the 20 Hz loop, mana, snapshots, forfeits |
| `physics.py` | Continuous collision detection, captures, blocking |
| `rules.py` | Move validation: direction sectors, range caps, board bounds |
| `pieces.py` | Piece types, board layout, opening-overlap checks |
| `params.py` | Every tunable number, and the bounds a client's may fall inside |
| `presets.py` `civs.py` | Tempo presets and civilizations, resolved server side |
| `recorder.py` | Turns a game into an event log while it is played |
| `accounts.py` | Local accounts: argon2id, sessions, no email and no reset |
| `rating.py` | Elo per tempo, and the rule for which games get one |
| `user_settings.py` | What a client may store as personal settings |
| `db.py` | The whole data layer: one pool, a handful of statements, no ORM |

| Client | |
|---|---|
| `main.ts` | Lobby wiring, input handling, render loop |
| `net.ts` | The socket, from menu through game |
| `render.ts` | Canvas drawing |
| `geometry.ts` | Input snapping: where a click is allowed to become a move |
| `interp.ts` | Dead reckoning between server ticks |
| `expand.ts` | Turns a stored event log back into snapshots |
| `replay.ts` | Recording and playback |
| `civs.ts` `presets.ts` | The client's copy of the two balance tables |
| `settings.ts` | Personal settings, in two layers |
| `account.ts` | Session token and whatever the server last said about you |

## The game loop

`GameState.run()` ticks at `params.TICK_RATE` (20 Hz) and takes a broadcast
callback, so the loop itself does not know rooms exist. Each tick:

1. refill mana,
2. advance every piece through `physics.py`, which sweeps for the first contact
   rather than stepping and checking, so a fast piece cannot pass through
   another,
3. serialise the board and hand it to the callback.

The board is continuous: x and y both run `0..BOARD_SIZE`, and a move is a
destination point, not a square. Between snapshots the client extrapolates in a
straight line (`interp.ts`), which is why velocity is on the wire.

## The protocol

One JSON message per frame over one socket, defined in `shared/protocol.py` and
mirrored in `web/src/protocol.ts`. `VERSION` must match in both files:
`deploy.sh` refuses to ship a mismatch, and the server announces its version in
`SERVER_HELLO` so a browser holding a stale cached bundle says "reload" instead
of failing in ways that look like game bugs. Bump it whenever a message shape
changes in a way an old client cannot handle.

Messages fall into four groups: the lobby (create, join, quick match, rejoin,
ready, leave, rematch), the game (queue move, state, rejected, over), accounts
(sign up, in, out, resume, settings), and the read-only lists (rooms, online,
profile, games, one stored game).

## Rooms and seats

A seat is held by a token, not by a socket, so a player who reloads or loses
their connection can reclaim the colour they were playing. During a game the
reservation lasts `DISCONNECT_GRACE` (30 s) before the absent player forfeits.
In the lobby a seat is released at once when somebody leaves on purpose, and
after the same grace when a socket simply dies - a reservation with no expiry
is what once told a returning player "room is full" while telling their
opponent "not here yet" forever.

Solo rooms seat one connection in both colours, so anything that walks
`Room.clients` has to cope with seeing the same connection twice.

## Where authority lives

The server owns everything a game can be won with:

- move legality (`rules.py`) and what actually happens (`physics.py`),
- the balance tables. A client sends the *name* of a civilization or a tempo,
  never the numbers. Bounds-checking numbers a client chose is not validation,
  it is a smaller menu of cheats,
- who may see what, per seat,
- whether a game is rated (`rating.rated_reason`, which takes the account ids
  so that one person on two tabs is visible as a game against yourself).

The client owns only what it can be trusted with: drawing, input snapping
(`geometry.ts` decides which clicks are offered, and the server checks the
result anyway), and personal settings.

## The same rule in two languages

Four things exist in both Python and TypeScript, because the browser has to run
them and the server has to be the authority on them. Each pair has a test that
runs the TypeScript under `node --experimental-strip-types` and compares:

| Pair | Test |
|---|---|
| `rules.py` / `geometry.ts` | `tools/parity_test.py` |
| `civs.py` / `civs.ts`, `presets.py` / `presets.ts` | `tools/civ_parity.py` |
| `recorder.py` / `expand.ts` | `tools/replay_test.py` |
| `user_settings.py` / `settings.ts` | `tools/settings_test.py` |

Two copies of a table need a test, not a comment. These are in `deploy.sh` for
that reason: a drift between them is a game played on numbers nobody chose.

## Hidden information

The room's view settings decide what a player is told about their opponent:
mana, preparation, cooldown, destinations. Filtering happens on the server, in
`GameState.to_dict(viewer)`, because a client can read its own socket.

Blanking a field is not enough on its own. With destinations hidden, a mover's
`state_timer` is time-to-arrival, so `x + vel * timer` reconstructs the
destination exactly. The fix is to clip the timer to the interpolation horizon
(`HIDDEN_TIMER_CAP`), not to blank the velocity, which the client needs in
order to draw. Ask what is derivable from a snapshot, not what is in it.

## Recording and replay

A snapshot is 6.9 KB and a five minute game is 41 MB of them, so a recording is
not snapshots. `recorder.py` writes the *effects*: the moments where reality
departed from the cheap prediction the player will run. That is about 14 KB
gzipped for the same game, it costs the server no CPU to play back, and it
survives `physics.py` being rebalanced - an effects log says what happened,
where a move log would silently become a different game.

`web/src/expand.ts` turns the log back into the exact frames the server
broadcast, so `Recording`, `Player` and `Renderer` took stored games unchanged.
`recorder.FORMAT` is checked before expanding.

Hiding is a rule of playing, not of watching: the stored log holds everything,
including the preparation and destinations the room hid at the time, and a
replay simply draws it.

## Accounts, ratings, and playing anonymously

Local accounts: a name and a password, no email, and therefore no reset flow -
the most-attacked component of an auth system is the one that is not there.
Passwords are argon2id at OWASP's minimum parameters, and every hash runs in
`asyncio.to_thread`: this process also runs every live game's 20 Hz tick, so a
hash on the loop stalls every game on the machine. Sessions store the sha256 of
the token, never the token.

`users.provider` exists so that adding OAuth later is a new row value rather
than a migration of every account.

Ratings are Elo, one row per player per tempo. Anonymous play is first class:
a game with one or two unknown players is stored in the same table with the
same recording, and is replayable by whoever holds the id. What a player
without an account gives up is being able to find their games from another
browser, and nothing else - the browser keeps the id in localStorage.

## Personal settings

Two layers, and the rule between them is the whole design. The device keeps its
values in localStorage; a signed-in account keeps the ones it has an opinion
about, and those override the device's. Changing a setting while signed in
writes both, so signing out leaves the machine as its owner set it up.

An account stores only the keys that were changed while signed in. An absent
key is not a default, it is no opinion, so a fresh account never resets a
browser somebody has already configured, and signing in adopts the account's
settings rather than pushing the machine's onto the account.

## Persistence

`server/db.py` is the entire data layer. Migrations in `migrations/*.sql` are
applied in filename order at `db.connect()`, one transaction each, recorded in
`schema_migrations`. Note that `connect()` reads `.env`, so starting the server
locally applies pending migrations to whatever `DATABASE_URL` points at.

## Running it locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-server.txt
.venv/bin/python -m server.main --port 8799     # add DATABASE_URL= to skip the db
cd web && npm install && npm run dev            # vite, port 5173
```

The system python may have no `websockets` and may refuse to install one
(PEP 668), which is why the venv is not optional.

## Deploying

`deploy.sh` runs the checks, then deploys the server, then the client. The
order is forced: the bundle bakes in `VITE_SERVER_URL` and the server's
`ALLOWED_ORIGINS` names the client, so the server has to accept the new client
before that client exists. `--skip-checks` skips the checks and is only safe
when they have just passed on the same tree.

`npm run deploy`, not `wrangler deploy`: wrangler only uploads `./dist`, so
calling it alone ships whatever happened to be built last.

## Tests

There is no unit test suite. There are tools, and `deploy.sh` runs the ones
that need nothing but python and node:

| Tool | Needs |
|---|---|
| `parity_test.py` `civ_parity.py` `replay_test.py` `settings_test.py` | node |
| `pawn_test.py` `visibility_test.py` `rating_test.py` | nothing |
| `civ_table.mjs` | node; prints the civilization budget |
| `fake_client.py` `lobby_test.py` `solo_test.py` | a running server |
| `accounts_test.py` `db_test.py` `balance.py` | `DATABASE_URL` |

The ones that need a server or a database are deliberately not in `deploy.sh`:
that script has to work on a machine with neither.
