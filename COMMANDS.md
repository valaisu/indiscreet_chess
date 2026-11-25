# Commands

Run from the repository root unless a line starts with `cd`. Tools import
`server` and `shared`, so they need `PYTHONPATH=.`.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-server.txt   # server
.venv/bin/pip install -r requirements.txt                                    # pygame client
cd web && npm install
```

## Run locally

```bash
DATABASE_URL= .venv/bin/python -m server.main --port 8765   # game server, no database
cd web && npm run dev                                       # client on :5173, talks to ws://localhost:8765
```

Without `DATABASE_URL=` the server reads `.env` and applies pending
migrations to the live Supabase database. Other server flags: `--grace 300`
(no forfeits while debugging), `--max-conn-per-ip 50` and
`--max-rooms-per-ip 50` (local load tests), `--verbose`.

Point a dev client at another server with `?server=ws://host:port` in the URL.
Kill a stuck server with `fuser -k 8765/tcp`, never `pkill -f`.

Legacy pygame client: `.venv/bin/python host.py`.

## Checks

```bash
cd web && npm run typecheck
PYTHONPATH=. python3 tools/parity_test.py        # client snap vs server validate_move
PYTHONPATH=. python3 tools/civ_parity.py         # civ and tempo tables, TS vs Python
PYTHONPATH=. python3 tools/pawn_test.py          # pawn capture, promotion, en passant
PYTHONPATH=. python3 tools/castling_test.py      # castling partners stop overlapping once apart
PYTHONPATH=. python3 tools/visibility_test.py    # hidden information absent from snapshots
PYTHONPATH=. python3 tools/replay_test.py        # recorded games expand to the broadcast frames
PYTHONPATH=. python3 tools/settings_test.py      # device and profile settings layers
PYTHONPATH=. python3 tools/rating_test.py        # elo and the rated-game rule
```

These need a running server (the run command above, with `--port 8799`):

```bash
PYTHONPATH=. .venv/bin/python tools/lobby_test.py --url ws://localhost:8799
PYTHONPATH=. .venv/bin/python tools/solo_test.py --url ws://localhost:8799
PYTHONPATH=. .venv/bin/python tools/fake_client.py --pair --rooms 2 --duration 8 --url ws://localhost:8799
PYTHONPATH=. .venv/bin/python tools/fake_client.py --hostile --url ws://localhost:8799
```

Online tab, spectating, friends, challenges and admin, against a throwaway
local Postgres (never the live one: the test creates accounts):

```bash
/usr/lib/postgresql/16/bin/initdb -D /tmp/chesspg -A trust -U postgres
/usr/lib/postgresql/16/bin/pg_ctl -D /tmp/chesspg -o "-p 5499 -k '' -c listen_addresses=127.0.0.1" -l /tmp/chesspg.log start
psql -h 127.0.0.1 -p 5499 -U postgres -c "create database chess"
ADMIN_USERS=tadmin1 DATABASE_URL=postgresql://postgres@127.0.0.1:5499/chess .venv/bin/python -m server.main --port 8799
PYTHONPATH=. .venv/bin/python tools/social_test.py --url ws://localhost:8799
```

Admins in production: `fly secrets set ADMIN_USERS=name1,name2` (restarts the
machine, which ends live games).

## Deploy

```bash
./deploy.sh                          # all checks, then server (Fly), then client (Cloudflare)
fly deploy --remote-only --ha=false  # server only; --ha=false every time
cd web && npm run deploy             # client only (builds, then uploads dist/)
```

A server deploy ends every game in progress. Client-only deploys do not.

## Production

```bash
fly status                               # machine state
fly logs                                 # server log
fly machine start <id>                   # after Fly stops an idle machine
cd web && npx wrangler deployments list  # client versions; `npx wrangler rollback` reverts
```

If the server crash-loops on boot with `socket.gaierror`, the free Supabase
project has paused: restore it in the Supabase dashboard, then start the
machine.

## Analysis

```bash
.venv/bin/python -m tools.balance                      # civ win rates (needs DATABASE_URL)
PYTHONPATH=. .venv/bin/python tools/stress.py --bench  # CPU per game, no sockets
PYTHONPATH=. .venv/bin/python tools/stress.py --load --rooms 1,2,4,6,8
```
