/**
 * Entry point: lobby wiring, input handling, render loop.
 */

import { Net } from "./net.ts";
import { Renderer } from "./render.ts";
import { interpolate } from "./interp.ts";
import { snapDestination, findPieceAt, type Piece } from "./geometry.ts";
import * as P from "./protocol.ts";
import { presetParams } from "./presets.ts";
import { describe, globalEffects, pieceEffects, PLURAL, TITLE, CIV_NAMES,
         civName } from "./civs.ts";
import { CIV_ICONS } from "./civicons.ts";
import { type GameState, forPiece } from "./protocol.ts";
import { settings, save as saveSettings, PRECISE_MIN_DRAG, VIEW_DEFAULTS,
         keyMatches, keyLabel, applyProfile, setPublisher,
         type View } from "./settings.ts";
import { Recording, Player, SPEEDS, NORMAL_SPEED } from "./replay.ts";
import * as account from "./account.ts";
import { expand, ExpandError } from "./expand.ts";
import type { StoredGame, OpenRoom, PublicProfile, SeatCard,
              GameSide } from "./protocol.ts";

const CLICK_R_SELECT = 0.5; // forgiving radius when nothing is selected
const CLICK_R_SWITCH = 0.3; // strict radius once a piece is selected
const MOVE_SNAP_MAX = 0.625; // ignore clicks further than this from a legal spot
const RECONNECT_TRIES = 10; // 10 x 3s covers both a Fly deploy and the 30s grace
const RECONNECT_DELAY_MS = 3000;

/**
 * Which server to talk to. A player never types this: the bundle is built
 * against the server it belongs to. `?server=ws://localhost:8765` overrides it
 * for development, and because the override lives in the URL there is nothing
 * persisted for a typo to get stuck on.
 */
const SERVER_URL =
  new URLSearchParams(location.search).get("server") ||
  (import.meta as any).env?.VITE_SERVER_URL ||
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname}:8765`;

const $ = (id: string) => document.getElementById(id)!;
const lobby = $("lobby") as HTMLDivElement;
const gameEl = $("game") as HTMLDivElement;
const canvas = $("board") as HTMLCanvasElement;
const statusEl = $("status") as HTMLDivElement;
const banner = $("banner") as HTMLDivElement;
const pregame = $("pregame") as HTMLDivElement;
const postgame = $("postgame") as HTMLDivElement;
const replayBar = $("replay-bar") as HTMLDivElement;
const gameBar = $("game-bar") as HTMLDivElement;

let net: Net;
let renderer: Renderer;
let state: GameState | null = null;
let stateAt = 0;
let selectedId: string | null = null;
let dragId: string | null = null;
let dragPos: [number, number] | null = null;
let dragWasSelected = false;
let rejoining = false;
let rejoinAttempts = 0;
let retryTimer: number | null = null;
let stale = false; // server speaks a protocol this bundle does not
let soloRoom = false; // this client holds both seats
let inRoom = false;   // between joining a room and leaving it
let precise = false; // any drag distance counts as a move while this is on
let preciseLatched = false; // the on-screen button holds it; the key only taps
let recording = new Recording();
let player: Player | null = null; // non-null while watching the replay
let replayReturn: "postgame" | "profile" | "card" = "postgame";
/**
 * The stored copy of the game just finished, once the server has written it.
 *
 * The frames this client kept are only what it was allowed to see while
 * playing - with the default settings, no opponent mana and no idea where
 * their pieces were going. The stored log is the whole game, so the replay
 * asks for that and the two ways into a replay show the same thing.
 */
let lastGameId: string | null = null;
/** Where a recording being fetched should be shown, and from whose side. */
let pendingReplay:
  { from: "postgame" | "profile" | "card"; seat: string | null } | null = null;
let matchLogged = false; // one history row per game, however many final frames
/** The history row this game was written to, so GAME_SAVED can name it. */
let lastMatchId: string | null = null;

/**
 * A finished game, as a player without an account knows about it. The row is
 * small and lives in localStorage; it is only an index. The game itself is on
 * the server like every other one, and `gameId` is how this browser asks for
 * it - so a replay survives a reload rather than living in `recordings` until
 * the tab is closed.
 */
interface Match {
  id: string;
  at: number;          // epoch ms
  tempo: string;
  civs: Record<string, string | null>;
  seat: string | null; // the colour this client played, null in solo
  winner: string;
  seconds: number;
  solo: boolean;
  /** The server's id for this game. Absent when it was never stored: no
   *  database behind the server, or the room was left before it was saved. */
  gameId?: string | null;
}

const HISTORY_KEY = "matches";
const MAX_HISTORY = 50;
/** Games shown at once, in every list. Matches the server's page size. */
const PAGE = 20;
const recordings = new Map<string, Recording>();
/** Finished games the server holds for this account. Empty when signed out. */
let storedGames: StoredGame[] = [];
/** Where the account's own history is paged to, and how long it is. */
let storedOffset = 0;
let storedTotal = 0;
/** The player whose card is open, with their page of games. */
let cardId: string | null = null;
let cardGames: StoredGame[] = [];
let cardOffset = 0;
let cardTotal = 0;
/** Which page of the local history a signed-out player is looking at. */
let localOffset = 0;
/**
 * Both seats of the room, as the server last described them: who is sitting
 * there, whether they are ready, and what they are rated at this tempo. The
 * pre-game screen, the name plates and the rematch prompt all read this, so
 * none of them can claim something the room does not say.
 */
let roomSeats: Record<string, SeatCard> | null = null;
/** Whether a game played in this room would be rated, and why not.  */
let roomRated: { rated: boolean; reason: string | null } = { rated: true, reason: null };
/** Rating movement from the game just finished, shown on the post-game card. */
let lastRating: { tempo: string; white: Rated; black: Rated } | null = null;
interface Rated { before: number; after: number }

function setStatus(text: string, isError = false): void {
  if (stale) return; // a version warning outranks routine lobby chatter
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

function showBanner(text: string): void {
  banner.textContent = text;
  banner.style.display = text ? "block" : "none";
}

async function ensureConnected(): Promise<boolean> {
  if (net) return true;
  net = new Net(SERVER_URL);
  net.on(P.ERROR, (m) => {
    if (rejoining) {
      // Rooms live only in the server's memory, and a seat left empty in the
      // lobby is given back to the room after the grace window. Either way
      // there is nothing to return to, so stop and let the lobby be used -
      // holding a dead seat is what used to answer "room is full".
      rejoining = false;
      sessionStorage.removeItem("seat");
      showBanner("That room has ended, or your seat was given up.");
      inRoom = false;
      return;
    }
    // A recording that was asked for and refused. The game just finished is
    // still in memory, so that one falls back to the frames this client kept.
    if (pendingReplay) {
      const pending = pendingReplay;
      pendingReplay = null;
      resetReplayButton();
      if (pending.from === "postgame") {
        watchReplay(recording, pending.seat, "postgame");
        return;
      }
      const card = $("player-card") as HTMLElement;
      $(card.hidden ? "pf-summary" : "pc-note").textContent =
        m.reason ?? "that game could not be opened";
      return;
    }
    // The lobby's status line is off-screen once the pre-game screen is up,
    // and a civilization can push piece size past what the opening position
    // allows - so a rejection at Ready has to be visible where it happens.
    const reason = m.reason ?? "server error";
    if (pregame.style.display === "block") {
      $("pg-status").textContent = reason;
      ($("rr-me") as HTMLButtonElement).disabled = false;
    }
    if (postgame.style.display === "block") {
      $("pg-result").textContent = reason;
      ($("btn-rematch") as HTMLButtonElement).disabled = false;
    }
    setStatus(reason, true);
  });
  net.on(P.ROOM_CREATED, (m) => {
    soloRoom = !!m.solo;
    inRoom = true;
    createEl.style.display = "none";
    findEl.style.display = "none";
    if (!m.solo) history.replaceState(null, "", `#${m.code}`);
    showPregame(m.code);
  });
  net.on(P.ROOM_JOINED, (m) => {
    rejoining = false;
    rejoinAttempts = 0;
    createEl.style.display = "none";
    findEl.style.display = "none";
    soloRoom = !!savedSeat()?.solo;
    inRoom = true;
    showBanner("");
    showPregame(m.code);
  });
  net.on(P.ROOM_STATE, (m) => {
    // The room's tempo, per seat, so a joiner applies their civ to it rather
    // than to whatever their own create screen happens to say. Both columns
    // are shown before anyone readies: a balanced room may have handed this
    // seat the worse half, and that has to be visible, not merely true.
    roomSeats = m.seats ?? null;
    roomRated = { rated: m.rated !== false, reason: m.unrated_reason ?? null };
    if (m.base_params) {
      baseParams = m.base_params.white ?? null;
      showRoomTerms(m.base_params, !!m.balanced, m.view);
    }
    renderPlates();
    const me = (net.color ?? "white") as "white" | "black";
    const them = me === "white" ? "black" : "white";
    // Drawn whatever the room is doing, but never before returnToPregame
    // below: that redraws the bar from scratch and would undo it.
    const drawSeats = () => {
      showSeat("rr-me", "You", soloRoom ? null : me, seatCard(me));
      if (!soloRoom) showSeat("rr-them", "Opponent", them, seatCard(them));
    };
    if (!m.waiting) {
      // Reloading after a game has ended rejoins a room that is not waiting,
      // and skipping this left the pre-game screen saying "not here yet"
      // about an opponent sitting right there.
      drawSeats();
      // A finished room, or one whose game is running. The only thing that
      // moves a finished one is a rematch, and that takes both players, so the
      // card has to say who has asked.
      showRematchState();
      // Only ever read on the pre-game screen, which is where a player who
      // reloaded after the game ended finds themselves - looking at a
      // civilization picker for a game that is over.
      $("pg-status").textContent =
        "That game has finished. Exit to the lobby to start another.";
      return;
    }
    if (gameEl.style.display === "block") returnToPregame(m.code);
    drawSeats();
    const mine = m.ready?.[me];
    $("pg-status").textContent = "";
    for (const el of civCards.children) el.classList.toggle("pick", !mine);
  });
  net.on(P.AUTH_STATE, () => void refreshOnline());
  net.on(P.ROOM_LIST, (m) => renderRooms(m.rooms ?? []));
  net.on(P.ONLINE_LIST, (m) => renderOnline(m));
  net.on(P.PROFILE, (m: PublicProfile) => renderPlayerCard(m));
  net.on(P.GAME_STATE, (m: GameState) => {
    // A frame can still be in flight when the room is left, and acting on it
    // would drag the player back onto the board from the lobby.
    if (!inRoom) return;
    // Gate on the board, not the lobby: the pre-game screen has already hidden
    // the lobby by this point.
    if (gameEl.style.display !== "block") enterGame();
    showCivLegend(m.civs);
    state = m;
    stateAt = performance.now();
    recording.push(m, stateAt);
    if (m.game_over) {
      showBanner("");
      logMatch(m);
      showPostgame(m.winner === "draw" ? "Draw" : `${m.winner} wins`);
    }
  });
  net.on(P.AUTH_STATE, (m) => {
    account.applyAuthState(m);
    // Signing in adopts whatever the account has an opinion about; signing out
    // drops back to this device's own values. Either way the numbers in the
    // Settings tab may have just changed without anybody typing there.
    applyProfile(m.user ? (m.user.settings ?? {}) : null);
    drawSettings();
    setAuthBusy(false);
    $("ac-status").textContent = "";
    $("ac-status").classList.remove("error");
    // Never leave a password sitting in a field across a screen change.
    ($("ac-pass") as HTMLInputElement).value = "";
    if (account.identity) ($("ac-name") as HTMLInputElement).value = "";
    // Signing in or out changes which history is the real one, so the list is
    // re-fetched rather than filtered: the server's copy and the local one are
    // different sets, not the same set seen two ways.
    storedGames = [];
    storedOffset = 0;
    storedTotal = 0;
    if (account.identity) net.listGames(0);
    renderAccount();
    renderProfile();
  });
  net.on(P.AUTH_ERROR, (m) => {
    // On the account form, not the lobby status line: that line is a tab away
    // from where the button was pressed.
    $("ac-status").textContent = m.reason ?? "sign in failed";
    $("ac-status").classList.add("error");
    setAuthBusy(false);
  });
  net.on(P.GAME_LIST, (m) => {
    storedGames = m.games ?? [];
    storedOffset = m.offset ?? 0;
    storedTotal = m.total ?? storedGames.length;
    renderProfile();
  });
  net.on(P.GAME_RECORD, (m) => {
    // Which button asked for it. Kept from the request rather than worked out
    // from the reply, because the game just finished is in no list yet.
    const pending = pendingReplay;
    pendingReplay = null;
    resetReplayButton();
    try {
      const rec = new Recording();
      // The stored log expands into the same frames the server broadcast, so
      // everything downstream - the player, the renderer, the seek bar - is
      // the code that already exists.
      for (const [i, frame] of expand(m.recording).entries()) {
        rec.push(frame, i * (1000 / (m.recording.header?.tick_rate ?? 20)));
      }
      watchReplay(rec, pending?.seat ?? null, pending?.from ?? "profile");
    } catch (err) {
      // The game just finished is still in memory, so a log this bundle
      // cannot read is not the end of watching it - only of watching all of
      // it. Anywhere else there is nothing to fall back to, so say so where
      // the row was clicked: a card replay reporting onto the profile tab
      // puts the message behind the card it was started from.
      if (pending?.from === "postgame") {
        watchReplay(recording, pending.seat, "postgame");
        return;
      }
      const card = $("player-card") as HTMLElement;
      $(card.hidden ? "pf-summary" : "pc-note").textContent =
        err instanceof ExpandError
          ? "That recording was made by an older version and cannot be replayed."
          : "That recording could not be read.";
    }
  });
  net.on(P.GAME_SAVED, (m) => {
    lastGameId = m.game_id ?? null;
    // The history row was written on the final frame, one moment before this
    // arrived. Name the stored game on it now: without an account that row is
    // the only index this browser has, and the id is what a replay opened
    // after a reload is fetched by.
    if (lastGameId && lastMatchId) attachGameId(lastMatchId, lastGameId);
  });
  net.on(P.RATING_UPDATE, (m) => {
    lastRating = { tempo: m.tempo, white: m.white, black: m.black };
    showRatingChange();
    // The identity carries the old number until the next sign-in, so refresh
    // it here or the profile shows a rating the game just changed.
    const seat = soloRoom ? null : net.color;
    if (account.identity && seat) {
      const r = account.identity.ratings[m.tempo] ??
                { rating: m[seat].before, games: 0 };
      account.identity.ratings[m.tempo] = { rating: m[seat].after,
                                            games: r.games + 1 };
    }
    net.listGames(storedOffset);
  });
  net.on(P.MOVE_REJECTED, (m) => setStatus(`rejected: ${m.reason}`, true));
  net.on(P.OPPONENT_LEFT, (m) => startLeftCountdown(m.grace_seconds ?? 30));
  net.on(P.OPPONENT_REJOINED, () => {
    stopLeftCountdown();
    showBanner("");
  });
  net.on("version-mismatch", () => {
    // The banner only exists on the game screen, and a mismatch is noticed at
    // connect time - usually still in the lobby. Say it in both places, and
    // set the flag last so setStatus can pin it against later messages.
    const text = "New version available. Reload the page.";
    showBanner(text);
    setStatus(text, true);
    stale = true;
  });
  net.on("close", () => {
    if (stale || !savedSeat() || state?.game_over) {
      if (!stale) showBanner("Disconnected");
      return;
    }
    // A retry already owns the reconnect; this close is that attempt failing.
    if (rejoining) scheduleRetry();
    else beginRejoin();
  });

  try {
    await net.connect();
    // A stored token is offered as soon as there is a socket to offer it on,
    // so a reload comes back signed in. Done here rather than at page load
    // because this also runs after a reconnect.
    account.resume((m) => net.send(m));
    net.listOnline();
    return true;
  } catch (err) {
    setStatus(String((err as Error).message), true);
    net = undefined as any;
    return false;
  }
}

