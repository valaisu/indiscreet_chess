"""
Presence, spectating, friends, challenges and the admin tab, over real sockets.

    ADMIN_USERS=tadmin1 DATABASE_URL=<a throwaway database> \
        .venv/bin/python -m server.main --port 8799 &
    PYTHONPATH=. .venv/bin/python tools/social_test.py --url ws://localhost:8799

The presence and spectating checks need only a server. Friends, challenges and
admin need accounts, so they run only when the server has a database, and they
create accounts in it: point it at a throwaway one, never at production.
"""

import argparse
import asyncio
import json
import secrets
import sys

import websockets

from shared import protocol

DEFAULT_URL = "ws://localhost:8799"
ADMIN = "tadmin1"                 # must be in the server's ADMIN_USERS
PW = "correct horse battery"
FAILS: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"[PASS] {name}")
    else:
        FAILS.append(f"{name}: {detail or 'failed'}")
        print(f"[FAIL] {name}: {detail}")


async def send(ws, **msg) -> None:
    await ws.send(json.dumps(msg))


async def wait_for(ws, kind: str, pred=lambda m: True, timeout: float = 8.0) -> dict:
    """The next `kind` message that satisfies `pred`, skipping everything else
    - including the 20 Hz game frames a seated socket is buried in."""
    async def loop() -> dict:
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == kind and pred(msg):
                return msg
    return await asyncio.wait_for(loop(), timeout)


async def nothing(ws, kind: str, seconds: float = 1.0) -> bool:
    """True if no `kind` message arrives within `seconds`."""
    try:
        await wait_for(ws, kind, timeout=seconds)
        return False
    except asyncio.TimeoutError:
        return True


async def online(ws) -> dict:
    await send(ws, type=protocol.LIST_ONLINE)
    return await wait_for(ws, protocol.ONLINE_LIST)


async def start_game(url: str):
    """Two anonymous players in a running game. Returns both sockets."""
    host = await websockets.connect(url)
    guest = await websockets.connect(url)
    await send(host, type=protocol.CREATE_ROOM, params={})
    code = (await wait_for(host, protocol.ROOM_CREATED))["code"]
    await send(guest, type=protocol.JOIN_ROOM, code=code)
    await wait_for(guest, protocol.ROOM_JOINED)
    for ws in (host, guest):
        await send(ws, type=protocol.SET_READY, ready=True, civ=None)
    await wait_for(host, protocol.GAME_STATE, lambda m: m["countdown"] is None,
                   timeout=10)
    return host, guest, code


# --- presence -----------------------------------------------------------------

async def test_presence(url: str) -> None:
    print("\n-- everybody is listed, guests included --")
    async with websockets.connect(url) as a, websockets.connect(url) as b:
        msg = await online(a)
        people = msg.get("people", [])
        guests = [p for p in people if p["guest"]]
        check("guests are listed by label",
              len(guests) >= 2 and all(p["name"].startswith("Guest ") for p in guests),
              people)
        mine = [p for p in people if p["key"] == msg.get("you")]
        check("the list says which entry is you", len(mine) == 1, msg.get("you"))
        check("an idle guest is online", mine and mine[0]["status"] == "online", mine)
        check("nobody idle is offered as watchable",
              all(p["watch"] is None for p in guests if p["status"] == "online"))


# --- spectating ---------------------------------------------------------------

