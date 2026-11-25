# Plan

## Social features and admin (agreed 2026-11-25)

Work on a branch (`social-and-admin`), verify locally, do not deploy. The user
ships it.

Decisions taken:
- Spectators see the **full, unfiltered game, live**. Accepted risk: a
  spectator can tell a player the opponent's mana and destinations. The stored
  replay already shows the same information after the game.
- Friends are **request and accept** (mutual).
- Anonymous visitors appear in the Online tab as **"Guest N"**.
- Admins are named in a **Fly secret**, `ADMIN_USERS=name1,name2`. Not in the
  repo, not in the database. Changing it restarts the machine, which ends any
  live games.

Order: 1 -> 2 -> 3 -> 4 -> 5. Each phase is usable on its own.

### Phase 1: Online tab
- [x] New top-level tab **Online**. The "Who is online" panel moves there from Play.
- [x] Every connection gets a session label: account name, or `Guest N` from a
      server counter. It is not stored and is not the same across reloads.
- [x] Each row shows a status: in the lobby / playing vs X / watching. A
      **Watch** button appears when the person is in a running game (phase 2),
      and a profile link appears for signed-in players.
- [x] Still polled (`LIST_ONLINE`), not pushed. The existing `MAX_ONLINE_LISTED`
      cap stays. A tab with 1000 visitors gets a count plus the first N.
- [x] Test: `tools/social_test.py` (all five phases share one file).

### Phase 2: Spectating
- [x] `SPECTATE {user_id | label | code}` joins a running room as a
      spectator. A spectator has no colour and no token, and is listed in
      `room.spectators`.
- [x] Each tick, the unfiltered snapshot is serialised **once** and the same
      bytes go to every spectator, so a spectator costs a send, not a
      `to_dict`.
- [x] Guards: a spectator cannot queue moves, ready up, resign or rematch. Its
      disconnect forfeits nothing. The "already in a room" guard covers
      spectating too (the `on_rejoin` lesson). Anything that walks
      `Room.clients` is unaffected, because spectators are not in it.
- [x] Per-room cap (e.g. 10) and a server-wide cap. Bandwidth: a snapshot is
      6.9 KB at 20 Hz, about 140 KB/s or 40 MB per five-minute game per
      spectator. Check Fly's egress price before raising the caps.
- [x] Players see "N watching" on the board. Spectators see both name plates,
      both mana bars, the clock-free result, and then the stored replay via
      `GAME_SAVED`.
- [x] Entry points: the Watch button in the Online tab, "Watch live game" on a
      profile whose owner is playing, and admin's game list (phase 3).
- [x] `LEAVE_ROOM` works for a spectator, and the client teardown
      (`exitToLobby`) handles a spectator.
- [x] Test (in `tools/social_test.py`): the spectator receives frames with
      both manas, its moves are refused, it leaving or dropping does not end
      the game, and a rematch lets it go.
- [ ] Not tested: spectating a solo game (one socket in both seats).

### Phase 3: Admin tab
- [x] Server reads `ADMIN_USERS` (case-insensitive names) at startup. Local runs
      set it in the environment.
- [x] `AUTH_STATE` carries `admin: true|false`. The client shows the tab only
      then, as display only.
- [x] `ADMIN_STATS` and `ADMIN_LIST_*` are checked against the **socket's
      signed-in account on every request**. A patched client that shows the
      tab gets refusals.
- [x] Live: connections, signed-in vs guests, rooms by state, games in progress
      (each with Watch), ticks per second (the slow-motion signal).
- [x] Stored: total users, new users 7d/30d, games total/today/7d, games per
      day, a paged list of all games (players, tempo, result, date, replay),
      and a paged list of users (name, joined, last seen, games, online).
      Ratings are on each user's card rather than in the list.
- [x] Read-only. No IP addresses, nothing a player has not already made
      public, apart from last-seen times.
- [x] Test (in `tools/social_test.py`): anonymous and non-admin accounts are
      refused every admin message; an admin gets the numbers. Checked to fail
      with the guard disabled.

### Phase 4: Friends
- [x] Migration `004_friends.sql`: `friendships(user_lo, user_hi, requested_by,
      status pending|accepted, created_at)`, one row per pair (lo < hi).
      Applied to the live Supabase on the 2026-11-25 deploy.
- [x] Messages: `FRIEND_REQUEST {name|id}`, `FRIEND_RESPOND {id, accept}`,
      `FRIEND_REMOVE {id}`, `LIST_FRIENDS` -> `FRIEND_LIST` (friends with
      online/playing status, incoming requests, outgoing requests). All are
      polled, with no unsolicited pushes.
- [x] A request both ways at once becomes a friendship. Caps: about 200 friends
      and 50 pending outgoing.
- [x] Profile button: Add friend / Request sent / Accept / Friends (remove).
      The Online tab gets a Friends section at the top, plus incoming
      requests, with a count badge on the tab.
- [x] Test (in `tools/social_test.py`), against a throwaway local Postgres
      rather than cleaning up rows in the live one.

### Phase 5: Challenges
- [x] `CHALLENGE {id, tempo}`. The server checks that the friendship is
      accepted, the friend is online and not in a game, and there is no open
      challenge between the pair. Changed from the plan: the terms are one of
      the three preset tempos only, like quick match, so a challenge game is
      always rated. The full Create game settings would mean a second copy of
      that form.
- [x] The server pushes `CHALLENGE_RECEIVED` to **all** the friend's sockets.
      This is the first unsolicited message since `SERVER_HELLO`, so
      `fake_client` and every `_await_msg` helper must be checked (see "Adding a
      greeting frame broke every helper").
- [x] The recipient gets a banner with Accept / Decline and a 60 s countdown.
      Accept creates a private room at that tempo and seats both players
      (challenger white), who then land in the normal pre-game screen (civ
      pick, Ready). Anyone watching a game when it is accepted stops watching.
- [x] The challenger sees "waiting for X" with Withdraw. Decline, expiry, cancel
      or either side disconnecting closes it, and both sides are told.
- [x] In memory only, with no table. A restart loses open challenges, which is
      fine.
- [x] Test (in `tools/social_test.py`): non-friends, unknown tempo, one at a
      time, decline, withdraw, challenger disconnecting, accept, and a busy
      recipient.
- [ ] Not tested: the 60 s expiry (the test would have to wait a minute).

### Every phase
- [x] Protocol `VERSION` bumped in both `shared/protocol.py` and
      `web/src/protocol.ts` (deploy.sh refuses a mismatch).
- [x] New test commands in `COMMANDS.md`. `docs/architecture.md` updated.
- [x] Every new server rejection has a place on the screen that caused it.