let leftTimer: number | null = null;
function startLeftCountdown(seconds: number): void {
  let left = Math.ceil(seconds);
  showBanner(`Opponent disconnected, ${left}s left`);
  stopLeftCountdown();
  leftTimer = window.setInterval(() => {
    left -= 1;
    if (left <= 0) stopLeftCountdown();
    else showBanner(`Opponent disconnected, ${left}s left`);
  }, 1000);
}
function stopLeftCountdown(): void {
  if (leftTimer !== null) window.clearInterval(leftTimer);
  leftTimer = null;
}

// --- reconnect --------------------------------------------------------------

function savedSeat(): { code: string; token: string; solo?: boolean } | null {
  const saved = sessionStorage.getItem("seat");
  return saved ? JSON.parse(saved) : null;
}

function beginRejoin(): void {
  rejoining = true;
  rejoinAttempts = 0;
  retry();
}

function scheduleRetry(): void {
  // connect() rejecting and the socket closing both land here; run once.
  if (retryTimer !== null) return;
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    retry();
  }, RECONNECT_DELAY_MS);
}

function retry(): void {
  const seat = savedSeat();
  if (stale || !seat) {
    rejoining = false;
    return;
  }
  if (rejoinAttempts >= RECONNECT_TRIES) {
    rejoining = false;
    showBanner("Disconnected. Reload to start a new game.");
    return;
  }
  rejoinAttempts += 1;
  showBanner(`Reconnecting\u2026 (${rejoinAttempts}/${RECONNECT_TRIES})`);
  net
    .connect()
    .then(() => net.rejoin(seat.code, seat.token))
    .catch(scheduleRetry);
}

/**
 * One civilization per seat. A normal game only ever fills "white", which is
 * this player's own pick whatever colour they were dealt; solo fills both,
 * because the point of solo is to try two sides against each other.
 */
const picks: Record<"white" | "black", string> = { white: "none", black: "none" };
let activeSeat: "white" | "black" = "white";
let baseParams: Record<string, number> | null = null;

const COLOR_LABEL: Record<string, string> = { white: "White", black: "Black" };

/** One seat as the server last described it. Solo owns both, as one cell. */
function seatCard(color: "white" | "black"): SeatCard | null {
  if (!roomSeats) return null;
  if (soloRoom) {
    const w = roomSeats.white, b = roomSeats.black;
    return {
      present: true,
      name: w?.name ?? b?.name ?? null,
      rating: w?.rating ?? null,
      ready: !!(w?.ready && b?.ready),
      rematch: !!(w?.rematch && b?.rematch),
    };
  }
  return roomSeats[color] ?? null;
}

/** "Valo 1215", or "anonymous" for a seat with no account behind it. */
function personInto(el: HTMLElement, card: SeatCard | null): void {
  el.textContent = "";
  if (!card || !card.present) return;
  const name = document.createElement("span");
  // Another player's text always arrives as a node, never as markup.
  name.textContent = card.name ?? "anonymous";
  if (!card.name) name.style.opacity = "0.7";
  el.append(name);
  if (card.rating) {
    const r = document.createElement("span");
    r.className = "rating";
    r.textContent = ` ${Math.round(card.rating.rating)}`;
    el.append(r);
  }
}

/**
 * One cell of the ready bar. Both cells are always on screen, and the bar is
 * sticky: a game that has not started looks identical whichever seat is the
 * one still choosing, and the cards below it are a long scroll.
 *
 * The cell names the colour as well as the person. It used to say only "You"
 * and "Opponent", which leaves the one thing the room's terms are written
 * against - which of you is white - nowhere on the screen.
 *
 * Your own cell is the button. It reports the server's view of your seat, not
 * the click, so it cannot claim a readiness the room has not recorded.
 */
function showSeat(id: string, who: string, color: "white" | "black" | null,
                  card: SeatCard | null): void {
  const cell = $(id);
  const seated = !!card?.present;
  const ready = !!card?.ready;
  cell.className = `rr ${!seated ? "empty" : ready ? "yes" : "no"}`;
  const label = color === null ? who : `${who} - ${COLOR_LABEL[color]}`;
  (cell.querySelector(".who") as HTMLElement).textContent =
    cell instanceof HTMLButtonElement && !ready ? `${label} - press when ready` : label;
  personInto(cell.querySelector(".nm") as HTMLElement, card);
  (cell.querySelector(".st") as HTMLElement).textContent =
    !seated ? "not here yet" : ready ? "\u2713 Ready" : "still choosing";
  if (cell instanceof HTMLButtonElement) cell.disabled = ready;
}

/** Build a civ card. Pickable ones select; the lobby copy is read-only. */
function civCard(civ: string, pickable: boolean): HTMLElement {
  const card = document.createElement("div");
  card.className = pickable ? "card pick" : "card";
  card.dataset.civ = civ;
  const effects = civ === "none" ? [] : describe(civ);
  card.innerHTML =
    `<div class="head">${CIV_ICONS[civ]}` +
    `<div><h3>${civName(civ)}</h3><p class="title">${TITLE[civ]}</p></div>` +
    `<span class="tag"></span></div><ul>` +
    // The base civilization is a choice like the other eight, so its card is
    // shaped like theirs: an empty list would read as a card still loading.
    (civ === "none"
      ? `<li class="plain"><span>Every value exactly as the tempo sets it</span></li>`
      : effects
          .map((e) => `<li class="${e.good ? "good" : "bad"}">` +
                      `<span>${e.what}</span><span class="amt">${e.amount}</span></li>`)
          .join("")) +
    "</ul>";
  if (pickable) {
    card.addEventListener("click", () => {
      picks[activeSeat] = civ;
      refreshPicks();
    });
  }
  return card;
}