async def test_spectating(url: str) -> None:
    print("\n-- watching a game --")
    host, guest, code = await start_game(url)
    try:
        async with websockets.connect(url) as spec:
            msg = await online(spec)
            playing = [p for p in msg["people"] if p["status"] == "playing"]
            check("players in a running game are listed as playing",
                  len(playing) >= 2, msg["people"])
            check("a playing entry carries a key to watch by",
                  all(p["watch"] == p["key"] for p in playing))
            check("and no room code anywhere in the list",
                  code not in json.dumps(msg))
            host_key = next(p["key"] for p in playing)

            await send(spec, type=protocol.SPECTATE, key="g:Guest 0")
            end = await wait_for(spec, protocol.SPECTATE_END)
            check("a key nobody is playing under is refused",
                  "ended" in end["reason"], end)

            await send(spec, type=protocol.SPECTATE, key=host_key)
            await wait_for(spec, protocol.SPECTATING)
            st = await wait_for(spec, protocol.ROOM_STATE)
            check("the spectator is sent the room", st["code"] == code, st)
            seen = await wait_for(host, protocol.ROOM_STATE,
                                  lambda m: m.get("watchers") == 1)
            check("the players are told someone is watching",
                  seen["watchers"] == 1)

            frame = await wait_for(spec, protocol.GAME_STATE)
            check("the spectator sees both mana pools",
                  set(frame["mana"]) == {"white", "black"}, frame["mana"])
            pframe = await wait_for(guest, protocol.GAME_STATE)
            check("while a player still sees only their own",
                  set(pframe["mana"]) == {"black"}, pframe["mana"])

            # Nothing a seat can do works for a spectator.
            piece = next(p for p in frame["pieces"] if p["owner"] == "white"
                         and p["type"] == "pawn")
            await send(spec, type=protocol.QUEUE_MOVE, piece_id=piece["id"],
                       destination=[piece["x"], piece["y"] - 1])
            await send(spec, type=protocol.RESIGN)
            await asyncio.sleep(0.6)
            later = await wait_for(spec, protocol.GAME_STATE)
            moved = next(p for p in later["pieces"] if p["id"] == piece["id"])
            check("a spectator's move is ignored", moved["state"] == "idle", moved)
            check("a spectator cannot resign for anyone", not later["game_over"])

            await send(spec, type=protocol.CREATE_ROOM, params={})
            err = await wait_for(spec, protocol.ERROR)
            check("a spectator cannot open a room without leaving",
                  err["reason"] == "already in a room", err)

            msg = await online(host)
            me = next(p for p in msg["people"] if p["status"] == "watching")
            check("the spectator is listed as watching", me is not None)

            await send(spec, type=protocol.LEAVE_ROOM)
            seen = await wait_for(host, protocol.ROOM_STATE,
                                  lambda m: m.get("watchers") == 0)
            check("leaving is seen by the players", seen["watchers"] == 0)

        # A spectator that drops is not a player that drops.
        spec2 = await websockets.connect(url)
        await send(spec2, type=protocol.SPECTATE, key=host_key)
        await wait_for(spec2, protocol.SPECTATING)
        await wait_for(host, protocol.ROOM_STATE, lambda m: m.get("watchers") == 1)
        await spec2.close()
        await wait_for(host, protocol.ROOM_STATE, lambda m: m.get("watchers") == 0)
        check("a spectator disconnecting tells nobody an opponent left",
              await nothing(host, protocol.OPPONENT_LEFT, 1.0))

        # A rematch is a new game: whoever was watching is let go.
        spec3 = await websockets.connect(url)
        await send(spec3, type=protocol.SPECTATE, key=host_key)
        await wait_for(spec3, protocol.SPECTATING)
        await send(host, type=protocol.RESIGN)
        final = await wait_for(spec3, protocol.GAME_STATE, lambda m: m["game_over"])
        check("the spectator sees the result", final["winner"] == "black", final)
        await send(host, type=protocol.REMATCH)
        await send(guest, type=protocol.REMATCH)
        end = await wait_for(spec3, protocol.SPECTATE_END)
        check("a rematch ends the watching", "rematch" in end["reason"], end)
        await spec3.close()
    finally:
        await host.close()
        await guest.close()


# --- accounts -----------------------------------------------------------------

