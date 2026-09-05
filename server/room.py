"""
Room layer: many concurrent games in one server process.

A Room owns a GameState and the task driving it. GameState.run() already takes
a broadcast callback, so the game loop itself is unaware that rooms exist.
"""

import asyncio
import json
import logging
import secrets
import time

from . import civs, db, params, presets, rating
from .game import GameState
from .pieces import start_overlap_reason
from shared import protocol

log = logging.getLogger("room")

LOBBY, RUNNING, FINISHED = "lobby", "running", "finished"

DISCONNECT_GRACE = 30.0     # seconds a player may be gone before forfeiting
LOBBY_TTL        = 900.0    # unjoined rooms expire after 15 minutes
FINISHED_TTL     = 300.0    # finished rooms stay rematchable; a replay takes minutes
GC_INTERVAL      = 30.0

OPPOSITE = {"white": "black", "black": "white"}


def dump(msg: dict) -> str:
    """Serialise one frame. allow_nan=False because NaN and Infinity are not
    JSON: Python writes the bare tokens happily and every browser's JSON.parse
    then throws on the frame, which does not break one value, it breaks the
    socket for the rest of the game. Raising here turns a bad number into a
    dropped frame and a stack trace instead."""
    return json.dumps(msg, allow_nan=False)


class Connection:
    """One websocket. Owns its identity and rate-limit budget."""

    def __init__(self, ws, ip: str) -> None:
        self.ws = ws
        self.ip = ip
        self.room: "Room | None" = None
        self.color: str | None = None
        self.token: str = ""
        # Token bucket for QUEUE_MOVE. Legitimate play is mana-bound to roughly
        # 5 instant moves then ~0.3/s, so this is generous.
        self.move_tokens: float = 10.0
        self.move_tokens_at: float = time.monotonic()
        # Wrong room codes tried on this socket. Guessing them is how a private
        # room gets found, and a real player mistypes a code once or twice.
        self.join_fails: int = 0
        # {"id", "name"} once signed in. The seat copies this when it sits
        # down, so signing out mid-game cannot change who the game was between.
        self.user: dict | None = None
        self.auth_attempts: int = 0

    async def send(self, msg: dict) -> None:
        try:
            data = dump(msg)
        except ValueError:
            log.exception("refusing to send a non-finite value to %s", self.ip)
            return
        try:
            await self.ws.send(data)
        except Exception:
            pass

    async def error(self, reason: str) -> None:
        await self.send({"type": protocol.ERROR, "reason": reason})

    def allow_move(self) -> bool:
        now = time.monotonic()
        self.move_tokens = min(10.0, self.move_tokens + (now - self.move_tokens_at) * 3.0)
        self.move_tokens_at = now
        if self.move_tokens < 1.0:
            return False
        self.move_tokens -= 1.0
        return True