/** Highlight the active seat's pick, and label who has taken what in solo. */
function refreshPicks(): void {
  for (const el of civCards.children) {
    const civ = (el as HTMLElement).dataset.civ!;
    el.classList.toggle("on", picks[activeSeat] === civ);
    const taken = soloRoom
      ? (["white", "black"] as const).filter((seat) => picks[seat] === civ)
      : [];
    const tag = el.querySelector(".tag") as HTMLElement;
    tag.textContent = taken.map((seat) => (seat === "white" ? "W" : "B")).join(" ");
    tag.title = taken.join(" and ");
  }
  $("seat-white").classList.toggle("on", activeSeat === "white");
  $("seat-black").classList.toggle("on", activeSeat === "black");
}

const civCards = $("civ-cards") as HTMLDivElement;
for (const civ of ["none", ...CIV_NAMES]) civCards.append(civCard(civ, true));
for (const civ of ["none", ...CIV_NAMES]) $("civ-reference").append(civCard(civ, false));
refreshPicks();

function showPregame(code: string): void {
  lobby.style.display = "none";
  pregame.style.display = "block";
  $("pg-code").textContent = code;
  $("pg-terms").textContent = "";
  $("pg-seats").style.display = soloRoom ? "flex" : "none";
  $("pg-lede").textContent = soloRoom
    ? "You play both sides. Pick a civilization for each seat, then press Ready."
    : "Pick a civilization, then press Ready. The game starts once both of you"
      + " have, and your opponent cannot see your choice until it does.";
  // Drawn before the first ROOM_STATE so the bar is never briefly absent.
  // Your own seat is known the moment you have one; the opponent's is not.
  $("rr-them").style.display = soloRoom ? "none" : "";
  const mine: SeatCard = {
    present: true, name: account.identity?.name ?? null,
    rating: null, ready: false, rematch: false,
  };
  showSeat("rr-me", soloRoom ? "Both seats" : "You",
           soloRoom ? null : (net.color as "white" | "black" | null), mine);
  if (!soloRoom) showSeat("rr-them", "Opponent",
                          net.color === "white" ? "black" : "white", null);
  if (!soloRoom) activeSeat = "white";
  refreshPicks();
}

/**
 * Write the current mode+civ into the settings fields. "Custom" means the
 * fields were hand-set, so neither is reapplied - a civ multiplier compounds
 * if it lands on values it has already modified.
 */
/** What each tempo is for, kept out of the labels themselves. */
const MODE_NOTE: Record<string, string> = {
  bullet: "Enough time to recapture, and no more.",
  rapid:  "Enough time to dodge a long move.",
  slow:   "Enough time to intercept a piece already on its way.",
  custom: "Enough time for whatever the parameters below say.",
};

/** The chosen tempo. "custom" means the parameter fields were hand-edited. */
let tempo = "bullet";

/** Balanced mode: the two seats get different columns. */
let balanced = false;

/** Who the room being built is open to. */
let whoCanJoin: "open" | "code" | "solo" = "open";

const createEl = $("create") as HTMLDivElement;
const findEl = $("find") as HTMLDivElement;

/**
 * Choose a tempo. `writeFields` is false when the choice came from editing a
 * parameter by hand: the fields are already what the player wants, and a
 * preset would overwrite them.
 */
function setTempo(name: string, writeFields = true): void {
  tempo = name;
  for (const b of document.querySelectorAll<HTMLElement>("#tempo-bar button")) {
    b.classList.toggle("on", b.dataset.mode === name);
  }
  $("mode-note").textContent = MODE_NOTE[name] ?? "";
  if (!writeFields) return;
  const params = presetParams(name);
  if (!params) return;
  for (const input of document.querySelectorAll<HTMLInputElement>("[data-param]")) {
    const value = params[input.dataset.param!];
    if (value !== undefined) input.value = String(value);
  }
}

/** The room's information rules, as set by whoever opens the room. */
function readView(): View {
  const view = { ...VIEW_DEFAULTS };
  for (const input of document.querySelectorAll<HTMLInputElement>("[data-view]")) {
    view[input.dataset.view as keyof View] = input.checked;
  }
  return view;
}

/**
 * One side's column of the movement parameters. Both columns exist in the DOM
 * at all times - balanced mode only reveals the second - so this never has to
 * ask which mode the screen is in.
 */
function readParams(seat: "white" | "black" = "white"): Record<string, number> {
  const params: Record<string, number> = {};
  const sel = `[data-param][data-seat="${seat}"]`;
  for (const input of document.querySelectorAll<HTMLInputElement>(sel)) {
    const value = parseFloat(input.value);
    if (!Number.isNaN(value)) params[input.dataset.param!] = value;
  }
  return params;
}

/** Black's column, or null when the room is even and both sides share one. */
function readBlackParams(): Record<string, number> | null {
  return balanced ? readParams("black") : null;
}

// --- the room's terms, shown before anyone readies ---------------------------

/** Field names as a player reads them, and which direction is an advantage. */
const PARAM_LABEL: Record<string, [string, number]> = {
  mana_refill_rate:     ["Mana / sec", 1],
  maximum_mana:         ["Max mana", 1],
  preparation_period:   ["Prep (s)", -1],
  cooldown:             ["Cooldown (s)", -1],
  movement_speed:       ["Speed", 1],
  movement_freedom_deg: ["Freedom (deg)", 1],
  base_move_cost:       ["Move cost", -1],
  distance_cost:        ["Cost / distance", -1],
  diameter_piece:       ["Piece size", 0],
};

const VIEW_LABEL: Record<string, string> = {
  enemy_mana:     "opponent's mana",
  enemy_prep:     "opponent's preparation",
  enemy_cooldown: "opponent's cooldowns",
  enemy_dest:     "where opponent's moves are headed",
};

/**
 * What this room is, from the server's own ROOM_STATE.
 *
 * The reason this exists: per-seat parameters were removed from this codebase
 * once because a room could hand the joiner a crippled seat while announcing
 * only one set of numbers. They are back, so the numbers have to be on screen
 * before the Ready button means anything. Only the rows that differ are shown;
 * an even room is one line.
 */
function showRoomTerms(seats: Record<string, Record<string, number>>,
                       isBalanced: boolean,
                       view: Record<string, boolean> | undefined): void {
  const box = $("pg-terms");
  box.textContent = "";
  const white = seats.white ?? {};
  const black = seats.black ?? {};

  const head = document.createElement("div");
  head.className = "terms-head";
  const label = TEMPO_LABEL[tempoName(white)] ?? tempoName(white);
  const changed = Object.entries(view ?? {})
    .filter(([k, v]) => v !== VIEW_DEFAULTS[k as keyof View])
    .map(([k, v]) => `${v ? "shows" : "hides"} ${VIEW_LABEL[k] ?? k}`);
  head.textContent = `${label} tempo`
    + (isBalanced ? ", balanced" : "")
    + (changed.length ? `. This room ${changed.join(", ")}.` : ".");
  box.append(head);

  // Whether the game will count, from the server's own answer rather than
  // this screen's guess at the rules. Every condition is known now, and after
  // the game is too late to have wanted a different room.
  const rated = document.createElement("div");
  rated.className = "terms-rated";
  rated.textContent = roomRated.rated
    ? "This game will be rated."
    : `Not rated: ${roomRated.reason ?? "this room's settings"}.`;
  box.append(rated);

  if (!isBalanced) return;

  const mine = soloRoom ? null : net.color;
  const table = document.createElement("table");
  const hrow = document.createElement("tr");
  // Which column is yours is said in the heading, not by colouring the cells:
  // the cells already carry the better/worse colour, which is the thing you
  // actually need to read, and two meanings on one property is neither.
  for (const h of ["", "white", "black"]) {
    const th = document.createElement("th");
    th.textContent = h === "" ? ""
      : `${h === "white" ? "White" : "Black"}${h === mine ? " (you)" : ""}`;
    if (h === mine) th.classList.add("mine");
    hrow.append(th);
  }
  table.append(hrow);

  for (const [key, [name, good]] of Object.entries(PARAM_LABEL)) {
    const w = white[key];
    const b = black[key];
    if (w === undefined || b === undefined || Math.abs(w - b) < 1e-9) continue;
    const tr = document.createElement("tr");
    const th = document.createElement("td");
    th.textContent = name;
    tr.append(th);
    for (const seat of ["white", "black"] as const) {
      const td = document.createElement("td");
      const v = seat === "white" ? w : b;
      const other = seat === "white" ? b : w;
      td.textContent = String(Math.round(v * 1000) / 1000);
      // Piece size is not better or worse in a direction, so it gets no colour.
      if (good !== 0) {
        const advantaged = good === 1 ? v > other : v < other;
        td.classList.add(advantaged ? "better" : "worse");
      }
      tr.append(td);
    }
    table.append(tr);
  }
  box.append(table);
}

// --- create / find / who is online ------------------------------------------

const WHO_NOTE: Record<string, string> = {
  open: "Listed under Find a game, and quick match can drop somebody in.",
  code: "Reachable only by the four-letter code, which you share yourself.",
  solo: "Both sides are yours. Nobody can join, and you move either colour.",
};

function setWho(who: "open" | "code" | "solo"): void {
  whoCanJoin = who;
  for (const b of document.querySelectorAll<HTMLElement>("#who-bar button")) {
    b.classList.toggle("on", b.dataset.who === who);
  }
  $("who-note").textContent = WHO_NOTE[who];
  // A room only you can enter has no second side to balance against.
  const balBox = $("bal-on") as HTMLInputElement;
  balBox.disabled = who === "solo";
  if (who === "solo" && balBox.checked) {
    balBox.checked = false;
    setBalanced(false);
  }
}

function setBalanced(on: boolean): void {
  balanced = on;
  $("param-grid").classList.toggle("balanced", on);
  ($("create").querySelector(".bal-legend") as HTMLElement).hidden = !on;
  // Turning it on starts both sides equal, so the first edit is the handicap
  // rather than whatever black's boxes happened to be left at.
  if (on) copyColumn("white", "black");
}

/** Write one seat's boxes into the other's. */
function copyColumn(from: "white" | "black", to: "white" | "black"): void {
  const src = readParams(from);
  const sel = `[data-param][data-seat="${to}"]`;
  for (const input of document.querySelectorAll<HTMLInputElement>(sel)) {
    const value = src[input.dataset.param!];
    if (value !== undefined) input.value = String(value);
  }
}

function showCreate(): void {
  lobby.style.display = "none";
  findEl.style.display = "none";
  createEl.style.display = "block";
  createEl.scrollTop = 0;
  $("create-status").textContent = "";
}

function showFind(): void {
  lobby.style.display = "none";
  createEl.style.display = "none";
  findEl.style.display = "block";
  findEl.scrollTop = 0;
  $("find-status").textContent = "Loading...";
  void refreshRooms();
}

/** Back to the menu from a screen that has not opened a room. */
function backToLobby(): void {
  createEl.style.display = "none";
  findEl.style.display = "none";
  lobby.style.display = "flex";
}

async function refreshRooms(): Promise<void> {
  if (!(await ensureConnected())) {
    $("find-status").textContent = "cannot reach the server";
    return;
  }
  net.listRooms();
}

const TEMPO_LABEL: Record<string, string> = {
  bullet: "Bullet", rapid: "Rapid", slow: "Slow", custom: "Custom",
};

