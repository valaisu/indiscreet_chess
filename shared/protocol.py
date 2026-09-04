# Bumped whenever a message shape changes in a way old clients cannot handle.
# The server announces it on connect so a browser holding a stale cached bundle
# says "reload" instead of failing in ways that look like game bugs.
VERSION = 8

# --- In-game messages (unchanged) ---
HELLO = "HELLO"
READY = "READY"
QUEUE_MOVE = "QUEUE_MOVE"
GAME_STATE = "GAME_STATE"
MOVE_REJECTED = "MOVE_REJECTED"
GAME_OVER = "GAME_OVER"
ERROR = "ERROR"

# --- LAN discovery (local play only; unused online) ---
DISCOVER = "DISCOVER"
ANNOUNCE = "ANNOUNCE"

DISCOVERY_PORT = 8766

# --- Lobby messages ---
# Client -> server
CREATE_ROOM = "CREATE_ROOM"
JOIN_ROOM = "JOIN_ROOM"
QUICK_MATCH = "QUICK_MATCH"
REJOIN = "REJOIN"
SET_READY = "SET_READY"
LEAVE_ROOM = "LEAVE_ROOM"
REMATCH = "REMATCH"
RESIGN = "RESIGN"
PING = "PING"

# --- Accounts ---
# Local accounts: a name and a password, no email. RESUME_SESSION carries the
# token a previous sign-in returned, so a reload does not ask again.
SIGN_UP = "SIGN_UP"
SIGN_IN = "SIGN_IN"
SIGN_OUT = "SIGN_OUT"
RESUME_SESSION = "RESUME_SESSION"

# --- Stored games ---
# The list carries results only. A recording is fetched one at a time, when a
# replay is opened: fifty of them at once would be a megabyte of nothing.
LIST_GAMES = "LIST_GAMES"
GET_GAME = "GET_GAME"

# Server -> client
ROOM_CREATED = "ROOM_CREATED"
ROOM_JOINED = "ROOM_JOINED"
ROOM_STATE = "ROOM_STATE"
OPPONENT_LEFT = "OPPONENT_LEFT"
OPPONENT_REJOINED = "OPPONENT_REJOINED"
PONG = "PONG"
SERVER_HELLO = "SERVER_HELLO"
# Who this socket is signed in as, or nobody. Sent only in reply to one of the
# four account messages above; nothing arrives unasked.
AUTH_STATE = "AUTH_STATE"
# Auth failures are their own message, not ERROR: they belong on the sign-in
# form, and the lobby status line is off-screen while that form is open.
AUTH_ERROR = "AUTH_ERROR"
# Sent to both seats when a finished game moved their ratings.
RATING_UPDATE = "RATING_UPDATE"
GAME_LIST = "GAME_LIST"
GAME_RECORD = "GAME_RECORD"

# Room codes use an alphabet without visually ambiguous characters.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4