class Room:
    def __init__(self, code: str, base_params: dict[str, dict],
                 public: bool = False, solo: bool = False,
                 view: dict | None = None, unrated: bool = False) -> None:
        self.code = code
        self.public = public
        self.solo = solo
        # The host's own decision that this game should not count. Everything
        # else that makes a game unrated is a property of the room the server
        # works out for itself; this is the one a player asks for, and it is
        # fixed at creation so nobody can turn it on after seeing the result.
        self.unrated = unrated
        # Fixed by whoever opened the room, like the tempo: both sides play
        # under the same information rules.
        self.view: dict = params.build_view(view)
        # The tempo the room was made with, per seat, and the only thing a
        # client gets to choose. Balanced rooms give the two seats different
        # numbers, so BOTH are announced in ROOM_STATE and the pre-game screen
        # shows both columns: a room that could hand you a crippled seat
        # without showing you is why the earlier version of this was removed.
        self.base_params: dict[str, dict] = {
            "white": dict(base_params["white"]),
            "black": dict(base_params["black"]),
        }
        # Filled in at Ready, by the server, from base_params and the seat's
        # civilization. Never taken from a client: a seat that names its own
        # numbers names its own physics.
        self.params: dict[str, dict] = {
            c: dict(self.base_params[c]) for c in ("white", "black")
        }
        # Which preset the room plays under, or "custom". Fixed at creation,
        # because base_params is: it names the rating a seat's number belongs
        # to and the tempo a stored game is filed under.
        self.tempo: str = presets.tempo_name(self.base_params["white"]) or "custom"
        self.game: GameState | None = None
        self.ready: dict[str, bool] = {"white": False, "black": False}
        # Both seats have to ask for a rematch, the same way both have to ready
        # up: pressing it alone used to drop the other player straight back
        # into civilization picking off a screen they were still reading.
        self.rematch: dict[str, bool] = {"white": False, "black": False}
        self.civ: dict[str, str | None] = {"white": None, "black": None}
        self.clients: dict[str, Connection | None] = {"white": None, "black": None}
        # Copied from the connection when the seat is taken, not read back from
        # it later: signing out or reloading mid-game must not change who the
        # game was between, and a rejoining socket must not be able to claim a
        # seat for a different account.
        self.user: dict[str, dict | None] = {"white": None, "black": None}
        self.tokens: dict[str, str] = {}
        self.state: str = LOBBY
        self.created_at = time.monotonic()
        self.finished_at: float | None = None
        self.task: asyncio.Task | None = None
        self._grace: dict[str, asyncio.Task] = {}
        self._save_task: asyncio.Task | None = None
        # Set once the finished game reaches the database, so the client can be
        # pointed at its stored replay.
        self.game_id: str | None = None

    # -- membership ---------------------------------------------------------

    def balanced(self) -> bool:
        """True when the two seats were given different numbers."""
        return self.base_params["white"] != self.base_params["black"]

    def free_seat(self) -> str | None:
        for color in ("white", "black"):
            if self.clients[color] is None and color not in self.tokens:
                return color
        return None

    def occupants(self) -> int:
        return len({id(c) for c in self.clients.values() if c is not None})

    def matches_quick(self, tempo: str) -> bool:
        """Whether a quick match at `tempo` may be dropped into this room.

        Quick match asks for one thing - a tempo - so it must not land the
        player in a room that changed anything else. A room offering a
        handicap, hidden information or no rating is a room somebody chose
        deliberately; walking into one unasked is the surprise this list of
        conditions exists to prevent.
        """
        return (self.public and not self.solo and self.state == LOBBY
                and not self.unrated and not self.balanced()
                and self.tempo == tempo
                and self.view == params.VIEW_DEFAULTS)

    async def seat(self, conn: Connection, color: str) -> None:
        conn.room = self
        conn.color = color
        conn.token = secrets.token_urlsafe(12)
        self.clients[color] = conn
        self.user[color] = dict(conn.user) if conn.user else None
        # Read once, when the seat is taken, rather than on every ROOM_STATE:
        # this is a label on a player, and notify_state runs on every ready
        # toggle. A rating that moves mid-room is the next game's problem.
        if self.user[color] is not None and self.tempo != "custom":
            ratings = await db.get_ratings(self.user[color]["id"])
            self.user[color]["rating"] = ratings.get(self.tempo)
        self.tokens[color] = conn.token

    def release_seat(self, color: str) -> None:
        """Give a seat back to the room so somebody else can sit in it.

        A seat is held by its token even after the socket dies, so a dropped
        player can reclaim the colour they were playing. That is right during
        a game and wrong once the room is back in the lobby: the reservation
        outlived the player, so the one still waiting was told "not here yet"
        forever, the one coming back was told "room is full", and quick match
        skipped the very room its own player had just left.
        """
        self.clients[color] = None
        self.tokens.pop(color, None)
        self.user[color] = None
        self.ready[color] = False
        self.civ[color] = None
        self.rematch[color] = False
        self.params[color] = dict(self.base_params[color])

    async def broadcast(self, msg: dict) -> None:
        try:
            data = dump(msg)
        except ValueError:
            log.exception("room %s: refusing to broadcast a non-finite value", self.code)
            return
        seen: set[int] = set()
        for conn in self.clients.values():
            if conn is None or id(conn) in seen:
                continue
            seen.add(id(conn))
            try:
                await conn.ws.send(data)
            except Exception:
                pass

    async def broadcast_game(self, game) -> None:
        """Game snapshots, one per seat: each side may be entitled to see
        different things. Solo holds both seats on one socket, so it gets the
        unfiltered view once."""
        seen: set[int] = set()
        for color, conn in self.clients.items():
            if conn is None or id(conn) in seen:
                continue
            seen.add(id(conn))
            await conn.send(game.to_dict(None if self.solo else color))

    def seat_card(self, color: str) -> dict:
        """One seat as both players may see it: who is sitting there, and how
        strong they are at this room's tempo.

        Anonymous is a first-class way to play, so an empty name is not a
        missing value; it is the answer. The rating is the one for the tempo
        the room is actually played at, because that is the only number the
        result will move.
        """
        user = self.user[color]
        return {
            "present": self.clients[color] is not None,
            "name":    user["name"] if user else None,
            "rating":  (user or {}).get("rating"),
            "ready":   self.ready[color],
            "rematch": self.rematch[color],
        }

    def rated_reason(self) -> str | None:
        """Why a game played in this room now would not be rated, or None.

        Asked before the game rather than only after it: every condition here
        is known at the lobby, and a player who wanted a rated game should
        find that out while they can still change the room.
        """
        return rating.rated_reason(
            self.solo, self.base_params, self.view,
            (self.user["white"] or {}).get("id"),
            (self.user["black"] or {}).get("id"),
            unrated=self.unrated)

    async def notify_state(self) -> None:
        # Deliberately no civ names: the pick stays hidden until the game runs.
        reason = self.rated_reason()
        await self.broadcast({
            "type":    protocol.ROOM_STATE,
            "code":    self.code,
            "players": self.occupants(),
            "waiting": self.state == LOBBY,
            # Who is in each seat, whether they are ready, and what they are
            # rated. One key rather than a parallel "seated" map: a seat is one
            # thing, and two maps describing it drift.
            "seats":   {c: self.seat_card(c) for c in ("white", "black")},
            "base_params": self.base_params,
            "balanced": self.balanced(),
            "tempo": self.tempo,
            "view": self.view,
            "ready":   dict(self.ready),
            "rated": reason is None,
            "unrated_reason": reason,
        })

    def set_ready(self, color: str, ready: bool, civ: str | None) -> None:
        """Record one seat's choice. A solo client owns both seats and readies
        them one at a time, so each can be a different civilization.

        The params follow from the civilization; the caller has already
        resolved and checked them (see civs.resolve_checked)."""
        self.ready[color] = ready
        self.civ[color] = civ
        if ready:
            self.params[color] = civs.resolve(self.base_params[color], civ)

    def reset_for_rematch(self) -> None:
        """Put a finished room back in the lobby with the same seats and the
        same tempo. Readiness and civilizations are cleared rather than
        carried over: picking again is most of the reason to want a rematch,
        and it puts the room back through the one path that starts a game."""
        self.game = None
        self.task = None
        self.game_id = None
        self.state = LOBBY
        self.finished_at = None
        self.created_at = time.monotonic()   # LOBBY_TTL starts again
        self.ready = {"white": False, "black": False}
        self.civ = {"white": None, "black": None}
        self.rematch = {"white": False, "black": False}
        self.params = {c: dict(self.base_params[c]) for c in ("white", "black")}

    # -- lifecycle ----------------------------------------------------------

    def start_if_ready(self) -> None:
        if self.state != LOBBY:
            return
        if any(self.clients[c] is None for c in ("white", "black")):
            return
        if not all(self.ready[c] for c in ("white", "black")):
            return
        self.game = GameState(solo=self.solo,
                              params_white=self.params["white"],
                              params_black=self.params["black"],
                              civs=dict(self.civ),
                              view=self.view)
        self.state = RUNNING
        self.task = asyncio.create_task(self._run())
        log.info("room %s starting", self.code)

    async def _run(self) -> None:
        try:
            await self.game.run(self.broadcast_game)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("room %s crashed", self.code)
        finally:
            self.state = FINISHED
            self.finished_at = time.monotonic()
            log.info("room %s over, winner=%s", self.code, self.game.winner)
            # Storing the game is not part of finishing it. A slow or missing
            # database must not hold up the final broadcast, the rematch
            # button, or the room's own teardown, so this is fired and
            # forgotten. The game is passed in rather than read back off the
            # room, because a rematch agreed in the next moment replaces it.
            if db.enabled() and self.game is not None:
                self._save_task = asyncio.create_task(self._save(self.game))
            # A player watching the final frame can press Rematch before this
            # coroutine gets here, and their request would otherwise land on a
            # room still marked RUNNING and be dropped - leaving them waiting
            # for an opponent who had already agreed.
            if any(self.rematch.values()):
                self.maybe_rematch()
                await self.notify_state()

    def maybe_rematch(self) -> bool:
        """Reopen the room if both seats have asked for it. True if it did.

        Separate from recording the request, because the two happen at
        different moments: the second player's press usually does both, but a
        press that arrives while the game loop is still winding up records
        now and reopens when the room actually reaches FINISHED.
        """
        if self.state != FINISHED:
            return False
        if not all(self.rematch[c] for c in ("white", "black")):
            return False
        if not self.solo and any(self.clients[c] is None
                                 for c in ("white", "black")):
            return False
        log.info("room %s: rematch", self.code)
        self.reset_for_rematch()
        return True

    async def _save(self, game: GameState) -> None:
        """Store the finished game, and move the ratings if it was rated.
        Called once, off the game loop."""
        if game is None:
            return
        tempo = self.tempo
        white_id = (self.user["white"] or {}).get("id")
        black_id = (self.user["black"] or {}).get("id")
        reason = self.rated_reason()
        self.game_id = await db.save_game(
            white_user_id=white_id,
            black_user_id=black_id,
            white_civ=self.civ["white"],
            black_civ=self.civ["black"],
            tempo=tempo,
            winner=game.winner or "draw",
            ticks=game.tick,
            rated=reason is None,
            unrated_reason=reason,
            recording=game.recorder.to_dict(),
            log_format=game.recorder.format,
            civ_table=civs.table_fingerprint(),
            solo=self.solo,
        )
        if not self.game_id:
            return
        log.info("room %s: saved game %s (%s, %d ticks, rated=%s)",
                 self.code, self.game_id, tempo, game.tick, reason is None)
        # Both seats are told where the game went, because that recording is
        # what they should be watching: it holds the whole game, while the
        # frames their own client kept hold only the half the room's
        # visibility settings let them see at the time.
        await self.broadcast({"type": protocol.GAME_SAVED,
                              "game_id": self.game_id})

        if reason is not None:
            return
        moved = await db.apply_rating(
            game_id=self.game_id, white_user_id=white_id, black_user_id=black_id,
            tempo=tempo, winner=game.winner or "draw", update_fn=rating.update)
        if moved:
            # Both seats are told, even the loser: a rating that changes
            # silently is one people assume is broken.
            await self.broadcast({"type": protocol.RATING_UPDATE,
                                  "game_id": self.game_id, **moved})

    async def on_disconnect(self, conn: Connection) -> None:
        color = conn.color
        if color is None or self.clients.get(color) is not conn:
            return
        # A solo client is seated twice. Clearing only the seat it was dealt
        # left the other one pointing at the dead socket, so the game kept
        # running and kept broadcasting to it.
        for seat, seated in list(self.clients.items()):
            if seated is conn:
                self.clients[seat] = None

        if self.solo:
            if (self.state == RUNNING and self.game is not None
                    and not self.game.game_over):
                # Nobody is left to play it out and nobody to tell.
                self.game.forfeit(color)
            return

        if self.state == RUNNING:
            await self.broadcast({
                "type": protocol.OPPONENT_LEFT,
                "color": color,
                "grace_seconds": DISCONNECT_GRACE,
            })
            self._grace[color] = asyncio.create_task(self._forfeit_after_grace(color))
        else:
            # No game to forfeit here, but the seat still has to come back to
            # the room. The same grace, for the same reason: a phone that
            # backgrounds itself gets its colour back, and a player who is
            # gone for good stops holding a seat nobody can take.
            self._grace[color] = asyncio.create_task(self._release_after_grace(color))
            await self.notify_state()

    async def _release_after_grace(self, color: str) -> None:
        try:
            await asyncio.sleep(DISCONNECT_GRACE)
        except asyncio.CancelledError:
            return
        if self.clients.get(color) is not None or self.state == RUNNING:
            return          # they came back, or a game started around them
        log.info("room %s: %s seat released", self.code, color)
        self.release_seat(color)
        await self.notify_state()

    async def leave(self, conn: Connection) -> None:
        """Leave on purpose, rather than by losing the socket.

        The seat is the difference. Somebody who says they are going is not
        coming back to that colour, so the reservation goes now instead of in
        thirty seconds: the room has to be joinable again immediately, or
        quick match opens a second room for the same two people.
        """
        seats = [c for c, seated in self.clients.items() if seated is conn]
        await self.on_disconnect(conn)
        if self.state == RUNNING:
            return          # the game is still being played out; the seat is theirs
        for color in seats:
            grace = self._grace.pop(color, None)
            if grace:
                grace.cancel()
            self.release_seat(color)
        await self.notify_state()

    async def _forfeit_after_grace(self, color: str) -> None:
        try:
            await asyncio.sleep(DISCONNECT_GRACE)
        except asyncio.CancelledError:
            return
        if (self.clients.get(color) is None and self.state == RUNNING
                and self.game is not None and not self.game.game_over):
            log.info("room %s: %s forfeits (disconnect timeout)", self.code, color)
            self.game.forfeit(color)

    async def rejoin(self, conn: Connection, color: str) -> None:
        grace = self._grace.pop(color, None)
        if grace:
            grace.cancel()
        conn.room = self
        conn.color = color
        self.clients[color] = conn
        await self.broadcast({"type": protocol.OPPONENT_REJOINED, "color": color})

    def abandoned(self) -> bool:
        if self.state == LOBBY:
            return self.occupants() == 0 or time.monotonic() - self.created_at > LOBBY_TTL
        if self.state == FINISHED:
            # Empty goes at once: the long TTL exists so the players still
            # sitting there can agree a rematch, not to keep a husk alive.
            return (self.occupants() == 0
                    or (self.finished_at is not None
                        and time.monotonic() - self.finished_at > FINISHED_TTL))
        return False

    def shutdown(self) -> None:
        for t in self._grace.values():
            t.cancel()
        self._grace.clear()
        if self.task and not self.task.done():
            self.task.cancel()