function renderRooms(rooms: OpenRoom[]): void {
  const list = $("find-list");
  list.textContent = "";
  $("find-status").textContent = "";
  if (rooms.length === 0) {
    $("find-status").textContent =
      "Nobody is waiting right now. Create a game and yours will be listed here.";
    return;
  }
  for (const room of rooms) {
    const row = document.createElement("div");
    row.className = "find-row";

    const left = document.createElement("div");
    left.className = "grow";
    const title = document.createElement("div");
    // Built from nodes: a host name is another player's text.
    const code = document.createElement("span");
    code.className = "code";
    code.textContent = room.code;
    title.append(code);
    if (room.host) {
      const host = document.createElement("span");
      host.textContent = `  ${room.host}`;
      title.append(host);
    }
    left.append(title);

    const meta = document.createElement("div");
    meta.className = "meta";
    const tempo = document.createElement("span");
    tempo.className = "tag";
    tempo.textContent = TEMPO_LABEL[room.tempo] ?? room.tempo;
    meta.append(tempo);
    if (room.balanced) {
      const bal = document.createElement("span");
      bal.className = "tag bal";
      bal.textContent = "Balanced";
      meta.append(bal);
    }
    // A room the host marked unrated, or one whose settings rule it out.
    // Worth a tag: it is the difference between a game that counts and one
    // that does not, and it is decided before you walk in.
    if (room.rated === false) {
      const un = document.createElement("span");
      un.className = "tag";
      un.textContent = "Unrated";
      meta.append(un);
    }
    const odd = Object.entries(room.view ?? {})
      .filter(([k, v]) => v !== VIEW_DEFAULTS[k as keyof View]).length;
    if (odd > 0) {
      const v = document.createElement("span");
      v.className = "tag";
      v.textContent = "Custom visibility";
      meta.append(v);
    }
    meta.append(` waiting ${duration(room.waiting)}`);
    left.append(meta);
    row.append(left);

    const join = document.createElement("button");
    join.textContent = "Join";
    join.addEventListener("click", async () => {
      if (await ensureConnected()) net.joinRoom(room.code);
    });
    row.append(join);
    list.append(row);
  }
}

function renderOnline(msg: { count: number; signed_in: number;
                            users: { id: string; name: string }[] }): void {
  const people = msg.users ?? [];
  $("online-count").textContent = people.length === 0
    ? `${msg.count} connected, nobody signed in.`
    : `${msg.count} connected, ${msg.signed_in} signed in.`;
  const list = $("online-list");
  list.textContent = "";
  for (const person of people) {
    const b = document.createElement("button");
    b.textContent = person.name;           // never innerHTML: another player's text
    b.addEventListener("click", () => openPlayerCard(person.id, person.name));
    list.append(b);
  }
}

async function refreshOnline(): Promise<void> {
  if (!net || net.isClosed()) return;   // no socket is not worth opening one for
  net.listOnline();
}

// --- one player's public card ------------------------------------------------

function openPlayerCard(id: string, name: string): void {
  cardId = id;
  cardGames = [];
  cardOffset = 0;
  cardTotal = 0;
  $("pc-name").textContent = name;
  $("pc-ratings").textContent = "";
  $("pc-list").textContent = "";
  ($("pc-pager") as HTMLElement).hidden = true;
  $("pc-note").textContent = "Loading...";
  ($("player-card") as HTMLElement).hidden = false;
  if (net && !net.isClosed()) net.getProfile({ id });
}

/** Fetch another page of the open card's games. */
function pageCard(offset: number): void {
  if (!cardId || !net || net.isClosed()) return;
  cardOffset = offset;
  net.getProfile({ id: cardId, offset });
}

/**
 * Another player's card: their ratings, and their games newest first.
 *
 * The games are theirs, so the rows are drawn from their seat - "as white vs
 * you" reads correctly whoever is looking. Their replays are watchable: a
 * finished game is a public record, and looking through an opponent's is how
 * you learn anything about them.
 */
function renderPlayerCard(profile: PublicProfile): void {
  cardId = profile.id;
  cardGames = profile.games ?? [];
  cardOffset = profile.offset ?? 0;
  cardTotal = profile.total ?? cardGames.length;
  $("pc-name").textContent = profile.name;
  const box = $("pc-ratings");
  box.textContent = "";
  let played = 0;
  for (const mode of MODES) {
    const r = profile.ratings?.[mode];
    if (r) played += r.games;
    box.append(ratingTile(mode, r));
  }
  $("pc-note").textContent =
    (played === 0 ? "No rated games yet."
                  : `${played} rated ${played === 1 ? "game" : "games"}.`) +
    (cardTotal ? ` ${cardTotal} game${cardTotal === 1 ? "" : "s"} stored.` : "");

  const list = $("pc-list");
  list.textContent = "";
  for (const g of cardGames) list.append(storedRow(g));
  renderPager("pc", cardOffset, cardTotal, pageCard);
}

/**
 * Who is playing what, and which pieces their civilization singles out. Both
 * civs are in every snapshot: the pick is secret only until the game starts.
 *
 * Built from the same table the board reads for its markers, so the dot on a
 * piece and the line naming it can never disagree.
 */
let legendFor = "";

/** One "▲ what   +10%" line. */
function effectRow(text: string, amount: string, good: boolean): HTMLElement {
  const row = document.createElement("div");
  row.className = `lg-eff ${good ? "good" : "bad"}`;
  const what = document.createElement("span");
  what.textContent = text;
  const amt = document.createElement("span");
  amt.className = "amt";
  amt.textContent = amount;
  row.append(what, amt);
  return row;
}

/** "Roman · The Legion", built from nodes - a civ name reached innerHTML
 *  once and was stored XSS. The seat is the panel's own heading now. */
function civHeading(civ: string | null): HTMLElement {
  const head = document.createElement("div");
  const b = document.createElement("b");
  b.textContent = `${civName(civ)} \u00b7 ${TITLE[civ ?? "none"]}`;
  head.append(b);
  return head;
}

/** Which colour is at each end of the board as it is currently drawn. */
function endColor(end: "near" | "far"): "white" | "black" {
  const near = renderer?.flipped ? "black" : "white";
  return end === "near" ? near : near === "white" ? "black" : "white";
}

/** The four panel ids, in the order they are built. */
const LEGEND_PANELS = ["civ-econ-near", "civ-pieces-near",
                       "civ-econ-far", "civ-pieces-far"];

/** One panel: what kind of effects, whose they are, and the list. */
function fillLegend(panel: HTMLElement, title: string, seat: "white" | "black",
                    civ: string | null,
                    rows: readonly (readonly [string, string, boolean])[],
                    empty: string): void {
  panel.textContent = "";
  const h = document.createElement("div");
  h.className = "lg-title";
  h.textContent = title;
  panel.append(h);
  const owner = document.createElement("div");
  owner.className = "lg-owner";
  owner.textContent = COLOR_LABEL[seat] +
    (!soloRoom && seat === net.color ? " (you)" : "");
  panel.append(owner);

  const side = document.createElement("div");
  side.className = "lg-side";
  side.append(civHeading(civ));
  if (rows.length === 0) {
    const none = document.createElement("div");
    none.className = "lg-none";
    none.textContent = empty;
    side.append(none);
  }
  for (const [what, amount, good] of rows) side.append(effectRow(what, amount, good));
  panel.append(side);
  panel.style.display = "block";
}

/**
 * Who is playing what, in four boxes rather than two lists of two.
 *
 * Left and right say what a box is about (the economy, particular pieces);
 * near and far say whose it is, following the board's own orientation. So the
 * pair at your end is always yours, whether you are white, black, or have
 * pressed f to flip the board - and reading "is that mine or theirs" off a
 * heading stops being part of playing.
 *
 * Both civs are in every snapshot: the pick is secret only until the game
 * starts. Everything here is read off the same table the board marks pieces
 * from, so a dot and the line naming it cannot disagree.
 */
function showCivLegend(civs: Record<string, string | null> | undefined): void {
  // The orientation is part of the key: flipping the board swaps which end is
  // which, and the panels have to follow it.
  const key = JSON.stringify(civs ?? {}) + (renderer?.flipped ? "|f" : "");
  if (key === legendFor) return;
  legendFor = key;

  for (const end of ["near", "far"] as const) {
    const seat = endColor(end);
    const civ = civs?.[seat] ?? null;
    fillLegend($(`civ-econ-${end}`), "Economy", seat, civ,
               globalEffects(civ).map((e) => [e.what, e.amount, e.good] as const),
               "no modifiers");
    const perPiece: (readonly [string, string, boolean])[] = [];
    for (const [piece, effects] of Object.entries(pieceEffects(civ))) {
      for (const e of effects) {
        perPiece.push([`${PLURAL[piece] ?? piece} ${e.what}`, e.amount, e.good]);
      }
    }
    fillLegend($(`civ-pieces-${end}`), "Pieces", seat, civ, perPiece,
               civ ? "no piece singled out" : "no modifiers");
  }
}

/** All four civ panels off, when a game ends or the room is left. */
function hideCivLegend(): void {
  for (const id of LEGEND_PANELS) $(id).style.display = "none";
  legendFor = "";
}

/**
 * Who is playing, on a plate at the end of the board they play from. Names and
 * ratings come from ROOM_STATE, which is the server's account of the seats, so
 * an anonymous player is named as one rather than left blank.
 */
function renderPlates(): void {
  const live = gameEl.style.display === "block" && !player;
  for (const end of ["near", "far"] as const) {
    const el = $(`plate-${end}`);
    el.style.display = live ? "block" : "none";
    if (!live) continue;
    const color = endColor(end);
    const card = roomSeats?.[color] ?? null;
    el.textContent = "";
    const side = document.createElement("span");
    side.className = "side";
    side.textContent = `${COLOR_LABEL[color]}  `;
    el.append(side);
    const name = document.createElement("span");
    // Another player's text: a node, never markup.
    name.textContent = card?.name ?? "Anonymous";
    if (!soloRoom && color === net.color) name.className = "you";
    el.append(name);
    if (card?.rating) {
      const rating = document.createElement("span");
      rating.className = "rating";
      rating.textContent = `  ${Math.round(card.rating.rating)}`;
      el.append(rating);
    }
  }
}

/**
 * Whether a rematch has been asked for, and by whom. Both players have to
 * press it, so the one who pressed first needs to be told they are waiting
 * rather than left looking at a dead button.
 */
function showRematchState(): void {
  const btn = $("btn-rematch") as HTMLButtonElement;
  const line = $("pg-rematch");
  const me = (net.color ?? "white") as "white" | "black";
  const them = me === "white" ? "black" : "white";
  const mine = soloRoom || !!roomSeats?.[me]?.rematch;
  const theirs = soloRoom || !!roomSeats?.[them]?.rematch;
  if (mine && !theirs) {
    line.textContent = "Rematch asked for. Waiting for your opponent.";
    btn.textContent = "Rematch";
    btn.disabled = true;
  } else if (theirs && !mine) {
    line.textContent = "Your opponent wants a rematch.";
    btn.textContent = "Accept rematch";
    btn.disabled = false;
  } else {
    line.textContent = "";
    btn.textContent = "Rematch";
    btn.disabled = false;
  }
}