async def account(url: str, name: str):
    """A signed-in socket. Signs up, or in if the name exists already."""
    ws = await websockets.connect(url)
    await send(ws, type=protocol.SIGN_UP, name=name, password=PW)
    reply = await asyncio.wait_for(_auth_reply(ws), 8)
    if reply["type"] == protocol.AUTH_ERROR and "taken" in reply["reason"]:
        await send(ws, type=protocol.SIGN_IN, name=name, password=PW)
        reply = await asyncio.wait_for(_auth_reply(ws), 8)
    if reply["type"] != protocol.AUTH_STATE:
        await ws.close()
        return None, reply.get("reason")
    return ws, reply["user"]


async def _auth_reply(ws) -> dict:
    while True:
        msg = json.loads(await ws.recv())
        if msg["type"] in (protocol.AUTH_STATE, protocol.AUTH_ERROR):
            return msg


async def friends(ws, **msg) -> dict:
    await send(ws, **msg)
    return await wait_for(ws, protocol.FRIEND_LIST)


async def test_accounts(url: str) -> None:
    tag = secrets.token_hex(3)
    alice_ws, alice = await account(url, f"a{tag}")
    if alice_ws is None:
        print(f"\n(skipping friends, challenges and admin: {alice})")
        return
    bob_ws, bob = await account(url, f"b{tag}")
    carol_ws, carol = await account(url, f"c{tag}")
    admin_ws, admin = await account(url, ADMIN)
    try:
        await test_friends(url, alice_ws, alice, bob_ws, bob, carol_ws, carol)
        await test_challenges(url, alice_ws, alice, bob_ws, bob, carol_ws, carol)
        await test_admin(url, admin_ws, admin, bob_ws, bob)
    finally:
        for ws in (alice_ws, bob_ws, carol_ws, admin_ws):
            await ws.close()


async def test_friends(url, alice_ws, alice, bob_ws, bob, carol_ws, carol) -> None:
    print("\n-- friends --")
    msg = await friends(alice_ws, type=protocol.FRIEND_REQUEST, name=bob["name"])
    check("a request is sent", "sent" in (msg["note"] or ""), msg["note"])
    check("and shows as outgoing",
          [o["name"] for o in msg["outgoing"]] == [bob["name"]], msg)

    msg = await friends(alice_ws, type=protocol.FRIEND_REQUEST, name=bob["name"])
    check("asking twice is refused", "already asked" in (msg["note"] or ""), msg["note"])

    msg = await friends(alice_ws, type=protocol.FRIEND_REQUEST, name=alice["name"])
    check("you cannot add yourself", "yourself" in (msg["note"] or ""), msg["note"])

    msg = await friends(alice_ws, type=protocol.FRIEND_REQUEST, name="nobodyXYZ123")
    check("an unknown name is refused", "no player" in (msg["note"] or ""), msg["note"])

    msg = await friends(bob_ws, type=protocol.LIST_FRIENDS)
    check("the other side sees it as incoming",
          [i["name"] for i in msg["incoming"]] == [alice["name"]], msg)

    await send(bob_ws, type=protocol.GET_PROFILE, id=alice["id"])
    card = await wait_for(bob_ws, protocol.PROFILE)
    check("a card says a request is waiting on you", card["friend"] == "incoming", card)

    msg = await friends(bob_ws, type=protocol.FRIEND_RESPOND, id=alice["id"], accept=True)
    check("accepting makes them friends",
          [f["name"] for f in msg["friends"]] == [alice["name"]], msg)
    check("a friend who is here is shown online",
          msg["friends"][0]["status"] == "online", msg["friends"])

    # Two people asking each other is two people agreeing.
    await friends(carol_ws, type=protocol.FRIEND_REQUEST, name=alice["name"])
    msg = await friends(alice_ws, type=protocol.FRIEND_REQUEST, name=carol["name"])
    check("a request crossing one already made is an acceptance",
          "now friends" in (msg["note"] or ""), msg["note"])
    check("alice now has two friends", len(msg["friends"]) == 2, msg["friends"])

    await send(alice_ws, type=protocol.GET_PROFILE, id=bob["id"])
    card = await wait_for(alice_ws, protocol.PROFILE)
    check("a card says you are friends", card["friend"] == "friends", card)
    check("and that they are online", card["status"] == "online", card)

    # Declining and removing.
    await friends(carol_ws, type=protocol.FRIEND_REQUEST, name=bob["name"])
    msg = await friends(bob_ws, type=protocol.FRIEND_RESPOND, id=carol["id"], accept=False)
    check("declining drops the request", msg["incoming"] == [], msg)
    msg = await friends(carol_ws, type=protocol.LIST_FRIENDS)
    check("and the asker no longer has it outgoing", msg["outgoing"] == [], msg)

    signed_out = await websockets.connect(url)
    msg = await friends(signed_out, type=protocol.FRIEND_REQUEST, name=bob["name"])
    check("signed out, friends are refused with a reason", "Sign in" in msg["note"], msg)
    await signed_out.close()


