# Bumped whenever a message shape changes in a way old clients cannot handle.
# The server announces it on connect so a browser holding a stale cached bundle
# says "reload" instead of failing in ways that look like game bugs.
VERSION = 6

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
RESIGN = "RESIGN"
PING = "PING"

# Server -> client
ROOM_CREATED = "ROOM_CREATED"
ROOM_JOINED = "ROOM_JOINED"
ROOM_STATE = "ROOM_STATE"
OPPONENT_LEFT = "OPPONENT_LEFT"
OPPONENT_REJOINED = "OPPONENT_REJOINED"
PONG = "PONG"
SERVER_HELLO = "SERVER_HELLO"

# Room codes use an alphabet without visually ambiguous characters.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4