/**
 * Back to the pre-game screen without leaving the room: a rematch. Everything
 * the finished game owned has to go, or its board, its replay and its legend
 * survive into the next one - the same list `exitToLobby` clears, minus what
 * belongs to the room itself (the seat, the tempo, whether it is solo).
 */
function returnToPregame(code: string): void {
  state = null;
  selectedId = null;
  dragId = null;
  dragPos = null;
  player = null;
  recording = new Recording();
  matchLogged = false;
  lastMatchId = null;
  stopLeftCountdown();
  showBanner("");
  setPrecise(preciseLatched = false);
  postgame.style.display = "none";
  replayBar.style.display = "none";
  hideCivLegend();
  gameEl.style.display = "none";
  renderPlates();
  showPregame(code);
}

function enterGame(): void {
  lobby.style.display = "none";
  pregame.style.display = "none";
  gameEl.style.display = "block";
  gameBar.style.display = "flex";
  matchLogged = false;
  lastMatchId = null;
  // A rating line left over from the previous game would sit under this one's
  // result until the server replaced it, which for an unrated game is never.
  lastRating = null;
  // The previous game's stored copy is not this one's.
  lastGameId = null;
  pendingReplay = null;
  resetReplayButton();
  $("pg-rating").textContent = "";
  // Nobody to resign to when both sides are yours: exiting is the way out.
  $("btn-resign").style.display = soloRoom ? "none" : "";
  renderer = new Renderer(canvas, net.color);
  renderer.hints = hintMode();
  renderer.resize();
  hideCivLegend();
  $("pg-rematch").textContent = "";
  ($("btn-rematch") as HTMLButtonElement).textContent = "Rematch";
  // Who is playing. The seats were settled before the game started, so this
  // is drawn once here and again only if the board is flipped.
  renderPlates();
}

// --- input ------------------------------------------------------------------

function playable(): boolean {
  return !player && !!state && !state.game_over && state.countdown === null;
}

/**
 * Precise mode: a deliberate 0.2-square move is otherwise unreachable, because
 * a drag that short reads as a click. While it is on, any drag counts and a
 * click near a friendly piece is a move rather than a change of selection.
 * Held on the keyboard, latched by the on-screen button for touch.
 */
function setPrecise(on: boolean): void {
  precise = on;
  $("btn-precise").classList.toggle("on", on);
  if (renderer) renderer.hints = hintMode();
}

function hintMode(): "off" | "on" | "strong" {
  if (!settings.showHints) return "off";
  return precise ? "strong" : "on";
}

/** Try to move `sel` to the click; true if the move was sent. */
function tryMove(sel: Piece, bx: number, by: number): boolean {
  const freedom = forPiece(state!, sel, "movement_freedom_deg", state!.freedom_deg, 5.0);
  const snap = snapDestination(bx, by, sel, freedom, state!.pieces);
  if (!Number.isFinite(snap.d) || snap.d > MOVE_SNAP_MAX) return false;
  net.queueMove(sel.id, snap.x, snap.y);
  return true;
}

function onPointerDown(ev: PointerEvent): void {
  if (!playable()) return;
  const [bx, by] = renderer.pxToBoard(ev.clientX, ev.clientY);
  // A press off the board puts the piece down. The canvas is wider and taller
  // than the eight squares, so there is always a margin to aim at, and it is
  // the gesture people reach for when they change their mind.
  if (!(bx >= 0 && bx < 8 && by >= 0 && by < 8)) {
    selectedId = null;
    return;
  }

  const pieces = state!.pieces;
  const mine = (p: Piece) => soloRoom || net.color === null || p.owner === net.color;

  // Picking up an own idle piece starts a drag; the same press also selects it,
  // so releasing in place leaves the click-click flow intact.
  const grabbed = findPieceAt(bx, by, pieces, CLICK_R_SELECT);
  if (grabbed && grabbed.state === "idle" && mine(grabbed)) {
    if (selectedId !== null && selectedId !== grabbed.id) {
      const sel = pieces.find((p) => p.id === selectedId);
      // A close click on a friendly piece switches rather than moves - unless
      // precise mode is on, where landing next to a piece is the whole point.
      const onPiece = !precise && findPieceAt(bx, by, pieces, CLICK_R_SWITCH);
      if (sel && !onPiece && tryMove(sel, bx, by)) {
        selectedId = null;
        return;
      }
    }
    dragWasSelected = selectedId === grabbed.id;
    if (settings.moveMode !== "click") {
      dragId = grabbed.id;
      dragPos = [ev.clientX, ev.clientY];
      canvas.setPointerCapture(ev.pointerId);
    }
    selectedId = grabbed.id;
    return;
  }
  if (settings.moveMode === "drag") {
    selectedId = null; // click-to-move is off: a click on empty board clears
    return;
  }

  if (selectedId !== null) {
    const sel = pieces.find((p) => p.id === selectedId);
    if (sel && tryMove(sel, bx, by)) selectedId = null;
  }
}

function onPointerMove(ev: PointerEvent): void {
  if (dragId !== null) dragPos = [ev.clientX, ev.clientY];
}

function onPointerUp(ev: PointerEvent): void {
  if (dragId === null || !state) return;
  const id = dragId;
  dragId = null;
  dragPos = null;

  const sel = state.pieces.find((p) => p.id === id);
  if (!sel) {
    selectedId = null;
    return;
  }
  const [bx, by] = renderer.pxToBoard(ev.clientX, ev.clientY);
  const threshold = precise ? PRECISE_MIN_DRAG : settings.dragThreshold;
  const movedFar = Math.hypot(bx - sel.x, by - sel.y) > threshold;

  if (!movedFar) {
    // Released where it started: a plain click. Toggles the selection.
    // With click-to-move off, the piece stays selected so the next drag is
    // not swallowed by an accidental deselect.
    if (dragWasSelected && settings.moveMode !== "drag") selectedId = null;
    return;
  }
  if (bx >= 0 && bx < 8 && by >= 0 && by < 8 && tryMove(sel, bx, by)) {
    selectedId = null;
  }
  // Otherwise keep it selected so the click-click flow can still be used.
}

// --- post-game and replay ---------------------------------------------------

function showPostgame(result: string): void {
  $("pg-result").textContent = result;
  $("btn-resign").style.display = "none";
  // Solo rematches too: it owns both seats, so it agrees with itself.
  ($("btn-rematch") as HTMLButtonElement).disabled = false;
  $("pg-rematch").textContent = "";
  if (player) return; // a late final frame must not cover the replay

  postgame.style.display = "block";
}

function matchHistory(): Match[] {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]");
  } catch {
    return []; // corrupt or cleared: an empty history is the right fallback
  }
}

/** Which preset a room's parameters are, so a joiner labels its tempo too. */
function tempoName(p: Record<string, number> | null): string {
  if (!p) return tempo;
  for (const mode of ["bullet", "rapid", "slow"]) {
    const preset = presetParams(mode)!;
    if (Object.entries(preset).every(([k, v]) => Math.abs((p[k] ?? NaN) - v) < 1e-6)) {
      return mode;
    }
  }
  return "custom";
}

function logMatch(final: GameState): void {
  if (matchLogged || !recording.frames.length) return;
  matchLogged = true;
  const id = `m${Date.now().toString(36)}`;
  lastMatchId = id;
  recordings.set(id, recording);
  const row: Match = {
    id,
    // Usually still null here: this runs on the final frame and the server
    // stores the game a moment later, so GAME_SAVED fills it in. Read anyway,
    // for the case where the save landed first.
    gameId: lastGameId,
    at: Date.now(),
    tempo: tempoName(baseParams),
    civs: (final.civs ?? {}) as Record<string, string | null>,
    seat: soloRoom ? null : net.color,
    // A game left early has no winner. Keeping the row anyway is the point:
    // the recording is attached to it, and that is what the profile offers.
    winner: final.game_over ? (final.winner ?? "draw") : "unfinished",
    seconds: Math.round(recording.duration / 1000),
    solo: soloRoom,
  };
  localStorage.setItem(HISTORY_KEY,
                       JSON.stringify([row, ...matchHistory()].slice(0, MAX_HISTORY)));
}

/** Write the server's id onto a history row already stored. */
function attachGameId(matchId: string, gameId: string): void {
  const all = matchHistory();
  const row = all.find((m) => m.id === matchId);
  if (!row || row.gameId) return;
  row.gameId = gameId;
  localStorage.setItem(HISTORY_KEY, JSON.stringify(all));
}

/** The three rated tempos, in the order they are always shown. */
const MODES = ["bullet", "rapid", "slow"] as const;

/**
 * One rating tile. Shared by your own profile and another player's card, so
 * the two cannot drift into showing the same number differently.
 */
function ratingTile(mode: string,
                    r: { rating: number; games: number } | undefined): HTMLElement {
  const tile = document.createElement("div");
  tile.className = r ? "rating" : "rating unplayed";
  const label = document.createElement("span");
  label.className = "mode";
  label.textContent = mode;
  const value = document.createElement("span");
  value.className = "value";
  value.textContent = r ? String(Math.round(r.rating)) : "unrated";
  tile.append(label, value);
  if (r) {
    const games = document.createElement("span");
    games.className = "games";
    games.textContent = ` ${r.games} game${r.games === 1 ? "" : "s"}`;
    tile.append(games);
  }
  return tile;
}


/** Fill in the account panels from whatever the server last told us. */
function renderAccount(): void {
  const who = account.identity;
  // Whether you are signed in decides whether a game counts, so it is said in
  // the bar that is on every screen, not only on the tab about it.
  const whoami = $("whoami");
  whoami.textContent = who ? who.name : "Not signed in";
  whoami.classList.toggle("in", !!who);
  whoami.title = who ? "Signed in. Opens your profile."
                     : "Not signed in. Games are not rated. Opens the profile tab.";
  ($("pf-signed-out") as HTMLElement).hidden = !!who;
  ($("pf-signed-in") as HTMLElement).hidden = !who;
  if (!who) return;

  $("pf-name").textContent = `Signed in as ${who.name}`;
  const box = $("pf-ratings");
  box.textContent = "";
  // Every mode is shown, including unplayed ones: a missing tile reads as a
  // bug, while "unrated" reads as an invitation.
  for (const mode of MODES) box.append(ratingTile(mode, who.ratings?.[mode]));
}

function setAuthBusy(busy: boolean): void {
  for (const id of ["btn-signin", "btn-signup"]) {
    ($(id) as HTMLButtonElement).disabled = busy;
  }
}

function submitAuth(kind: "in" | "up"): void {
  const name = ($("ac-name") as HTMLInputElement).value.trim();
  const pass = ($("ac-pass") as HTMLInputElement).value;
  $("ac-status").classList.remove("error");
  $("ac-status").textContent = "";
  if (!name || !pass) {
    $("ac-status").textContent = "Enter a name and a password.";
    $("ac-status").classList.add("error");
    return;
  }
  setAuthBusy(true);
  void (async () => {
    if (!(await ensureConnected())) {
      setAuthBusy(false);
      return;
    }
    const send = (m: object) => net.send(m);
    if (kind === "in") account.signIn(send, name, pass);
    else account.signUp(send, name, pass);
  })();
}

