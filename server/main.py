"""
WebSocket server entry point.

Hosts many concurrent games in one process. Run from the project root:

    python -m server.main [--port PORT] [--origin https://example.com]

Clients create or join rooms over the lobby protocol; see shared/protocol.py.
"""

import argparse
import asyncio
import http
import json
import logging
import math
import os
import re
import secrets
import time
from pathlib import Path

import websockets
from websockets.asyncio.server import serve

from . import (accounts, civs, db, params, presets, recorder, room as room_mod,
               user_settings)
from .room import Connection, RoomManager, RUNNING
from shared import protocol

log = logging.getLogger("server")

MAX_CONN_PER_IP = 5
MAX_MSG_PER_SEC = 40
MAX_MESSAGE_BYTES = 4096
MAX_CONNECTIONS = 200       # process-wide; Fly's proxy caps lower, this is the floor
MAX_JOIN_FAILS = 10         # wrong room codes before the socket is closed
MAX_ONLINE_LISTED = 100     # people in one ONLINE_LIST; the count is not capped
CHALLENGE_SECONDS = 60      # how long a challenge waits for an answer
GAMES_PAGE = 20             # stored games in one GAME_LIST or PROFILE
MAX_OFFSET = 10_000         # how deep anyone may page; a bound, not a policy

# Game ids are uuids. Checking the shape before the query keeps a malformed id
# from reaching Postgres as a cast error rather than a miss.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                      r"[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _offset(value: object) -> int:
    """A page offset from a client. Anything unusable is the first page."""
    try:
        return max(0, min(MAX_OFFSET, int(value)))     # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _refuse_constant(name: str) -> float:
    """Reject NaN and Infinity at the parser.

    Python's json accepts all three as bare literals, which no browser will
    ever send and which nothing downstream is written to survive: NaN passes
    every bounds check by failing every comparison, and once one reaches the
    game state the snapshot it lands in is no longer valid JSON for anybody.
    """
    raise ValueError(f"non-finite number: {name}")


def admin_names(raw: str | None) -> frozenset[str]:
    """The ADMIN_USERS secret: comma-separated account names. Lowercased,
    because names are case-insensitive everywhere else too (citext)."""
    return frozenset(n.strip().lower() for n in (raw or "").split(",") if n.strip())


class Challenge:
    """One player asking a friend for a game. Lives in memory only."""

    def __init__(self, src: Connection, dst_id: str, dst_name: str,
                 tempo: str) -> None:
        self.id = secrets.token_urlsafe(9)
        self.src = src
        self.src_user = dict(src.user or {})
        self.dst_id = dst_id
        self.dst_name = dst_name
        self.tempo = tempo
        self.task: asyncio.Task | None = None


# How busy a person is, most to least: a group of sockets for one account shows
# the busiest of them.
_STATUS_RANK = {"playing": 3, "in a room": 2, "watching": 1, "online": 0}


