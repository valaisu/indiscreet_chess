"""
Local accounts: a name, a password, and a session token.

Deliberately the smallest thing that works. No email is collected, so there is
nothing to leak and nothing to send; and because there is no email there is no
password reset, which removes the single most-attacked component of any auth
system. Losing a password loses the account, which is an acceptable trade for a
chess site and is stated plainly in the UI.

`users.provider` exists so that adding Google sign-in later is a new row value
rather than a migration of every account. Nothing here needs to change for it.

What is actually being defended:

  - The stored hash. argon2id, the current recommendation, with OWASP's
    minimum parameters. A dump of the users table must not yield passwords.
  - The event loop. Hashing is slow *on purpose*, and this process also runs
    every live game's 20 Hz tick, so a hash on the loop stalls every game on
    the machine. Every hash runs in a thread.
  - Guessing. Attempts are capped per connection and per address.

What is not defended, and should be said out loud: a password reused from
somewhere else is only as safe as this database, and this is a hobby project.
The sign-up screen says so.
"""

import asyncio
import hashlib
import logging
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import db

log = logging.getLogger("accounts")

# OWASP's minimum for argon2id: 19 MiB, 2 iterations, 1 lane. The library's
# defaults ask for 64 MiB, which is a lot to hand a 512 MB machine several
# times at once for no meaningful gain at this threat level.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

SESSION_DAYS = 30
MIN_PASSWORD = 8
MAX_PASSWORD = 128          # bcrypt-style truncation bugs do not apply, but an
                            # unbounded input is an unbounded amount of hashing

# Names are shown to the other player and stored. Letters, digits, underscore
# and hyphen only: no spaces (which make impersonation by padding trivial), no
# punctuation, nothing that has to be escaped anywhere it is displayed.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,19}$")

# Guessing limits. Per connection, and per address across connections, because
# five sockets each getting a fresh budget is not a limit.
MAX_ATTEMPTS_PER_CONN = 8
MAX_ATTEMPTS_PER_IP = 30
ATTEMPT_WINDOW = 900.0      # seconds


def check_name(name: object) -> str | None:
    if not isinstance(name, str):
        return "name must be text"
    if not NAME_RE.match(name):
        return ("names are 3 to 20 characters: letters, digits, "
                "underscore and hyphen, starting with a letter or digit")
    return None


def check_password(password: object) -> str | None:
    if not isinstance(password, str):
        return "password must be text"
    if len(password) < MIN_PASSWORD:
        return f"password must be at least {MIN_PASSWORD} characters"
    if len(password) > MAX_PASSWORD:
        return f"password must be at most {MAX_PASSWORD} characters"
    return None


def token_hash(token: str) -> bytes:
    """What goes in the sessions table. Never the token itself: a dump of that
    table must not let anyone sign in as anybody.

    Plain sha256, not argon2. The token is 32 random bytes from secrets, so
    there is no guessing to slow down - the reason to make a *password* hash
    expensive is that people choose weak passwords, and nobody chose this."""
    return hashlib.sha256(token.encode()).digest()


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(stored: str, password: str) -> bool:
    def _verify() -> bool:
        try:
            _hasher.verify(stored, password)
            return True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    return await asyncio.to_thread(_verify)


class Attempts:
    """Sign-in attempts per address, in a sliding window."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(ip, []) if now - t < ATTEMPT_WINDOW]
        hits.append(now)
        self._hits[ip] = hits
        return len(hits) <= MAX_ATTEMPTS_PER_IP

    def sweep(self) -> None:
        now = time.monotonic()
        for ip in list(self._hits):
            fresh = [t for t in self._hits[ip] if now - t < ATTEMPT_WINDOW]
            if fresh:
                self._hits[ip] = fresh
            else:
                del self._hits[ip]


async def sign_up(name: str, password: str) -> tuple[dict | None, str | None]:
    """Create an account and open a session. Returns (identity, error)."""
    reason = check_name(name) or check_password(password)
    if reason:
        return None, reason
    user = await db.create_user(name, await hash_password(password))
    if user is None:
        # Names are public by nature - the point of one is that other people
        # see it - so saying a name is taken reveals nothing that picking a
        # name does not already reveal.
        return None, "that name is taken"
    return await _open_session(user), None


async def sign_in(name: str, password: str) -> tuple[dict | None, str | None]:
    """Check a password and open a session. Returns (identity, error)."""
    if not isinstance(name, str) or not isinstance(password, str):
        return None, "name and password must be text"
    user = await db.find_user(name)
    # One message for both "no such name" and "wrong password". Not because it
    # hides anything - sign-up already tells you which names exist - but
    # because two different messages invite people to read meaning into them.
    if user is None or not await verify_password(user["password_hash"], password):
        return None, "wrong name or password"
    return await _open_session(user), None


async def resume(token: str) -> dict | None:
    """The identity behind a stored session token, if it is still valid."""
    if not isinstance(token, str) or not token:
        return None
    user = await db.session_user(token_hash(token))
    if user is None:
        return None
    return {"id": user["id"], "name": user["name"], "token": token,
            "ratings": await db.get_ratings(user["id"])}


async def sign_out(token: str) -> None:
    if isinstance(token, str) and token:
        await db.delete_session(token_hash(token))


async def _open_session(user: dict) -> dict:
    token = secrets.token_urlsafe(32)
    await db.create_session(user["id"], token_hash(token), SESSION_DAYS)
    return {"id": user["id"], "name": user["name"], "token": token,
            "ratings": await db.get_ratings(user["id"])}