/**
 * Ask the server for a stored game, and show it as a replay when it arrives.
 *
 * Connecting first is the whole reason this exists. The socket is opened
 * lazily and boots itself only when a token is stored, so a signed-out player
 * who reloaded had no connection at all: the button sent nothing, and nothing
 * said so. A signed-in client is already connected, which is why the stored
 * list never showed this.
 */
function openStoredGame(id: string, seat: string | null,
                        from: "postgame" | "profile" | "card"): void {
  void (async () => {
    if (!(await ensureConnected())) {
      // Same two endings the server's own refusal has: the game just finished
      // is still in memory, and anywhere else the message belongs on whichever
      // of the two lists the button was in.
      resetReplayButton();
      if (from === "postgame") {
        watchReplay(recording, seat, "postgame");
        return;
      }
      const card = $("player-card") as HTMLElement;
      $(card.hidden ? "pf-summary" : "pc-note").textContent =
        "Could not reach the server to fetch that replay.";
      return;
    }
    pendingReplay = { from, seat };
    net.send({ type: P.GET_GAME, id });
  })();
}

/** One row of the match list, built from nodes rather than markup. */
function matchRow(opts: {
  outcome: string; cls: string; detail: string; when: string;
  rating: { before: number; after: number } | null;
  onWatch: (() => void) | null; missing: string;
}): HTMLElement {
  const row = document.createElement("div");
  row.className = "match";
  const what = document.createElement("span");
  what.className = "what";
  const result = document.createElement("span");
  result.className = `result ${opts.cls}`;
  result.textContent = opts.outcome;
  what.append(result, ` ${opts.detail}`);
  if (opts.rating) {
    // The number people actually look for. Signed, because "1216" alone does
    // not say whether the game was worth playing.
    const delta = Math.round(opts.rating.after) - Math.round(opts.rating.before);
    const el = document.createElement("span");
    el.className = `delta ${delta >= 0 ? "up" : "down"}`;
    el.textContent = `${delta >= 0 ? "+" : ""}${delta}`;
    what.append(el);
  }
  const when = document.createElement("span");
  when.className = "when";
  when.textContent = opts.when;
  what.append(when);
  row.append(what);
  if (opts.onWatch) {
    const btn = document.createElement("button");
    btn.textContent = "Watch replay";
    btn.addEventListener("click", opts.onWatch);
    row.append(btn);
  } else if (opts.missing) {
    const gone = document.createElement("span");
    gone.className = "gone";
    gone.textContent = opts.missing;
    row.append(gone);
  }
  return row;
}

const duration = (seconds: number) => {
  const mins = Math.floor(seconds / 60);
  return `${mins ? `${mins}m ` : ""}${seconds % 60}s`;
};

function renderProfile(): void {
  renderAccount();
  const list = $("pf-list");
  list.textContent = "";
  $("pf-storage-note").textContent = account.identity
    ? "Your games are kept on the server and replay from anywhere you sign in."
    : "Every game is recorded on the server, including yours. Without an " +
      "account this browser is the only thing that remembers which ones were " +
      "yours, so clearing this list is how you lose them.";
  ($("btn-clear-history") as HTMLElement).hidden = !!account.identity;

  if (account.identity) {
    renderStored(list);
    return;
  }
  renderLocal(list);
}

/**
 * One page of a pager: which rows these are, and the two buttons.
 *
 * `go` is assigned rather than added as a listener, because this runs again on
 * every page and a second listener on the same button would page twice.
 */
function renderPager(prefix: string, offset: number, total: number,
                     go: (offset: number) => void): void {
  ($(`${prefix}-pager`) as HTMLElement).hidden = total <= PAGE;
  const prev = $(`${prefix}-prev`) as HTMLButtonElement;
  const next = $(`${prefix}-next`) as HTMLButtonElement;
  const shown = Math.min(PAGE, Math.max(0, total - offset));
  $(`${prefix}-range`).textContent =
    `${total === 0 ? 0 : offset + 1}\u2013${offset + shown} of ${total}`;
  prev.disabled = offset <= 0;
  next.disabled = offset + PAGE >= total;
  prev.onclick = () => go(Math.max(0, offset - PAGE));
  next.onclick = () => go(offset + PAGE);
}

/** A rating as it stood before the game: "(1215)", or nothing if there was none. */
const ratingTag = (r: number | null) => (r === null ? "" : ` (${Math.round(r)})`);

/**
 * One stored game as a row, from the point of view of whoever's page it is.
 * `seat` is that player's side, so the same function draws your own history
 * and somebody else's card without either one needing its own copy.
 */
function storedRow(g: StoredGame): HTMLElement {
  const seat = g.seat as "white" | "black";
  const them = seat === "white" ? "black" : "white";
  const me: GameSide = g.players[seat];
  const other: GameSide = g.players[them];
  const outcome = g.winner === "draw" ? "Draw"
                : g.winner === seat ? "Won" : "Lost";
  const cls = g.winner === "draw" ? "" : g.winner === seat ? "win" : "loss";
  return matchRow({
    outcome,
    cls,
    // Both ratings as they stood before the game: a result between two
    // numbers means something, and a result on its own does not.
    detail: `as ${seat}${ratingTag(me.rating_before)} vs ` +
            `${other.name ?? "an anonymous player"}${ratingTag(other.rating_before)}, ` +
            `${civName(me.civ)} vs ${civName(other.civ)}`,
    when: `${g.tempo}${g.rated ? ", rated" : ""}, ` +
          `${duration(Math.round(g.ticks / 20))}, ` +
          `${new Date(g.at * 1000).toLocaleString()}`,
    rating: me.rating_before !== null && me.rating_after !== null
      ? { before: me.rating_before, after: me.rating_after } : null,
    onWatch: () => net.send({ type: P.GET_GAME, id: g.id }),
    missing: "",
  });
}

/** Games the server holds for this account, one page at a time. */
function renderStored(list: HTMLElement): void {
  const rows = storedGames;
  const won = rows.filter((g) => g.winner === g.seat).length;
  const drawn = rows.filter((g) => g.winner === "draw").length;
  // The totals are for the page, and say so: counting wins across pages would
  // mean fetching every game to draw one sentence.
  $("pf-summary").textContent = storedTotal
    ? `${storedTotal} game${storedTotal === 1 ? "" : "s"} in all. ` +
      `On this page: ${won} won, ${rows.length - won - drawn} lost, ${drawn} drawn.`
    : "No games yet. Sign in on any device to see them here.";

  for (const g of rows) list.append(storedRow(g));
  renderPager("pf", storedOffset, storedTotal, (offset) => net.listGames(offset));
}

/** Games this browser remembers, for players without an account. */
function renderLocal(list: HTMLElement): void {
  const all = matchHistory();
  const rows = all.slice(localOffset, localOffset + PAGE);
  const played = all.filter((m) => !m.solo && m.winner !== "unfinished");
  const won = played.filter((m) => m.winner === m.seat).length;
  const drawn = played.filter((m) => m.winner === "draw").length;
  const rest = all.length - played.length;
  $("pf-summary").textContent = all.length
    ? `${all.length} game${all.length === 1 ? "" : "s"}: ` +
      `${won} won, ${played.length - won - drawn} lost, ${drawn} drawn` +
      `${rest ? `, ${rest} solo or unfinished` : ""}.`
    : "No games yet.";

  for (const m of rows) {
    const outcome =
      m.winner === "unfinished" ? "Unfinished"
      : m.solo ? (m.winner === "draw" ? "Draw"
                                      : `${COLOR_LABEL[m.winner] ?? m.winner} wins`)
      : m.winner === "draw" ? "Draw"
      : m.winner === m.seat ? "Won" : "Lost";
    const cls = m.solo || m.winner === "draw" || m.winner === "unfinished"
      ? "" : m.winner === m.seat ? "win" : "loss";
    const sides = m.solo
      ? `${civName(m.civs.white)} vs ${civName(m.civs.black)}`
      : `${civName(m.civs[m.seat ?? "white"])} vs ` +
        `${civName(m.civs[m.seat === "white" ? "black" : "white"])}`;
    const rec = recordings.get(m.id);
    const seat = m.solo ? null : m.seat;
    // The server's copy when there is one, so a row here opens the same
    // replay the post-game button does and still works after a reload. The
    // frames this tab kept are the fallback, for a game that was never stored.
    const gameId = m.gameId;
    const onWatch = gameId
      ? () => openStoredGame(gameId, seat, "profile")
      : rec ? () => watchReplay(rec, seat, "profile") : null;
    list.append(matchRow({
      outcome,
      cls,
      detail: `${m.solo ? "solo" : `as ${m.seat}`}, ${sides}`,
      when: `${m.tempo}, ${duration(m.seconds)}, ` +
            `${new Date(m.at).toLocaleString()}`,
      rating: null,
      onWatch,
      missing: "replay expired",
    }));
  }
  renderPager("pf", localOffset, all.length, (offset) => {
    localOffset = offset;
    renderProfile();
  });
}

/** Rating movement from the game just finished, on the post-game card. */
function showRatingChange(): void {
  const seat = soloRoom ? null : net.color;
  if (!lastRating || !seat) return;
  const mine = lastRating[seat as "white" | "black"];
  const delta = Math.round(mine.after) - Math.round(mine.before);
  $("pg-rating").textContent =
    `${lastRating.tempo} rating ${Math.round(mine.after)} ` +
    `(${delta >= 0 ? "+" : ""}${delta})`;
}

/**
 * Watch a recording, either the game just finished or one picked out of the
 * profile. `from` is where Close goes back to, because those are the only two
 * places a replay can be started from.
 */
function watchReplay(rec: Recording, color: string | null,
                     from: "postgame" | "profile" | "card"): void {
  if (!rec.frames.length) return;
  replayReturn = from;
  // The card is an overlay over the lobby, so it has to come off the screen.
  ($("player-card") as HTMLElement).hidden = true;
  lobby.style.display = "none";
  pregame.style.display = "none";
  gameEl.style.display = "block";
  postgame.style.display = "none";
  gameBar.style.display = "none"; // resigning an old game means nothing
  replayBar.style.display = "flex";
  // The civilizations are already named on the two mana bars, and four panels
  // of percentages around a board that has just given up a strip to the
  // replay bar is more than fits. Watching wants the board and the mana.
  hideCivLegend();
  // Before the renderer is built: the class takes the bar's height off the
  // canvas, and the renderer measures the canvas as it constructs.
  gameEl.classList.add("replaying");
  selectedId = null;
  dragId = null;
  renderer = new Renderer(canvas, color);
  renderer.hints = hintMode();
  player = new Player(rec);
  renderPlates();     // a stored game is not this room; the plates come off
  setSpeed(NORMAL_SPEED);
}