async def test_challenges(url, alice_ws, alice, bob_ws, bob, carol_ws, carol) -> None:
    print("\n-- challenges --")
    await send(carol_ws, type=protocol.CHALLENGE, id=bob["id"], tempo="rapid")
    msg = await wait_for(carol_ws, protocol.CHALLENGE_CLOSED)
    check("only friends can be challenged", "only challenge friends" in msg["reason"], msg)

    await send(alice_ws, type=protocol.CHALLENGE, id=bob["id"], tempo="warp")
    msg = await wait_for(alice_ws, protocol.CHALLENGE_CLOSED)
    check("an unknown tempo is refused", "tempo" in msg["reason"], msg)

    await send(alice_ws, type=protocol.CHALLENGE, id=bob["id"], tempo="rapid")
    sent = await wait_for(alice_ws, protocol.CHALLENGE_SENT)
    got = await wait_for(bob_ws, protocol.CHALLENGE_RECEIVED)
    check("the challenger is told it went out", sent["to"]["name"] == bob["name"], sent)
    check("the friend is told who and what",
          got["from"]["name"] == alice["name"] and got["tempo"] == "rapid", got)

    await send(alice_ws, type=protocol.CHALLENGE, id=carol["id"], tempo="rapid")
    msg = await wait_for(alice_ws, protocol.CHALLENGE_CLOSED, lambda m: m["id"] is None)
    check("one challenge out at a time", "already" in msg["reason"], msg)

    await send(bob_ws, type=protocol.CHALLENGE_ANSWER, id=got["id"], accept=False)
    msg = await wait_for(alice_ws, protocol.CHALLENGE_CLOSED, lambda m: m["id"] == got["id"])
    check("a decline reaches the challenger", "declined" in msg["reason"], msg)

    # Withdrawn.
    await send(carol_ws, type=protocol.CHALLENGE, id=alice["id"], tempo="bullet")
    got = await wait_for(alice_ws, protocol.CHALLENGE_RECEIVED)
    await send(carol_ws, type=protocol.CHALLENGE_CANCEL, id=got["id"])
    msg = await wait_for(alice_ws, protocol.CHALLENGE_CLOSED, lambda m: m["id"] == got["id"])
    check("a withdrawn challenge is taken down", "withdrew" in msg["reason"], msg)

    # The challenger leaving takes it with them.
    carol2, _ = await account(url, carol["name"])
    await send(carol2, type=protocol.CHALLENGE, id=alice["id"], tempo="bullet")
    got = await wait_for(alice_ws, protocol.CHALLENGE_RECEIVED)
    await carol2.close()
    msg = await wait_for(alice_ws, protocol.CHALLENGE_CLOSED, lambda m: m["id"] == got["id"])
    check("a challenger who disconnects withdraws it", "left" in msg["reason"], msg)

    # Accepted: both land in the same room, at the tempo asked for.
    await send(alice_ws, type=protocol.CHALLENGE, id=bob["id"], tempo="bullet")
    got = await wait_for(bob_ws, protocol.CHALLENGE_RECEIVED)
    await send(bob_ws, type=protocol.CHALLENGE_ANSWER, id=got["id"], accept=True)
    made = await wait_for(alice_ws, protocol.ROOM_CREATED)
    joined = await wait_for(bob_ws, protocol.ROOM_JOINED)
    check("accepting puts both in one room", made["code"] == joined["code"],
          (made, joined))
    st = await wait_for(bob_ws, protocol.ROOM_STATE)
    check("at the tempo the challenge named", st["tempo"] == "bullet", st["tempo"])
    check("and it is rated, both being signed in", st["rated"] is True,
          st.get("unrated_reason"))

    await send(carol_ws, type=protocol.CHALLENGE, id=alice["id"], tempo="rapid")
    msg = await wait_for(carol_ws, protocol.CHALLENGE_CLOSED, lambda m: m["id"] is None)
    check("a friend already in a room cannot be challenged", "in a game" in msg["reason"],
          msg)
    for ws in (alice_ws, bob_ws):
        await send(ws, type=protocol.LEAVE_ROOM)