class Hub:
    def __init__(self, admins: frozenset[str] = frozenset()) -> None:
        self.rooms = RoomManager()
        self.conns: set[Connection] = set()
        # Sign-in attempts per address. Per-connection counting alone is not a
        # limit: MAX_CONN_PER_IP sockets each get a fresh budget, and a new
        # handshake is cheap.
        self.attempts = accounts.Attempts()
        # Account names allowed the admin tab. Read from the environment (a Fly
        # secret in production), so the repository says how the check works
        # and nothing about who passes it.
        self.admins = admins
        self.guests = 0                     # numbers "Guest N" labels
        self.challenges: dict[str, Challenge] = {}
        self.started = time.time()

    def conns_from(self, ip: str) -> int:
        return sum(1 for c in self.conns if c.ip == ip)

    def is_admin(self, conn: Connection) -> bool:
        return bool(conn.user) and conn.user["name"].lower() in self.admins

    @staticmethod
    def status_of(conn: Connection) -> str:
        if conn.room is not None:
            return "playing" if conn.room.state == RUNNING else "in a room"
        if conn.watching is not None:
            return "watching"
        return "online"

    def people(self) -> dict[str, dict]:
        """Everyone connected, one entry per person: by account when signed
        in, so two tabs are one person, and by socket otherwise.

        `watch` is the key to spectate them by, present only while they are
        playing a game that is running. It is a key and never a room code: a
        code is how a private room is joined, and this list goes to everyone.
        """
        out: dict[str, dict] = {}
        for c in self.conns:
            key = c.key
            status = self.status_of(c)
            entry = out.get(key)
            if entry is None:
                entry = out[key] = {
                    "key": key,
                    "id": c.user["id"] if c.user else None,
                    "name": c.user["name"] if c.user else c.label,
                    "guest": c.user is None,
                    "status": "online",
                    "watch": None,
                    "opponent": None,
                    "solo": False,
                }
            if _STATUS_RANK[status] <= _STATUS_RANK[entry["status"]]:
                continue
            entry["status"] = status
            if status == "playing":
                room = c.room
                entry["watch"] = key
                entry["solo"] = room.solo
                other = "black" if c.color == "white" else "white"
                entry["opponent"] = (room.user[other] or {}).get("name")
        return out

    def presence(self, viewer: Connection | None = None) -> dict:
        """Who is connected right now.

        Everybody is listed, guests included, under the label this server
        gave their socket. `users` is the signed-in subset in the shape older
        clients read; `people` is the whole list.
        """
        people = self.people()
        named = sorted((p for p in people.values() if not p["guest"]),
                       key=lambda p: p["name"].lower())
        guests = sorted((p for p in people.values() if p["guest"]),
                        key=lambda p: int(p["name"].split()[-1]))
        return {
            "count": len(self.conns),
            "signed_in": len(named),
            "users": [{"id": p["id"], "name": p["name"]}
                      for p in named[:MAX_ONLINE_LISTED]],
            "people": (named + guests)[:MAX_ONLINE_LISTED],
            "you": viewer.key if viewer else None,
        }

    def playing_room(self, key: str):
        """The running game the person with this presence key is playing in."""
        for c in self.conns:
            if c.key == key and c.room is not None and c.room.state == RUNNING:
                return c.room
        return None

    def conns_of(self, user_id: str) -> list[Connection]:
        return [c for c in self.conns if c.user and c.user["id"] == user_id]

    # -- message handlers ---------------------------------------------------

    async def on_create(self, conn: Connection, msg: dict) -> None:
        if conn.busy():
            await conn.error("already in a room")
            return
        # An open game is a public room: it goes in the LIST_ROOMS listing and
        # quick match can land in it. A private one is reachable by code only.
        room = await self.rooms.create(conn, msg, public=bool(msg.get("public")))
        if room is None:
            return
        await conn.send({
            "type":  protocol.ROOM_CREATED,
            "code":  room.code,
            "color": conn.color,
            "token": conn.token,
            "solo":  room.solo,
        })
        if room.solo:
            room.start_if_ready()
        else:
            await room.notify_state()

    async def on_join(self, conn: Connection, msg: dict) -> None:
        if conn.busy():
            await conn.error("already in a room")
            return
        code = str(msg.get("code", "")).strip().upper()
        room = await self.rooms.join(conn, code)
        if room is None:
            # A room code is four characters, and the reply says which of "no
            # such room", "full" and "already started" it was - enough to sweep
            # the space and walk into someone's private lobby. Ten wrong codes
            # and this socket is done; another attempt costs a new handshake,
            # and MAX_CONN_PER_IP bounds how many of those can be in flight.
            conn.join_fails += 1
            if conn.join_fails >= MAX_JOIN_FAILS:
                log.warning("closing %s after %d failed joins", conn.ip, conn.join_fails)
                asyncio.create_task(conn.ws.close(1008, "too many failed joins"))
            return
        conn.join_fails = 0     # a code that worked; this player is not sweeping
        await conn.send({
            "type":  protocol.ROOM_JOINED,
            "code":  room.code,
            "color": conn.color,
            "token": conn.token,
        })
        await room.notify_state()
        room.start_if_ready()

    async def on_quick_match(self, conn: Connection, msg: dict) -> None:
        if conn.busy():
            await conn.error("already in a room")
            return
        room = await self.rooms.quick_match(conn, msg)
        if room is None:
            return
        await conn.send({
            "type":  protocol.ROOM_JOINED,
            "code":  room.code,
            "color": conn.color,
            "token": conn.token,
        })
        await room.notify_state()
        room.start_if_ready()

    async def on_rejoin(self, conn: Connection, msg: dict) -> None:
        # Same guard the other three entry points have. Without it one socket
        # could sit in two rooms at once: conn.room names only the second, so
        # the first never hears about the disconnect, never starts its grace
        # forfeit, and keeps its game loop running against nobody.
        if conn.busy():
            await conn.error("already in a room")
            return
        code  = str(msg.get("code", "")).strip().upper()
        token = str(msg.get("token", ""))
        color = self.rooms.find_rejoin(code, token)
        if color is None:
            await conn.error("cannot rejoin")
            return
        room = self.rooms.rooms[code]
        conn.token = token
        await room.rejoin(conn, color)
        await conn.send({
            "type":  protocol.ROOM_JOINED,
            "code":  room.code,
            "color": color,
            "token": token,
        })
        # The room as it stands, to both seats. Without this the player coming
        # back has ROOM_JOINED and nothing else, so their screen draws an
        # opponent it has never been told about: "not here yet", about
        # somebody sitting right there waiting for them.
        await room.notify_state()
        log.info("room %s: %s rejoined", code, color)

    async def on_set_ready(self, conn: Connection, msg: dict) -> None:
        room = conn.room
        if room is None or room.state != room_mod.LOBBY or conn.color is None:
            return
        # A seat picks a civilization by name, and nothing else. Any `params`
        # in this message is ignored: the numbers are derived from the room's
        # tempo on the server (civs.resolve), because a client trusted to send
        # its own cooldown will send a zero. Checking the name against the
        # table is also what keeps it out of the opponent's DOM.
        civ = msg.get("civ")
        if civ is not None and civ not in civs.CIV_NAMES:
            log.warning("rejected civ from %s: %r", conn.ip, civ)
            await conn.error("unknown civilization")
            return
        # One client holds both seats in solo, so it says which one it is
        # readying. Anywhere else the seat is the one the server assigned, and
        # a solo client that names no seat readies both with the same choice.
        seats = [conn.color]
        if room.solo:
            named = msg.get("color")
            seats = [named] if named in ("white", "black") else ["white", "black"]
        # A civilization multiplies the tempo, so this is where a piece-size
        # modifier lands: the opening position has to be checked here too.
        # Against this seat's own tempo, which a balanced room sets per side.
        for seat in seats:
            _, reason = civs.resolve_checked(room.base_params[seat], civ)
            if reason:
                log.warning("rejected ready from %s: %s", conn.ip, reason)
                await conn.error(reason)
                return
        for seat in seats:
            room.set_ready(seat, bool(msg.get("ready")), civ)
        await room.notify_state()
        room.start_if_ready()

    # -- accounts -----------------------------------------------------------

    async def _auth_guard(self, conn: Connection) -> str | None:
        """Why this socket may not try to sign in right now, or None."""
        if not db.enabled():
            return "accounts are not available on this server"
        conn.auth_attempts += 1
        if conn.auth_attempts > accounts.MAX_ATTEMPTS_PER_CONN:
            return "too many attempts; reconnect and try again"
        if not self.attempts.allow(conn.ip):
            log.warning("auth rate limit hit from %s", conn.ip)
            return "too many attempts from this address; try again later"
        return None

    async def _send_auth(self, conn: Connection, identity: dict | None) -> None:
        before = (conn.user or {}).get("id")
        conn.user = None if identity is None else {"id": identity["id"],
                                                   "name": identity["name"]}
        if before and before != (conn.user or {}).get("id"):
            # A challenge belongs to the account that sent it, and this socket
            # no longer speaks for that account.
            await self._drop_challenges(conn, "the challenger signed out")
        user = None if identity is None else {**identity,
                                              "admin": self.is_admin(conn)}
        await conn.send({"type": protocol.AUTH_STATE, "user": user})

    async def on_sign_up(self, conn: Connection, msg: dict) -> None:
        blocked = await self._auth_guard(conn)
        if blocked:
            await conn.send({"type": protocol.AUTH_ERROR, "reason": blocked})
            return
        identity, reason = await accounts.sign_up(msg.get("name"),
                                                  msg.get("password"))
        if reason:
            await conn.send({"type": protocol.AUTH_ERROR, "reason": reason})
            return
        log.info("new account %r from %s", identity["name"], conn.ip)
        await self._send_auth(conn, identity)

    async def on_sign_in(self, conn: Connection, msg: dict) -> None:
        blocked = await self._auth_guard(conn)
        if blocked:
            await conn.send({"type": protocol.AUTH_ERROR, "reason": blocked})
            return
        identity, reason = await accounts.sign_in(msg.get("name"),
                                                  msg.get("password"))
        if reason:
            await conn.send({"type": protocol.AUTH_ERROR, "reason": reason})
            return
        await self._send_auth(conn, identity)

    async def on_resume_session(self, conn: Connection, msg: dict) -> None:
        """A stored token from a previous visit. Not rate limited: the token is
        32 random bytes, so there is nothing here worth guessing at, and a
        reload must not spend the sign-in budget."""
        if not db.enabled():
            await self._send_auth(conn, None)
            return
        await self._send_auth(conn, await accounts.resume(msg.get("token", "")))

    async def on_sign_out(self, conn: Connection, msg: dict) -> None:
        await accounts.sign_out(msg.get("token", ""))
        await self._send_auth(conn, None)

    async def on_set_settings(self, conn: Connection, msg: dict) -> None:
        """Store the settings this account has an opinion about.

        Silent for a signed-out socket: the device keeps its own copy either
        way, so there is nothing for the player to do about a failure here and
        nothing lost by it. `clean` drops what it does not recognise rather
        than refusing, so a newer client loses one setting on an older server.
        """
        if conn.user is None:
            return
        await db.set_user_settings(conn.user["id"],
                                   user_settings.clean(msg.get("settings")))

    # -- stored games -------------------------------------------------------

    async def on_list_games(self, conn: Connection, msg: dict) -> None:
        if conn.user is None:
            await conn.send({"type": protocol.GAME_LIST,
                             "games": [], "offset": 0, "total": 0})
            return
        page = await db.recent_games(conn.user["id"], limit=GAMES_PAGE,
                                     offset=_offset(msg.get("offset")))
        await conn.send({"type": protocol.GAME_LIST, **page})

    async def on_get_game(self, conn: Connection, msg: dict) -> None:
        """One stored game with its recording, for replay. No sign-in needed.

        Anybody holding the id may watch: a game once played is a public
        record, and looking through an opponent's past games is most of the
        point of having profiles at all. It used to ask for an account, which
        left a player without one unable to watch the game they had just
        finished: their client fell back to the frames it had been sent, which
        the room's visibility settings had already stripped. Two ways into a
        replay showing two different games is what that gate cost.

        It bought little in return. `on_get_profile` needs no account either
        and answers with a page of game ids, so the ids were already readable
        anonymously - only the recordings behind them were not. What is left
        is that an id is unguessable, which is the model the schema describes:
        replayable by whoever holds the link.

        Worth knowing what that gives away. A recording is the whole truth,
        including the preparation and the destinations the room's visibility
        settings hid from the opponent while it was being played. That is over
        by the time it is stored, but it does mean past games can be studied
        in a detail the live game refused.
        """
        game_id = str(msg.get("id", ""))
        if not _UUID_RE.match(game_id):
            await conn.error("no such game")
            return
        record = await db.get_game(game_id)
        if record is None:
            await conn.error("no such game")
            return
        if record["log_format"] != recorder.FORMAT:
            await conn.error("this recording was made by an older version "
                             "and can no longer be replayed")
            return
        await conn.send({"type": protocol.GAME_RECORD, "id": record["id"],
                         "recording": record["recording"]})

    # -- finding people and games -------------------------------------------

    async def on_list_rooms(self, conn: Connection, msg: dict) -> None:
        await conn.send({"type": protocol.ROOM_LIST,
                         "rooms": self.rooms.open_rooms()})

    async def on_list_online(self, conn: Connection, msg: dict) -> None:
        await conn.send({"type": protocol.ONLINE_LIST, **self.presence(conn)})

    async def on_get_profile(self, conn: Connection, msg: dict) -> None:
        """One player's public card: name, ratings, and a page of their games.

        No sign-in needed for the card itself - a name and a rating are what
        the online list already shows. The games are results only; the
        recording behind one is fetched separately, and that does ask for a
        signed-in player.
        """
        if not db.enabled():
            await conn.error("accounts are not available on this server")
            return
        uid = str(msg.get("id") or "")
        name = str(msg.get("name") or "")
        if uid and not _UUID_RE.fullmatch(uid):
            await conn.error("no such player")
            return
        if name and not accounts.NAME_RE.fullmatch(name):
            await conn.error("no such player")
            return
        profile = await db.public_profile(user_id=uid or None, name=name or None)
        if profile is None:
            await conn.error("no such player")
            return
        page = await db.recent_games(profile["id"], limit=GAMES_PAGE,
                                     offset=_offset(msg.get("offset")))
        # Whether they are here and what they are doing, so the card can offer
        # to watch their game; and where you stand with them, for its friend
        # button. The second only means anything to somebody signed in.
        here = self.people().get(f"u:{profile['id']}")
        live = {
            "status": here["status"] if here else "offline",
            "watch": here["watch"] if here else None,
            "friend": None,
        }
        if conn.user:
            live["friend"] = ("self" if conn.user["id"] == profile["id"] else
                              await db.friendship(conn.user["id"], profile["id"]))
        await conn.send({"type": protocol.PROFILE, **profile, **page, **live})

    async def on_queue_move(self, conn: Connection, msg: dict) -> None:
        room = conn.room
        if room is None or room.state != RUNNING:
            return
        if not conn.allow_move():
            return                      # silently drop; a real client can't hit this
        dest = msg.get("destination") or [0.0, 0.0]
        try:
            dx, dy = float(dest[0]), float(dest[1])
        except (TypeError, ValueError, IndexError):
            await conn.error("bad destination")
            return
        # NaN passes every comparison a rule is written as: the distance check,
        # the direction sector, and "can you afford it". One such move used to
        # subtract NaN from the mana pool, after which no move ever cost
        # anything again. Nothing downstream is written to survive it.
        if not (math.isfinite(dx) and math.isfinite(dy)):
            log.warning("non-finite destination from %s", conn.ip)
            await conn.error("bad destination")
            return
        rejection = room.game.queue_move(str(msg.get("piece_id", "")), (dx, dy),
                                         conn.color or "white")
        if rejection:
            await conn.send(rejection)

    async def on_resign(self, conn: Connection, msg: dict) -> None:
        """Give up the game in progress. The seat stays connected, so the
        result arrives as a normal GAME_OVER rather than a disconnect."""
        room = conn.room
        if room is None or room.state != RUNNING or room.game is None:
            return
        if room.game.game_over:
            return
        # In solo both seats are this client, so it says which one gives up.
        color = conn.color or "white"
        if room.solo and msg.get("color") in ("white", "black"):
            color = msg["color"]
        log.info("room %s: %s resigns", room.code, color)
        room.game.forfeit(color)

    async def on_rematch(self, conn: Connection, msg: dict) -> None:
        """Ask for the same room again. The seats and the tempo stay; both
        sides then pick a civilization and ready up through the ordinary path.

        Both players have to ask, like readying up. One press used to reset
        the room, which pulled the other player off the result they were still
        reading and into the civilization screen without being consulted.

        Idempotent on purpose: the second press arrives at a room already back
        in the lobby, and answering that with an error would put a failure on
        the screen of whoever was slower."""
        room = conn.room
        if room is None or conn.color is None:
            return
        # The room is marked FINISHED a moment after the last frame goes out,
        # so a press on that frame can arrive while it still says RUNNING. The
        # game being over is the condition, not the bookkeeping around it.
        over = room.game is not None and room.game.game_over
        if room.state == RUNNING and not over:
            return
        if room.state != room_mod.LOBBY:
            if not room.solo and any(room.clients[c] is None
                                     for c in ("white", "black")):
                await conn.error("your opponent has left")
                return
            # A solo client is both seats, so it agrees with itself at once.
            for seat in (("white", "black") if room.solo else (conn.color,)):
                room.rematch[seat] = True
            # A no-op while the game loop is still winding up; the room runs
            # this again itself the moment it is finished.
            room.maybe_rematch()
        await room.notify_state()

    async def on_leave(self, conn: Connection, msg: dict) -> None:
        if conn.watching is not None:
            await conn.watching.remove_spectator(conn)
            return
        if conn.room is not None:
            # leave(), not on_disconnect(): an announced exit gives the seat
            # back immediately, so the room can be joined again and quick
            # match can put this player back into it.
            await conn.room.leave(conn)
            self.rooms.sweep()
            conn.room = None
            conn.color = None

    # -- watching -------------------------------------------------------------

    async def on_spectate(self, conn: Connection, msg: dict) -> None:
        """Watch the game somebody is playing, by their presence key.

        A refusal is a SPECTATE_END rather than an ERROR: the button was on the
        online list or a player's card, and that is where the answer belongs,
        not on the Play tab's status line.
        """
        async def refuse(reason: str) -> None:
            await conn.send({"type": protocol.SPECTATE_END, "reason": reason})

        if conn.busy():
            return await refuse("leave the game you are in first")
        room = self.playing_room(str(msg.get("key", "")))
        if room is None:
            return await refuse("that game has ended")
        if len(room.spectators) >= room_mod.MAX_SPECTATORS_PER_ROOM:
            return await refuse("that game has as many spectators as it can take")
        if self.rooms.spectators() >= room_mod.MAX_SPECTATORS:
            return await refuse("the server is not taking more spectators right now")
        log.info("room %s: %s is watching", room.code, conn.key)
        await room.add_spectator(conn)

    # -- friends --------------------------------------------------------------

    async def _send_friends(self, conn: Connection, note: str | None = None) -> None:
        """The whole list, whatever was asked: every friend message answers
        with it, so the panel never has to work out what one change did."""
        if conn.user is None or not db.enabled():
            await conn.send({"type": protocol.FRIEND_LIST, "friends": [],
                             "incoming": [], "outgoing": [],
                             "note": note or "Sign in to add friends."})
            return
        rows = await db.friends_of(conn.user["id"])
        people = self.people()
        friends, incoming, outgoing = [], [], []
        for r in rows:
            if r["rel"] == "friends":
                here = people.get(f"u:{r['id']}")
                friends.append({
                    "id": r["id"], "name": r["name"],
                    "status": here["status"] if here else "offline",
                    "watch": here["watch"] if here else None,
                    "opponent": here["opponent"] if here else None,
                })
            else:
                (incoming if r["rel"] == "incoming" else outgoing).append(
                    {"id": r["id"], "name": r["name"]})
        await conn.send({"type": protocol.FRIEND_LIST, "friends": friends,
                         "incoming": incoming, "outgoing": outgoing,
                         "note": note})

    async def _friend_target(self, conn: Connection, msg: dict) -> dict | None:
        """The account a friend message is about, by id or by name."""
        uid = str(msg.get("id") or "")
        name = str(msg.get("name") or "").strip()
        if uid:
            if not _UUID_RE.fullmatch(uid):
                return None
            return await db.public_profile(user_id=uid)
        if name and accounts.NAME_RE.fullmatch(name):
            return await db.public_profile(name=name)
        return None

    async def on_list_friends(self, conn: Connection, msg: dict) -> None:
        await self._send_friends(conn)

    async def on_friend_request(self, conn: Connection, msg: dict) -> None:
        if conn.user is None or not db.enabled():
            return await self._send_friends(conn)
        other = await self._friend_target(conn, msg)
        if other is None:
            return await self._send_friends(conn, "There is no player by that name.")
        reason = await db.friend_request(conn.user["id"], other["id"])
        if reason:
            return await self._send_friends(conn, reason[0].upper() + reason[1:] + ".")
        rel = await db.friendship(conn.user["id"], other["id"])
        await self._send_friends(
            conn, f"You and {other['name']} are now friends." if rel == "friends"
            else f"Friend request sent to {other['name']}.")

    async def on_friend_respond(self, conn: Connection, msg: dict) -> None:
        if conn.user is None or not db.enabled():
            return await self._send_friends(conn)
        other = await self._friend_target(conn, msg)
        if other is None or not await db.friend_respond(
                conn.user["id"], other["id"], bool(msg.get("accept"))):
            return await self._send_friends(conn, "That request is no longer there.")
        await self._send_friends(conn)

    async def on_friend_remove(self, conn: Connection, msg: dict) -> None:
        if conn.user is None or not db.enabled():
            return await self._send_friends(conn)
        other = await self._friend_target(conn, msg)
        if other is not None:
            await db.friend_remove(conn.user["id"], other["id"])
        await self._send_friends(conn)

    # -- challenges -----------------------------------------------------------

    async def _close_challenge(self, ch: Challenge, reason: str) -> None:
        """End a challenge and tell both sides, every tab of the challenged."""
        if self.challenges.pop(ch.id, None) is None:
            return
        if ch.task is not None and ch.task is not asyncio.current_task():
            ch.task.cancel()
        msg = {"type": protocol.CHALLENGE_CLOSED, "id": ch.id, "reason": reason}
        for c in {ch.src, *self.conns_of(ch.dst_id)}:
            await c.send(msg)

    async def _drop_challenges(self, conn: Connection, reason: str) -> None:
        """Close what this socket sent, and what was waiting on an account
        that has no socket left to answer it from."""
        for ch in list(self.challenges.values()):
            if ch.src is conn:
                await self._close_challenge(ch, reason)
            elif not any(c is not conn for c in self.conns_of(ch.dst_id)):
                await self._close_challenge(ch, f"{ch.dst_name} went offline")

    async def _expire(self, ch: Challenge) -> None:
        try:
            await asyncio.sleep(CHALLENGE_SECONDS)
        except asyncio.CancelledError:
            return
        await self._close_challenge(ch, "nobody answered in time")

    async def on_challenge(self, conn: Connection, msg: dict) -> None:
        """Ask a friend for a game at one of the three tempos.

        The game it makes is the one a quick match would: the server's own
        preset, standard visibility, so it is rated when both are signed in -
        which a challenge between two accounts always is.
        """
        async def refuse(reason: str) -> None:
            await conn.send({"type": protocol.CHALLENGE_CLOSED, "id": None,
                             "reason": reason})

        if conn.user is None or not db.enabled():
            return await refuse("sign in to challenge friends")
        tempo = msg.get("tempo")
        if tempo not in presets.MODES:
            return await refuse("unknown tempo")
        uid = str(msg.get("id") or "")
        if not _UUID_RE.fullmatch(uid):
            return await refuse("no such player")
        if conn.room is not None:
            return await refuse("leave the game you are in first")
        me = conn.user["id"]
        for ch in self.challenges.values():
            if ch.src_user.get("id") == me:
                return await refuse("you already have a challenge waiting")
            if {ch.src_user.get("id"), ch.dst_id} == {me, uid}:
                return await refuse("there is already a challenge between you")
        if await db.friendship(me, uid) != "friends":
            return await refuse("you can only challenge friends")
        targets = self.conns_of(uid)
        other = await db.public_profile(user_id=uid)
        name = other["name"] if other else "they"
        if not targets:
            return await refuse(f"{name} is not online")
        if any(c.room is not None for c in targets):
            return await refuse(f"{name} is in a game")

        ch = Challenge(conn, uid, name, tempo)
        self.challenges[ch.id] = ch
        ch.task = asyncio.create_task(self._expire(ch))
        log.info("challenge %s: %s -> %s (%s)", ch.id, conn.user["name"], name, tempo)
        await conn.send({"type": protocol.CHALLENGE_SENT, "id": ch.id,
                         "to": {"id": uid, "name": name}, "tempo": tempo,
                         "seconds": CHALLENGE_SECONDS})
        for c in targets:
            await c.send({"type": protocol.CHALLENGE_RECEIVED, "id": ch.id,
                          "from": {"id": me, "name": conn.user["name"]},
                          "tempo": tempo, "seconds": CHALLENGE_SECONDS})

    async def on_challenge_cancel(self, conn: Connection, msg: dict) -> None:
        ch = self.challenges.get(str(msg.get("id", "")))
        if ch is not None and ch.src is conn:
            await self._close_challenge(ch, f"{ch.src_user.get('name')} withdrew it")

    async def on_challenge_answer(self, conn: Connection, msg: dict) -> None:
        ch = self.challenges.get(str(msg.get("id", "")))
        if ch is None or conn.user is None or conn.user["id"] != ch.dst_id:
            await conn.send({"type": protocol.CHALLENGE_CLOSED,
                             "id": msg.get("id"),
                             "reason": "that challenge is no longer open"})
            return
        if not msg.get("accept"):
            await self._close_challenge(ch, f"{ch.dst_name} declined")
            return
        if conn.room is not None:
            await conn.send({"type": protocol.CHALLENGE_CLOSED, "id": None,
                             "reason": "leave the game you are in first"})
            return
        src = ch.src
        if (src not in self.conns or src.room is not None or not src.user
                or src.user["id"] != ch.src_user.get("id")):
            await self._close_challenge(
                ch, f"{ch.src_user.get('name')} is no longer available")
            return
        # Watching something is not a reason to refuse a game, on either side:
        # whoever is watching stops.
        for c in (conn, src):
            if c.watching is not None:
                await c.watching.remove_spectator(c)
        room = await self.rooms.create(src, {"params": dict(presets.PRESETS[ch.tempo])})
        if room is None:
            await self._close_challenge(ch, "the game could not be opened")
            return
        if await self.rooms.join(conn, room.code) is None:
            await self._close_challenge(ch, "the game could not be opened")
            return
        await self._close_challenge(ch, "accepted")
        await src.send({"type": protocol.ROOM_CREATED, "code": room.code,
                        "color": src.color, "token": src.token, "solo": False})
        await conn.send({"type": protocol.ROOM_JOINED, "code": room.code,
                         "color": conn.color, "token": conn.token})
        await room.notify_state()
        log.info("challenge %s accepted: room %s", ch.id, room.code)

    # -- admin ----------------------------------------------------------------

    async def _admin_guard(self, conn: Connection) -> bool:
        """Every admin message asks this, every time. The tab being visible
        is the client's business; whether anything comes back is this."""
        if self.is_admin(conn):
            return True
        log.warning("refused admin request from %s (%s)", conn.ip,
                    (conn.user or {}).get("name", "signed out"))
        await conn.error("not allowed")
        return False

    async def on_admin_stats(self, conn: Connection, msg: dict) -> None:
        if not await self._admin_guard(conn):
            return
        people = self.people().values()
        await conn.send({
            "type": protocol.ADMIN_STATS,
            "live": {
                "connections": len(self.conns),
                "people": len(people),
                "signed_in": sum(1 for p in people if not p["guest"]),
                "guests": sum(1 for p in people if p["guest"]),
                "rooms": self.rooms.stats(),
                "games": self.rooms.live_games(),
                "challenges": len(self.challenges),
                "uptime": round(time.time() - self.started),
            },
            "stored": await db.admin_stats() if db.enabled() else None,
        })

    async def on_admin_list_games(self, conn: Connection, msg: dict) -> None:
        if not await self._admin_guard(conn):
            return
        page = await db.admin_games(GAMES_PAGE, _offset(msg.get("offset")))
        await conn.send({"type": protocol.ADMIN_GAMES, **page})

    async def on_admin_list_users(self, conn: Connection, msg: dict) -> None:
        if not await self._admin_guard(conn):
            return
        page = await db.admin_users(GAMES_PAGE, _offset(msg.get("offset")))
        here = self.people()
        for u in page["users"]:
            u["online"] = f"u:{u['id']}" in here
        await conn.send({"type": protocol.ADMIN_USERS, **page})

    async def dispatch(self, conn: Connection, msg: dict) -> None:
        kind = msg.get("type")
        if kind == protocol.QUEUE_MOVE:
            await self.on_queue_move(conn, msg)
        elif kind == protocol.CREATE_ROOM:
            await self.on_create(conn, msg)
        elif kind == protocol.JOIN_ROOM:
            await self.on_join(conn, msg)
        elif kind == protocol.QUICK_MATCH:
            await self.on_quick_match(conn, msg)
        elif kind == protocol.SET_READY:
            await self.on_set_ready(conn, msg)
        elif kind == protocol.REJOIN:
            await self.on_rejoin(conn, msg)
        elif kind == protocol.RESIGN:
            await self.on_resign(conn, msg)
        elif kind == protocol.LEAVE_ROOM:
            await self.on_leave(conn, msg)
        elif kind == protocol.REMATCH:
            await self.on_rematch(conn, msg)
        elif kind == protocol.SIGN_UP:
            await self.on_sign_up(conn, msg)
        elif kind == protocol.SIGN_IN:
            await self.on_sign_in(conn, msg)
        elif kind == protocol.SIGN_OUT:
            await self.on_sign_out(conn, msg)
        elif kind == protocol.RESUME_SESSION:
            await self.on_resume_session(conn, msg)
        elif kind == protocol.SET_SETTINGS:
            await self.on_set_settings(conn, msg)
        elif kind == protocol.LIST_GAMES:
            await self.on_list_games(conn, msg)
        elif kind == protocol.GET_GAME:
            await self.on_get_game(conn, msg)
        elif kind == protocol.LIST_ROOMS:
            await self.on_list_rooms(conn, msg)
        elif kind == protocol.LIST_ONLINE:
            await self.on_list_online(conn, msg)
        elif kind == protocol.GET_PROFILE:
            await self.on_get_profile(conn, msg)
        elif kind == protocol.SPECTATE:
            await self.on_spectate(conn, msg)
        elif kind == protocol.LIST_FRIENDS:
            await self.on_list_friends(conn, msg)
        elif kind == protocol.FRIEND_REQUEST:
            await self.on_friend_request(conn, msg)
        elif kind == protocol.FRIEND_RESPOND:
            await self.on_friend_respond(conn, msg)
        elif kind == protocol.FRIEND_REMOVE:
            await self.on_friend_remove(conn, msg)
        elif kind == protocol.CHALLENGE:
            await self.on_challenge(conn, msg)
        elif kind == protocol.CHALLENGE_ANSWER:
            await self.on_challenge_answer(conn, msg)
        elif kind == protocol.CHALLENGE_CANCEL:
            await self.on_challenge_cancel(conn, msg)
        elif kind == protocol.ADMIN_STATS:
            await self.on_admin_stats(conn, msg)
        elif kind == protocol.ADMIN_LIST_GAMES:
            await self.on_admin_list_games(conn, msg)
        elif kind == protocol.ADMIN_LIST_USERS:
            await self.on_admin_list_users(conn, msg)
        elif kind == protocol.PING:
            await conn.send({"type": protocol.PONG, "t": msg.get("t"),
                             "server_time": time.time()})
        else:
            await conn.error(f"unknown message type: {kind}")

    # -- connection lifecycle -----------------------------------------------

    async def handle(self, ws) -> None:
        ip = client_ip(ws)
        if len(self.conns) >= MAX_CONNECTIONS:
            log.warning("refusing connection from %s: server full", ip)
            await ws.close(1013, "server full")
            return
        if self.conns_from(ip) >= MAX_CONN_PER_IP:
            log.warning("refusing connection from %s: too many", ip)
            await ws.close(1008, "too many connections")
            return

        self.guests += 1
        conn = Connection(ws, ip, label=f"Guest {self.guests}")
        self.conns.add(conn)
        await conn.send({"type": protocol.SERVER_HELLO, "version": protocol.VERSION})
        window_start, window_count = time.monotonic(), 0

        try:
            async for raw in ws:
                now = time.monotonic()
                if now - window_start >= 1.0:
                    window_start, window_count = now, 0
                window_count += 1
                if window_count > MAX_MSG_PER_SEC:
                    log.warning("flood from %s, closing", ip)
                    # close() waits for the peer's half of the handshake, which
                    # a flooding client never sends - that would pin this task
                    # and its connection slot for the full close timeout. Send
                    # the frame and drop the connection now instead.
                    asyncio.create_task(ws.close(1008, "message rate exceeded"))
                    break

                try:
                    msg = json.loads(raw, parse_constant=_refuse_constant)
                except (ValueError, TypeError):
                    await conn.error("malformed json")
                    continue
                if not isinstance(msg, dict):
                    await conn.error("message must be an object")
                    continue

                await self.dispatch(conn, msg)

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            log.exception("handler error for %s", ip)
        finally:
            self.conns.discard(conn)
            if conn.watching is not None:
                await conn.watching.remove_spectator(conn)
            if conn.room is not None:
                await conn.room.on_disconnect(conn)
                self.rooms.sweep()
            await self._drop_challenges(conn, "the challenger left")


