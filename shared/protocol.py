# Bumped whenever a message shape changes in a way old clients cannot handle.
# The server announces it on connect so a browser holding a stale cached bundle
# says "reload" instead of failing in ways that look like game bugs.
VERSION = 12

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
# Personal settings that follow the account, as against the ones this browser
# keeps to itself. Only the keys the player changed while signed in travel;
# the account's copy overrides the device's wherever it has an opinion.
SET_SETTINGS = "SET_SETTINGS"

# --- Stored games ---
# The list carries results only. A recording is fetched one at a time, when a
# replay is opened: fifty of them at once would be a megabyte of nothing.
LIST_GAMES = "LIST_GAMES"
GET_GAME = "GET_GAME"

# --- Finding people and games ---
# Open rooms waiting for a second player, the signed-in players currently
# connected, and one player's public card. All three are polled rather than
# pushed: none of them has to be up to the second, and a push on every
# connect and sign-out is a broadcast to everybody for a number in a corner.
LIST_ROOMS = "LIST_ROOMS"
LIST_ONLINE = "LIST_ONLINE"
GET_PROFILE = "GET_PROFILE"

# --- Watching a game in progress ---
# Asked for by the presence key of somebody who is playing, never by room
# code: codes are how a private room is joined, and listing them would let
# anyone walk into a lobby they were not invited to.
SPECTATE = "SPECTATE"

# --- Friends ---
# Request and accept. Every one of these is answered with a fresh FRIEND_LIST,
# carrying a `note` when the request was refused, so the panel that sent it is
# also the place the answer appears.
LIST_FRIENDS = "LIST_FRIENDS"
FRIEND_REQUEST = "FRIEND_REQUEST"
FRIEND_RESPOND = "FRIEND_RESPOND"
FRIEND_REMOVE = "FRIEND_REMOVE"

# --- Challenges ---
# A challenge to a friend who is online. Held in memory only: a restart loses
# the open ones, which is no loss for something that expires in a minute.
CHALLENGE = "CHALLENGE"
CHALLENGE_ANSWER = "CHALLENGE_ANSWER"
CHALLENGE_CANCEL = "CHALLENGE_CANCEL"

# --- Admin ---
# Checked against the socket's signed-in account on every request. The client
# shows the tab only to an admin, but that is display: the refusal is here.
ADMIN_STATS = "ADMIN_STATS"
ADMIN_LIST_GAMES = "ADMIN_LIST_GAMES"
ADMIN_LIST_USERS = "ADMIN_LIST_USERS"

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
# Sent to both seats once the finished game is in the database, so the replay
# they watch is the stored one - the whole game, rather than the half of it
# their own client was allowed to see while playing.
GAME_SAVED = "GAME_SAVED"
GAME_LIST = "GAME_LIST"
GAME_RECORD = "GAME_RECORD"
ROOM_LIST = "ROOM_LIST"
ONLINE_LIST = "ONLINE_LIST"
# One player as anyone may see them: their name and their ratings. Not their
# games - a stored recording holds the hidden preparation and destinations
# that the room's view settings kept from the opponent, so it stays with the
# people who played it.
PROFILE = "PROFILE"
# You are now watching a game. ROOM_STATE and GAME_STATE follow as for a seat.
SPECTATING = "SPECTATING"
# The game you were watching is gone: the room was closed, or the players
# started a rematch, which is a new game nobody asked you to watch.
SPECTATE_END = "SPECTATE_END"
FRIEND_LIST = "FRIEND_LIST"
# The challenger's own copy, so its screen can show the countdown and Cancel.
CHALLENGE_SENT = "CHALLENGE_SENT"
# Unsolicited: pushed to every socket the challenged player has open.
CHALLENGE_RECEIVED = "CHALLENGE_RECEIVED"
# Pushed to both sides when a challenge ends, acceptance included: a second
# tab still showing the banner has to take it down.
CHALLENGE_CLOSED = "CHALLENGE_CLOSED"
ADMIN_GAMES = "ADMIN_GAMES"
ADMIN_USERS = "ADMIN_USERS"

# Room codes use an alphabet without visually ambiguous characters.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4