async def refused(ws, kind: str) -> bool:
    """Send an admin request; True if it came back as a refusal and not data."""
    await send(ws, type=kind)
    reply_kind = {protocol.ADMIN_STATS: protocol.ADMIN_STATS,
                  protocol.ADMIN_LIST_GAMES: protocol.ADMIN_GAMES,
                  protocol.ADMIN_LIST_USERS: protocol.ADMIN_USERS}[kind]
    async def loop() -> dict:
        while True:
            m = json.loads(await ws.recv())
            if m["type"] in (protocol.ERROR, reply_kind):
                return m
    msg = await asyncio.wait_for(loop(), 8)
    return msg["type"] == protocol.ERROR and msg["reason"] == "not allowed"


async def test_admin(url, admin_ws, admin, bob_ws, bob) -> None:
    print("\n-- admin --")
    check("an admin is told so at sign-in", admin.get("admin") is True, admin)
    check("nobody else is", bob.get("admin") is False, bob)

    for kind in (protocol.ADMIN_STATS, protocol.ADMIN_LIST_GAMES,
                 protocol.ADMIN_LIST_USERS):
        check(f"{kind} refused to a signed-in non-admin", await refused(bob_ws, kind))
    async with websockets.connect(url) as anon:
        check("and to a guest", await refused(anon, protocol.ADMIN_STATS))

    await send(admin_ws, type=protocol.ADMIN_STATS)
    st = await wait_for(admin_ws, protocol.ADMIN_STATS)
    check("an admin gets live numbers", st["live"]["connections"] >= 4, st["live"])
    check("and stored ones", (st["stored"] or {}).get("users", 0) >= 4, st["stored"])
    check("with two weeks of games per day", len(st["stored"]["per_day"]) == 14)

    await send(admin_ws, type=protocol.ADMIN_LIST_USERS)
    users = await wait_for(admin_ws, protocol.ADMIN_USERS)
    names = {u["name"] for u in users["users"]}
    check("the user list has the accounts", bob["name"] in names, sorted(names)[:5])
    check("and says who is online",
          any(u["online"] for u in users["users"] if u["name"] == bob["name"]))
    await send(admin_ws, type=protocol.ADMIN_LIST_GAMES)
    games = await wait_for(admin_ws, protocol.ADMIN_GAMES)
    check("the game list answers", "total" in games and isinstance(games["games"], list))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    await test_presence(args.url)
    await test_spectating(args.url)
    await test_accounts(args.url)

    if FAILS:
        print(f"\nFAIL: {len(FAILS)} check(s)")
        for f in FAILS:
            print(f"  {f}")
        return 1
    print("\nPASS: presence, spectating, friends, challenges and admin")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