function exitReplay(): void {
  player = null;
  replayBar.style.display = "none";
  // The board gets its strip back, and has to be told to redraw at that size.
  gameEl.classList.remove("replaying");
  renderer?.resize();
  if (replayReturn === "profile" || replayReturn === "card") {
    gameEl.style.display = "none";
    lobby.style.display = "flex";
    // A card is opened from the online list on the Play tab, and it is still
    // filled in: put it back rather than making the player find them again.
    showTab(replayReturn === "card" ? "play" : "profile");
    if (replayReturn === "card") ($("player-card") as HTMLElement).hidden = false;
    return;
  }
  gameBar.style.display = "flex";
  postgame.style.display = "block";
  // Back to the finished game, which is a game screen again: its panels come
  // back with it.
  if (state) showCivLegend(state.civs);
  renderPlates();
}

/** Set the playback speed, and say so on the button that shows it. */
function setSpeed(speed: number): void {
  player?.setSpeed(speed);
  showSpeed();
}

/**
 * The middle button is the tempo and the pause: it reads the speed the replay
 * is running at, and pressing it stops the replay or starts it again. Zero is
 * a speed like any other here, so there is no second piece of state saying
 * whether it is playing.
 */
function showSpeed(): void {
  const btn = $("rp-speed") as HTMLButtonElement;
  const speed = player?.speed ?? 0;
  btn.textContent = `${speed}\u00d7`;
  btn.classList.toggle("paused", speed === 0);
  btn.title = speed === 0 ? "Play" : "Pause";
}

/** One place along SPEEDS. Backwards past zero runs the game in reverse. */
function stepSpeed(by: 1 | -1): void {
  if (!player) return;
  const at = SPEEDS.indexOf(player.speed as (typeof SPEEDS)[number]);
  const from = at === -1 ? SPEEDS.indexOf(NORMAL_SPEED) : at;
  setSpeed(SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, from + by))]);
}

/** The post-game button, after a fetch has finished one way or another. */
function resetReplayButton(): void {
  const btn = $("btn-replay") as HTMLButtonElement;
  btn.disabled = false;
  btn.textContent = "Watch replay";
}

const seek = $("rp-seek") as HTMLInputElement;
let seeking = false;

function updateReplayBar(): void {
  if (!player) return;
  const total = player.recording.duration;
  if (!seeking) seek.value = String(total ? (player.t / total) * 1000 : 0);
  showSpeed();
  $("rp-time").textContent = `${(player.t / 1000).toFixed(1)}s / ${(total / 1000).toFixed(0)}s`;
}

/**
 * Back to the lobby. This used to reload the page, which was the simplest way
 * to a clean slate, but it also threw away the recordings the profile now
 * offers. So the room is left explicitly and everything the game owned is
 * cleared by hand. The socket stays open: the server has let go of the room,
 * so this connection can open another.
 */
function exitToLobby(): void {
  if (state) logMatch(state);
  inRoom = false;
  if (net) net.send({ type: P.LEAVE_ROOM });
  sessionStorage.removeItem("seat");
  history.replaceState(null, "", location.pathname + location.search);

  state = null;
  selectedId = null;
  dragId = null;
  dragPos = null;
  player = null;
  recording = new Recording();
  matchLogged = false;
  lastMatchId = null;
  lastRating = null;
  $("pg-rating").textContent = "";
  soloRoom = false;
  baseParams = null;
  lastGameId = null;
  pendingReplay = null;
  resetReplayButton();
  roomSeats = null;
  roomRated = { rated: true, reason: null };
  renderPlates();
  rejoining = false;
  rejoinAttempts = 0;
  if (retryTimer !== null) {
    window.clearTimeout(retryTimer);
    retryTimer = null;
  }
  stopLeftCountdown();
  showBanner("");
  setPrecise(preciseLatched = false);

  postgame.style.display = "none";
  replayBar.style.display = "none";
  hideCivLegend();
  gameBar.style.display = "flex";
  $("btn-resign").style.display = "";
  ($("rr-me") as HTMLButtonElement).disabled = false;
  $("pg-status").textContent = "";
  gameEl.style.display = "none";
  pregame.style.display = "none";
  createEl.style.display = "none";
  findEl.style.display = "none";
  ($("player-card") as HTMLElement).hidden = true;
  lobby.style.display = "flex";
  setStatus("");
  showTab("play");
}

/** Show one lobby tab. The others are hidden, not unbuilt. */
function showTab(name: string): void {
  for (const el of document.querySelectorAll<HTMLElement>(".tab")) {
    el.hidden = el.id !== `tab-${name}`;
  }
  for (const b of document.querySelectorAll<HTMLElement>("#tabs button")) {
    b.classList.toggle("on", b.dataset.tab === name);
  }
  // One scroll container behind all four tabs, so a new tab would otherwise
  // open at wherever the last one was left.
  $("lobby-body").scrollTop = 0;
  if (name === "play") void refreshOnline();
  if (name === "profile") {
    // Opening the profile with a token but no socket yet (the connect at boot
    // can still be in flight, or have failed) must not show "signed out".
    if (account.token() && !account.identity) void ensureConnected();
    renderProfile();
  }
}

/** True while a real game is running that leaving would abandon. */
function gameInProgress(): boolean {
  return !!state && !state.game_over && !soloRoom;
}

/**
 * Give up. Sending is not enough on its own: a reload can close the socket
 * before the frame leaves, so the exit waits a moment for it. Failing that,
 * the disconnect grace window ends the game anyway, just 30 seconds later.
 */
function resign(): void {
  net.send({ type: P.RESIGN });
}

/**
 * A second press before acting, with the question on the button itself. A
 * confirm() dialog would block the page, and resigning by misclick is exactly
 * the thing worth one extra press.
 */
function armed(btn: HTMLButtonElement, question: string,
               live: () => boolean, action: () => void): void {
  const label = btn.textContent!;
  let timer: number | null = null;
  const reset = () => {
    btn.textContent = label;
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  };
  btn.addEventListener("click", () => {
    if (live() && timer === null) {
      btn.textContent = question;
      timer = window.setTimeout(reset, 4000);
      return;
    }
    reset();
    action();
  });
}

// --- render loop ------------------------------------------------------------

function frame(): void {
  const now = performance.now();
  if (player && renderer) {
    const at = player.tick(now);
    if (at) renderer.render(interpolate(at.state, at.age), null, 0);
    updateReplayBar();
  } else if (state && renderer) {
    renderer.render(interpolate(state, now - stateAt), selectedId,
                    net.rtt, dragId, dragPos);
  }
  requestAnimationFrame(frame);
}

// --- wiring -----------------------------------------------------------------

$("btn-create").addEventListener("click", showCreate);
$("btn-find").addEventListener("click", showFind);
$("btn-create-back").addEventListener("click", backToLobby);
$("btn-find-back").addEventListener("click", backToLobby);
$("btn-find-refresh").addEventListener("click", () => {
  $("find-status").textContent = "Loading...";
  void refreshRooms();
});
for (const b of document.querySelectorAll<HTMLElement>("#who-bar button")) {
  b.addEventListener("click", () => setWho(b.dataset.who as "open" | "code" | "solo"));
}
($("bal-on") as HTMLInputElement).addEventListener("change", (ev) => {
  setBalanced((ev.target as HTMLInputElement).checked);
});
$("btn-create-go").addEventListener("click", async () => {
  $("create-status").textContent = "";
  if (!(await ensureConnected())) {
    $("create-status").textContent = "cannot reach the server";
    return;
  }
  net.createRoom(readParams("white"), whoCanJoin === "solo", readView(),
                 whoCanJoin === "open", readBlackParams(),
                 ($("unrated-on") as HTMLInputElement).checked);
});
$("pc-close").addEventListener("click", () => {
  ($("player-card") as HTMLElement).hidden = true;
});
$("btn-join").addEventListener("click", async () => {
  const code = ($("room-code") as HTMLInputElement).value.trim();
  if (!code) return setStatus("enter a room code", true);
  if (await ensureConnected()) net.joinRoom(code);
});
// One button per tempo. Quick match used to play under whatever the create
// screen had last been left on, which the player had no reason to have looked
// at - so the one thing it decides is now the thing it asks.
for (const b of document.querySelectorAll<HTMLElement>("#quick-bar button")) {
  const mode = b.dataset.tempo!;
  b.title = MODE_NOTE[mode] ?? "";
  b.addEventListener("click", async () => {
    setStatus(`Looking for a ${TEMPO_LABEL[mode] ?? mode} game\u2026`);
    if (await ensureConnected()) net.quickMatch(mode);
  });
}

for (const b of document.querySelectorAll<HTMLElement>("#tempo-bar button")) {
  b.addEventListener("click", () => setTempo(b.dataset.mode!));
}

$("seat-white").addEventListener("click", () => {
  activeSeat = "white";
  refreshPicks();
});
$("seat-black").addEventListener("click", () => {
  activeSeat = "black";
  refreshPicks();
});

$("rr-me").addEventListener("click", () => {
  // One message per seat. The server takes the colour only from a solo room,
  // where this client owns both; anywhere else the seat is the one it dealt.
  // Only the name of the civilization goes over the wire: the server holds the
  // same table and resolves the params against the room's tempo itself, so a
  // patched copy of this page cannot deal itself a shorter cooldown.
  const ready = (seat: "white" | "black") => {
    const civ = picks[seat];
    net.send({ type: P.SET_READY, ready: true, color: seat,
               civ: civ === "none" ? null : civ });
  };
  if (soloRoom) {
    ready("white");
    ready("black");
  } else {
    ready("white");
  }
  // Left enabled: ROOM_STATE turns the cell green when the server agrees, and
  // a press the server rejected has to stay pressable.
});
// Both players press this; the server treats the second press as a no-op
// rather than an error, so nobody is told their rematch failed.
$("btn-rematch").addEventListener("click", () => {
  net.send({ type: P.REMATCH });
  // Said here as well as on the next ROOM_STATE, so the press has an effect
  // before the round trip. The server's answer replaces it either way.
  $("pg-rematch").textContent = "Rematch asked for. Waiting for your opponent.";
  ($("btn-rematch") as HTMLButtonElement).disabled = true;
});
$("btn-pg-exit").addEventListener("click", exitToLobby);
// Editing any field by hand means the settings are no longer a named mode.
for (const input of document.querySelectorAll<HTMLInputElement>("[data-param]")) {
  input.addEventListener("input", () => {
    setTempo("custom", false);
  });
}
// The fields are marked up with the server defaults; start them on the
// selected mode so what is shown is what will be sent.
setTempo(tempo);