class RoomManager:
    MAX_ROOMS_PER_IP = 3

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    # -- creation / joining -------------------------------------------------

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(protocol.CODE_ALPHABET)
                           for _ in range(protocol.CODE_LENGTH))
            if code not in self.rooms:
                return code

    def rooms_owned_by(self, ip: str) -> int:
        return sum(
            1 for r in self.rooms.values()
            if any(c is not None and c.ip == ip for c in r.clients.values())
        )

    async def create(self, conn: Connection, msg: dict, public: bool = False) -> Room | None:
        if self.rooms_owned_by(conn.ip) >= self.MAX_ROOMS_PER_IP:
            await conn.error("too many rooms from this address")
            return None

        # Balanced rooms hand the two seats different numbers: `params` is
        # white's and the room's default, `params_black` overrides black's.
        #
        # This existed once and was removed, because ROOM_STATE announced a
        # single base_params and a joiner could be seated into a crippled side
        # without ever being shown it. It is back with that hole closed:
        # notify_state sends both columns and `balanced`, and the pre-game
        # screen puts them side by side before anyone presses Ready. Keep those
        # together - per-seat numbers that the joiner cannot see is the bug.
        base = msg.get("params")
        black = msg.get("params_black")
        if black is None:
            black = base
        seats = {"white": base, "black": black}
        for seat, p_in in seats.items():
            reason = params.validate_params(p_in) or start_overlap_reason(p_in)
            if reason:
                log.warning("rejected %s params from %s: %s", seat, conn.ip, reason)
                await conn.error(reason if black is base else f"{seat}: {reason}")
                return None

        reason = params.validate_view(msg.get("view"))
        if reason:
            log.warning("rejected view from %s: %s", conn.ip, reason)
            await conn.error(reason)
            return None

        solo = bool(msg.get("solo"))
        room = Room(self._new_code(),
                    {seat: (p_in or {}) for seat, p_in in seats.items()},
                    public=public and not solo,
                    solo=solo, view=msg.get("view"),
                    unrated=bool(msg.get("unrated")))
        self.rooms[room.code] = room
        await room.seat(conn, "white")
        if solo:
            room.clients["black"] = conn
            # Copied from the seat that was just taken, not from the connection
            # again: that one has been through seat(), so it carries the
            # rating this room's tempo is played at.
            room.user["black"] = dict(room.user["white"]) if room.user["white"] else None
            room.tokens["black"] = conn.token
        log.info("room %s created by %s (public=%s)", room.code, conn.ip, public)
        return room

    async def join(self, conn: Connection, code: str) -> Room | None:
        room = self.rooms.get(code)
        if room is None:
            await conn.error("no such room")
            return None
        if room.state != LOBBY:
            await conn.error("game already started")
            return None
        seat = room.free_seat()
        if seat is None:
            await conn.error("room is full")
            return None
        await room.seat(conn, seat)
        return room

    async def quick_match(self, conn: Connection, msg: dict) -> Room | None:
        """Pair up at one of the three standard tempos.

        The tempo is a name, and the numbers behind it are this server's own
        presets - not the caller's params, as this used to take. Two things
        follow. A player knows what they are agreeing to before they press it,
        which they could not when the tempo was whatever the create screen had
        been left on; and a quick game is rateable by construction, because
        nothing about it was chosen by a client.
        """
        tempo = msg.get("tempo")
        if tempo not in presets.MODES:
            await conn.error("unknown tempo")
            return None
        for room in self.rooms.values():
            if room.matches_quick(tempo) and room.free_seat():
                seat = room.free_seat()
                await room.seat(conn, seat)
                log.info("quick match: %s joined %s (%s)", conn.ip, room.code, tempo)
                return room
        return await self.create(conn, {"params": dict(presets.PRESETS[tempo])},
                                 public=True)

    def open_rooms(self, limit: int = 40) -> list[dict]:
        """Public rooms still waiting for someone, oldest first.

        A room is listed only while it has a free seat and has not started, so
        every row is a game that can actually be walked into. The settings ride
        along because choosing between open games is the whole point of the
        list: the tempo, whether it is balanced, and what each side can see.
        """
        now = time.monotonic()
        out = []
        for room in self.rooms.values():
            if not room.public or room.solo or room.state != LOBBY:
                continue
            if room.free_seat() is None:
                continue
            host = next((u for u in room.user.values() if u), None)
            out.append({
                "code": room.code,
                "tempo": presets.tempo_name(room.base_params["white"]) or "custom",
                "balanced": room.balanced(),
                "base_params": room.base_params,
                "view": room.view,
                # The room's own answer, not the seated players': a listing
                # shows rooms with an empty seat, and "both players must be
                # signed in" is about who joins, not about the room.
                "rated": rating.settings_reason(room.solo, room.base_params,
                                                room.view, room.unrated) is None,
                "host": host["name"] if host else None,
                "waiting": round(now - room.created_at),
            })
        out.sort(key=lambda r: -r["waiting"])
        return out[:limit]

    def find_rejoin(self, code: str, token: str) -> str | None:
        """Return the colour this token owns in the room, if it is reclaimable."""
        room = self.rooms.get(code)
        if room is None:
            return None
        for color, tok in room.tokens.items():
            if secrets.compare_digest(tok, token) and room.clients[color] is None:
                return color
        return None

    # -- housekeeping -------------------------------------------------------

    def sweep(self) -> int:
        dead = [code for code, room in self.rooms.items() if room.abandoned()]
        for code in dead:
            self.rooms.pop(code).shutdown()
        if dead:
            log.info("gc removed %d room(s): %s", len(dead), ", ".join(dead))
        return len(dead)

    async def gc_loop(self) -> None:
        while True:
            await asyncio.sleep(GC_INTERVAL)
            try:
                self.sweep()
            except Exception:
                log.exception("gc sweep failed")

    def stats(self) -> dict:
        by_state = {LOBBY: 0, RUNNING: 0, FINISHED: 0}
        for room in self.rooms.values():
            by_state[room.state] = by_state.get(room.state, 0) + 1
        return {
            "rooms": len(self.rooms),
            "lobby": by_state[LOBBY],
            "running": by_state[RUNNING],
            "finished": by_state[FINISHED],
            "players": sum(r.occupants() for r in self.rooms.values()),
        }