def client_ip(ws) -> str:
    """Real client address, honouring the proxy header set by Caddy/Fly.

    Never the first entry of X-Forwarded-For: a proxy *appends* to that header,
    so the first entry is whatever the client itself sent, and reading it let
    any non-browser client pick its own identity and walk past every per-IP
    limit here. The last entry is the one our own proxy wrote. Fly-Client-IP is
    set by Fly-Proxy and cannot be forged through it, so prefer that.
    """
    headers = ws.request.headers if ws.request else {}
    fly = headers.get("Fly-Client-IP")
    if fly:
        return fly.strip()
    fwd = headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[-1].strip()
    addr = ws.remote_address
    return addr[0] if addr else "?"


async def main() -> None:
    global MAX_CONN_PER_IP
    parser = argparse.ArgumentParser(description="Continuous Chess server")
    parser.add_argument("--host", default=params.SERVER_HOST)
    parser.add_argument("--port", type=int, default=params.SERVER_PORT)
    parser.add_argument("--origin", action="append", default=None,
                        help="allowed Origin; repeatable. Defaults to the "
                             "comma-separated ALLOWED_ORIGINS env var, which "
                             "lets the deployment set it without rebuilding "
                             "the image. Omit both to allow any origin "
                             "(local development only).")
    parser.add_argument("--grace", type=float, default=room_mod.DISCONNECT_GRACE,
                        help="seconds a disconnected player may be gone before "
                             "forfeiting")
    parser.add_argument("--max-conn-per-ip", type=int, default=MAX_CONN_PER_IP,
                        help="raise for local load testing, where every client "
                             "shares one address")
    parser.add_argument("--max-rooms-per-ip", type=int,
                        default=room_mod.RoomManager.MAX_ROOMS_PER_IP,
                        help="the other half of the same limit: tools/stress.py "
                             "opens one room per game and they all share an address")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    MAX_CONN_PER_IP = args.max_conn_per_ip
    room_mod.RoomManager.MAX_ROOMS_PER_IP = args.max_rooms_per_ip
    room_mod.DISCONNECT_GRACE = args.grace

    # Local runs read .env; in production these are Fly secrets and are
    # already in the environment, where setdefault leaves them alone.
    db.load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    await db.connect(os.environ.get("DATABASE_URL"))

    admins = admin_names(os.environ.get("ADMIN_USERS"))
    if admins:
        log.info("%d admin account(s) configured", len(admins))
    hub = Hub(admins)

    def process_request(connection, request):
        if request.path == "/health":
            body = json.dumps(hub.rooms.stats() | {"connections": len(hub.conns)})
            return connection.respond(http.HTTPStatus.OK, body + "\n")
        return None

    origins = args.origin
    if not origins:
        env_origins = os.environ.get("ALLOWED_ORIGINS", "")
        origins = [o.strip() for o in env_origins.split(",") if o.strip()] or None
    if origins:
        log.info("restricting Origin to: %s", ", ".join(origins))
    else:
        log.warning("no --origin set: accepting connections from any origin")

    try:
        async with serve(hub.handle, args.host, args.port,
                         process_request=process_request,
                         origins=origins,
                         close_timeout=5,
                         max_size=MAX_MESSAGE_BYTES):
            log.info("listening on %s:%d", args.host, args.port)
            await hub.rooms.gc_loop()
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