for (const b of document.querySelectorAll<HTMLElement>("#tabs button")) {
  b.addEventListener("click", () => showTab(b.dataset.tab!));
}
$("whoami").addEventListener("click", () => showTab("profile"));
// A note under the unrated box rather than inside its label: the label is the
// choice, and the reasons a game may not count are longer than a label.
const unratedBox = $("unrated-on") as HTMLInputElement;
function showRatedNote(): void {
  $("rated-note").textContent = unratedBox.checked
    ? "This room is marked unrated. Nobody's rating moves, whatever else it is set to."
    : "A game is rated only when both players are signed in, the tempo is one " +
      "of the three presets, the two sides have the same settings and the " +
      "visibility is left at its default. The room says which of these it " +
      "fails before anyone readies.";
}
unratedBox.addEventListener("change", showRatedNote);
showRatedNote();
$("btn-signin").addEventListener("click", () => submitAuth("in"));
$("btn-signup").addEventListener("click", () => submitAuth("up"));
// Enter in either field submits a sign-in: the common case by far, and a form
// that only responds to the mouse feels broken.
for (const id of ["ac-name", "ac-pass"]) {
  $(id).addEventListener("keydown", (ev) => {
    if ((ev as KeyboardEvent).key === "Enter") submitAuth("in");
  });
}
$("btn-signout").addEventListener("click", () => {
  account.signOut((m) => net?.send(m));
  storedGames = [];
  ($("ac-pass") as HTMLInputElement).value = "";
  renderAccount();
  renderProfile();
});
$("btn-clear-history").addEventListener("click", () => {
  localStorage.removeItem(HISTORY_KEY);
  recordings.clear();
  renderProfile();
});

$("btn-replay").addEventListener("click", () => {
  const seat = soloRoom ? null : net.color;
  // The stored game rather than what this client kept: with the default
  // settings the live frames have no opponent mana in them and no destinations
  // for their moves, and a replay is not a game - there is nothing left to
  // hide. An account is not needed to ask; falls back to the frames in memory
  // only when there is no stored game, which means no database behind the
  // server.
  if (lastGameId) {
    const btn = $("btn-replay") as HTMLButtonElement;
    btn.disabled = true;
    btn.textContent = "Loading\u2026";
    openStoredGame(lastGameId, seat, "postgame");
    return;
  }
  watchReplay(recording, seat, "postgame");
});
$("btn-newgame").addEventListener("click", exitToLobby);
armed($("btn-resign") as HTMLButtonElement, "Confirm resign",
      gameInProgress, resign);
armed($("btn-exit") as HTMLButtonElement, "Resign and exit?", gameInProgress, () => {
  if (gameInProgress()) {
    resign();
    window.setTimeout(exitToLobby, 200);
  } else {
    exitToLobby();
  }
});
$("rp-exit").addEventListener("click", exitReplay);
$("rp-slower").addEventListener("click", () => stepSpeed(-1));
$("rp-faster").addEventListener("click", () => stepSpeed(1));
$("rp-speed").addEventListener("click", () => {
  player?.toggle();
  showSpeed();
});
seek.addEventListener("pointerdown", () => { seeking = true; });
seek.addEventListener("input", () => {
  if (player) player.seek((parseFloat(seek.value) / 1000) * player.recording.duration);
});
// Released on the window, not on the slider: a touch that ends anywhere else
// on the screen never gives the slider its pointerup, and the flag would stay
// set - after which the bar stops following the replay and looks frozen.
for (const end of ["pointerup", "pointercancel"]) {
  window.addEventListener(end, () => { seeking = false; });
}
seek.addEventListener("change", () => { seeking = false; });

// Precise mode: held on a keyboard, latched by the button for touch, where
// there is no modifier to hold.
$("btn-precise").addEventListener("click", () => {
  preciseLatched = !precise;
  setPrecise(preciseLatched);
});

// Personal settings. They apply immediately. They are also kept on the account
// when there is one signed in, and the account's copy wins on the next sign-in.
setPublisher((values) => net?.send({ type: P.SET_SETTINGS, settings: values }));
const moveModeSel = $("s-movemode") as HTMLSelectElement;
const dragInput = $("s-drag") as HTMLInputElement;
const hintsInput = $("s-hints") as HTMLInputElement;

/**
 * Put the values in force into the controls. Runs at boot and again on every
 * sign-in and sign-out: the profile overrides the device, so this panel can go
 * stale for reasons that have nothing to do with what was last typed into it.
 */
function drawSettings(): void {
  moveModeSel.value = settings.moveMode;
  dragInput.value = String(settings.dragThreshold);
  hintsInput.checked = settings.showHints;
  drawBindings();
  if (renderer) renderer.hints = hintMode();
}
moveModeSel.addEventListener("change", () =>
  saveSettings({ moveMode: moveModeSel.value as typeof settings.moveMode }));
dragInput.addEventListener("change", () => {
  const v = parseFloat(dragInput.value);
  if (Number.isFinite(v) && v >= 0) saveSettings({ dragThreshold: v });
  dragInput.value = String(settings.dragThreshold);
});
hintsInput.addEventListener("change", () => {
  saveSettings({ showHints: hintsInput.checked });
  if (renderer) renderer.hints = hintMode();
});
/**
 * The two key bindings. Each button is both the control and the value it
 * holds: press it, and the next key you press becomes the binding.
 *
 * The capture listener is on the window in the capture phase, so the keystroke
 * that is being bound never reaches the game's own shortcut handler below - it
 * would otherwise flip the board on its way to becoming the flip key.
 */
const KEY_BUTTONS = {
  preciseKey: $("s-precise") as HTMLButtonElement,
  unselectKey: $("s-unselect") as HTMLButtonElement,
} as const;
type Binding = keyof typeof KEY_BUTTONS;
let listening: Binding | null = null;

function drawBindings(): void {
  for (const [name, btn] of Object.entries(KEY_BUTTONS) as [Binding, HTMLButtonElement][]) {
    const live = listening === name;
    btn.textContent = live ? "Press a key…" : keyLabel(settings[name]);
    btn.classList.toggle("listening", live);
  }
}

function listen(name: Binding): void {
  listening = listening === name ? null : name;
  $("s-keys-note").textContent = listening
    ? "Press the key you want. Tab is not available: the page needs it."
    : KEYS_NOTE;
  drawBindings();
}

const KEYS_NOTE = $("s-keys-note").textContent ?? "";
for (const [name, btn] of Object.entries(KEY_BUTTONS) as [Binding, HTMLButtonElement][]) {
  btn.addEventListener("click", () => listen(name));
}
drawSettings();

window.addEventListener("keydown", (e) => {
  if (!listening) return;
  e.preventDefault();
  e.stopPropagation();
  const name = listening;
  const other: Binding = name === "preciseKey" ? "unselectKey" : "preciseKey";
  if (e.key === "Tab") {
    $("s-keys-note").textContent = "Tab is not available: the page needs it.";
    return;
  }
  if (keyMatches(e.key, settings[other])) {
    $("s-keys-note").textContent =
      `${keyLabel(e.key)} is already the ${other === "preciseKey" ? "precise" : "unselect"} key.`;
    return;
  }
  listening = null;
  saveSettings({ [name]: e.key });
  // Rebinding while the old key is held would leave precise mode on with
  // nothing left to release it.
  if (name === "preciseKey" && precise && !preciseLatched) setPrecise(false);
  $("s-keys-note").textContent = KEYS_NOTE;
  drawBindings();
  KEY_BUTTONS[name].blur();   // or the new binding re-presses the button
}, true);

for (const input of document.querySelectorAll<HTMLInputElement>("[data-view]")) {
  input.checked = VIEW_DEFAULTS[input.dataset.view as keyof View];
}

canvas.addEventListener("pointerdown", onPointerDown);
canvas.addEventListener("pointermove", onPointerMove);
canvas.addEventListener("pointerup", onPointerUp);
canvas.addEventListener("pointercancel", () => {
  dragId = null;
  dragPos = null;
});
canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  selectedId = null;
  dragId = null;
  dragPos = null;
});

// Mobile browsers suspend sockets on backgrounding, so reclaim the seat on
// wake rather than letting the grace window run out. Returning to the tab is a
// fresh signal, so it restarts an attempt budget the close handler used up.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !net) return;
  if (!net.isClosed() || rejoining || !savedSeat()) return;
  beginRejoin();
});
window.addEventListener("resize", () => renderer?.resize());
/** True while the keystroke belongs to something the player is typing into. */
function typingInto(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.tagName === "INPUT" ||
      target.tagName === "TEXTAREA" ||
      target.tagName === "SELECT" ||
      target.isContentEditable)
  );
}

window.addEventListener("keydown", (e) => {
  // Without this the shortcuts fire while a name or password is being typed:
  // "f" flips the board behind the lobby, and space would be swallowed.
  if (typingInto(e.target)) return;
  if (keyMatches(e.key, settings.unselectKey)) {
    selectedId = null;
    // Any key may be bound, including ones the page itself uses: space
    // scrolls, and would also press whichever button has focus.
    if (e.key === " ") e.preventDefault();
  }
  if (e.key === "f" && renderer) {
    renderer.flipped = !renderer.flipped;
    // Both of these are drawn per end of the board, so both follow the flip.
    if (state) showCivLegend(state.civs);
    renderPlates();
  }
  if (keyMatches(e.key, settings.preciseKey) && !precise) {
    setPrecise(true);
    if (e.key === " ") e.preventDefault();
  }
  if (e.key === " " && player) {
    // Space is play/pause while watching. Claimed only on that screen:
    // elsewhere it still scrolls the page and still presses a focused button.
    // Putting a piece down is the unselect key, whatever the player bound it
    // to - Space included, if they want the old second binding back.
    e.preventDefault();
    player.toggle();
  }
});
window.addEventListener("keyup", (e) => {
  // The button latches precise mode; releasing the key must not cancel that.
  if (keyMatches(e.key, settings.preciseKey) && !preciseLatched) setPrecise(false);
});

// A returning player is signed in before they touch anything, so the profile
// is right on arrival and, more importantly, so a room they open is opened by
// somebody. Only a stored token pays for this: a first-time visitor still
// opens no socket until they press something.
//
// The ordering that makes it safe is the server's: one connection's messages
// are dispatched one at a time, to completion, so RESUME_SESSION is fully
// handled before any CREATE_ROOM behind it. The seat cannot be taken by a
// player the server has not identified yet.
setWho("open");
setBalanced(false);

// A seat stored from before this page load: a reload, or a phone that was
// switched away from long enough for the socket to die. Reclaim it before
// anything else, because the alternative is what went wrong - the room still
// holding a seat for a player whose page has forgotten the token, so one side
// waits for somebody who is already back and the other is told "room is full"
// by their own reservation.
const bootSeat = savedSeat();
if (bootSeat) {
  void ensureConnected().then((ok) => {
    if (!ok) return;
    rejoining = true;   // so a refusal clears the stored seat rather than sticking
    net.rejoin(bootSeat.code, bootSeat.token);
  });
} else if (account.token()) {
  void ensureConnected();
}

// Presence is polled, not pushed: a broadcast to everybody on every connect
// and sign-out is a lot of traffic for a number in a corner, and this does not
// have to be up to the second. Only while the Play tab is actually in front.
window.setInterval(() => {
  if (lobby.style.display === "none") return;
  if (($("tab-play") as HTMLElement).hidden) return;
  void refreshOnline();
}, 30000);

// A room code in the fragment makes games shareable as a link.
if (location.hash.length > 1) {
  ($("room-code") as HTMLInputElement).value = location.hash.slice(1).toUpperCase();
}

requestAnimationFrame(frame);
