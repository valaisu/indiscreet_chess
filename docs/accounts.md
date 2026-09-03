# Accounts and rating - design, not yet built

Nothing in this document is implemented. It exists because the requirement was
"I need to understand why a solution is safe before we implement it", and that
is a decision, not a coding task. Read the recommendation, then say yes or pick
a different option.

Two hard constraints stated up front:

1. **Nothing stored on your computer**, ideally not even accessible to you.
2. **Anonymous play stays available**, and must not be second-class.

## The one-sentence version

**Do not store passwords at all - let Google/GitHub/Apple do the authenticating
(OAuth), and keep only an opaque provider ID plus a display name and a rating in
a hosted Postgres.** Then there is no password to leak, no reset flow to get
wrong, and no credential on your machine or in your backups.

## Why "just hash the passwords" is the wrong first project

Storing passwords safely is not one decision, it is about nine, and every one of
them is a way to get quietly breached:

- the hash must be memory-hard (argon2id or bcrypt) with correct parameters -
  SHA-256 is not a password hash;
- per-password salt, and a pepper held outside the database;
- constant-time comparison, to avoid timing oracles;
- rate limiting and lockout on login, or the hash strength is irrelevant;
- a password reset flow, which needs email delivery, single-use expiring
  tokens, and is historically the most-attacked part of any auth system;
- session tokens: length, storage, rotation, revocation, and cookie flags;
- a breach plan, because you now hold something worth stealing.

Each is doable. Together they are the whole project, and they buy you nothing a
player wants. You would be building a password database in order to display a
number next to a name.

## Recommended: OAuth sign-in, with anonymous play as the default

**What you store per player:** `provider` ("google"), `provider_user_id` (an
opaque string), a display name they choose, `rating`, `games_played`, and
timestamps. **What you never see:** their password, and - if you request only
the minimal scope - their email address.

Why this is safe enough to reason about in one paragraph: the only secret in
the system is your OAuth client secret, which lives in a Fly secret and grants
nothing except the ability to ask Google "is this person who they say they are".
Your database contains no credential. If it leaked entirely, the damage is a
list of display names and ratings. Nobody can log in as anybody with it.

Scope note, because you are cautious about this and should be: request the
**minimum** - for Google that is `openid` alone if you let players type their own
display name. `openid` returns a stable per-application user ID and nothing
else: no email, no profile, no contacts, no Drive. If you want to pre-fill a
name, `profile` adds the display name and avatar. Do not request `email` unless
you have a reason to send email; you do not.

**Anonymous play**: unchanged from today. No account, no rating, quick match and
room codes work exactly as they do now. A rated game simply requires both seats
to be signed in - if either is anonymous, the game is unrated and says so.

## Where the data would live

You already pay for Fly and rooms are in memory, so anything persistent is new
infrastructure and therefore **needs your explicit yes with a price attached**.
Three options, cheapest first:

| Option | Cost | Notes |
|---|---|---|
| Cloudflare D1 (SQLite) | Free tier covers this easily | You already deploy the client on Cloudflare. Access from Fly is over HTTP. |
| Supabase free tier | Free, paid at scale | Postgres plus OAuth handled for you - the least code by a distance. |
| Fly Postgres | ~$3-5/month, metered | Same provider as the server; you disliked Fly's billing model. |

Given your preference for hard caps and predictable pricing, **Supabase's free
tier is the best fit**: it provides the OAuth flow and the database together, so
the server's job shrinks to "verify this JWT, then read and write a rating row".
Its free tier stops rather than bills.

## Rating

Standard Elo, K = 32 under 30 games and K = 16 after, starting at 1200. It is
well understood, needs one formula, and nobody argues with it. Draws count as
half a point. Kept per tempo mode:

- **three ratings per player: bullet, rapid, slow.** As you specified,
  civilizations on and off share one rating within a mode - a civ is a balanced
  side-choice, not a different game.
- **Custom params are never rated.** The moment a player can set their own
  cooldown, the rating measures the settings.
- A rated game is one where: both seats are signed in, the tempo is one of the
  three presets, the view options are the defaults, and neither side is solo.

Forfeits by disconnect count as losses; otherwise leaving becomes free. The 30
second grace window already in the server is the right threshold.

## What would actually get built, in order

1. Schema and the Elo function, with a test for the arithmetic. No auth yet.
2. Sign-in button, OAuth round trip, session as an httpOnly cookie.
3. The server records a finished rated game and updates both ratings.
4. A leaderboard page and a rating shown in the lobby.

Steps 1 and 3 are small. Step 2 is where a mistake would matter, and it is the
step this design exists to make boring.

## What I need from you before any of it

1. **Yes to OAuth over passwords** - or tell me you want your own accounts, and
   I will write up what that honestly costs.
2. **Which provider(s)**: Google alone is the least friction. GitHub is one line
   more.
3. **Which database**, and an explicit yes to any spending it implies.
4. Confirm **display names are chosen by the player**, so no email is requested.
