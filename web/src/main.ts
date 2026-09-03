/**
 * Entry point: lobby wiring, input handling, render loop.
 */

import { Net } from "./net.ts";
import { Renderer } from "./render.ts";
import { interpolate } from "./interp.ts";
import { snapDestination, findPieceAt, type Piece } from "./geometry.ts";
import * as P from "./protocol.ts";
import { presetParams } from "./presets.ts";
import { withCiv, piecePayload, describe, TITLE, CIV_NAMES } from "./civs.ts";
import { CIV_ICONS } from "./civicons.ts";
import { type GameState, forPiece } from "./protocol.ts";
import { settings, save as saveSettings, PRECISE_MIN_DRAG, VIEW_DEFAULTS,
         type View } from "./settings.ts";
import { Recording, Player, SPEEDS } from "./replay.ts";

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
let replayReturn: "postgame" | "profile" = "postgame";
let matchLogged = false; // one history row per game, however many final frames

/**
 * A finished game. The row is small and lives in localStorage; the recording
 * it names is the snapshots themselves, which stay in memory. That split is
 * deliberate: results are worth keeping and cost nothing, while a recording is
 * megabytes and there is no database to put it in yet.
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
}

const HISTORY_KEY = "matches";
const MAX_HISTORY = 50;
const recordings = new Map<string, Recording>();

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
      // Rooms live only in the server's memory. If it restarted, the game is
      // gone and no amount of retrying brings it back - say so and stop.
      rejoining = false;
      sessionStorage.removeItem("seat");
      showBanner("Game ended: the server restarted");
      return;
    }
    // The lobby's status line is off-screen once the pre-game screen is up,
    // and a civilization can push piece size past what the opening position
    // allows - so a rejection at Ready has to be visible where it happens.
    const reason = m.reason ?? "server error";
    if (pregame.style.display === "block") {
      $("pg-status").textContent = reason;
      ($("btn-ready") as HTMLButtonElement).disabled = false;
    }
    setStatus(reason, true);
  });
  net.on(P.ROOM_CREATED, (m) => {
    ($("room-code") as HTMLInputElement).value = m.code;
    soloRoom = !!m.solo;
    inRoom = true;
    if (!m.solo) history.replaceState(null, "", `#${m.code}`);
    showPregame(m.code);
  });
  net.on(P.ROOM_JOINED, (m) => {
    rejoining = false;
    rejoinAttempts = 0;
    soloRoom = !!savedSeat()?.solo;
    inRoom = true;
    showBanner("");
    showPregame(m.code);
  });
  net.on(P.ROOM_STATE, (m) => {
    // The room's tempo, so a joiner applies their civ to it rather than to
    // whatever their own lobby fields happen to say.
    if (m.base_params) baseParams = m.base_params;
    if (!m.waiting) return;
    const me = net.color ?? "white";
    const them = me === "white" ? "black" : "white";
    const mine = m.ready?.[me];
    // Both halves come from the server, so pressing Ready cannot leave the
    // screen claiming something the room does not agree with.
    const theirs = !m.seated?.[them]
      ? "waiting for an opponent to join"
      : m.ready?.[them]
        ? "your opponent is ready"
        : "your opponent is still choosing";
    $("pg-status").textContent =
      `${mine ? "You are ready" : "Pick a civilization, then press Ready"}: ${theirs}.`;
    const btn = $("btn-ready") as HTMLButtonElement;
    btn.disabled = !!mine;
    for (const el of civCards.children) el.classList.toggle("pick", !mine);
  });
  net.on(P.GAME_STATE, (m: GameState) => {
    // A frame can still be in flight when the room is left, and acting on it
    // would drag the player back onto the board from the lobby.
    if (!inRoom) return;
    // Gate on the board, not the lobby: the pre-game screen has already hidden
    // the lobby by this point.
    if (gameEl.style.display !== "block") enterGame();
    state = m;
    stateAt = performance.now();
    recording.push(m, stateAt);
    if (m.game_over) {
      showBanner("");
      logMatch(m);
      showPostgame(m.winner === "draw" ? "Draw" : `${m.winner} wins`);
    }
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

/** Build a civ card. Pickable ones select; the lobby copy is read-only. */
function civCard(civ: string, pickable: boolean): HTMLElement {
  const card = document.createElement("div");
  card.className = pickable ? "card pick" : "card";
  card.dataset.civ = civ;
  const name = civ === "none" ? "None" : civ[0].toUpperCase() + civ.slice(1);
  const effects = civ === "none" ? [] : describe(civ);
  card.innerHTML =
    `<div class="head">${CIV_ICONS[civ]}` +
    `<div><h3>${name}</h3><p class="title">${TITLE[civ]}</p></div>` +
    `<span class="tag"></span></div><ul>` +
    effects
      .map((e) => `<li class="${e.good ? "good" : "bad"}">` +
                  `<span>${e.what}</span><span class="amt">${e.amount}</span></li>`)
      .join("") +
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
for (const civ of CIV_NAMES) $("civ-reference").append(civCard(civ, false));
refreshPicks();

/** The params a seat will actually play with: room tempo plus its civ. */
function readyParams(civ: string): object {
  const base = baseParams ?? (readParams() as Record<string, number>);
  const withMods = withCiv(base, civ);
  const pieces = piecePayload(withMods, civ);
  return Object.keys(pieces).length ? { ...withMods, pieces } : withMods;
}

function showPregame(code: string): void {
  lobby.style.display = "none";
  pregame.style.display = "block";
  $("pg-code").textContent = code;
  $("pg-seats").style.display = soloRoom ? "flex" : "none";
  $("pg-lede").textContent = soloRoom
    ? "You play both sides. Pick a civilization for each seat, then press Ready."
    : "Pick a civilization. Your opponent cannot see your choice until the game begins.";
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

function readParams(): object {
  const params: Record<string, number> = {};
  for (const input of document.querySelectorAll<HTMLInputElement>("[data-param]")) {
    const value = parseFloat(input.value);
    if (!Number.isNaN(value)) params[input.dataset.param!] = value;
  }
  return params;
}

function enterGame(): void {
  lobby.style.display = "none";
  pregame.style.display = "none";
  gameEl.style.display = "block";
  gameBar.style.display = "flex";
  matchLogged = false;
  // Nobody to resign to when both sides are yours: exiting is the way out.
  $("btn-resign").style.display = soloRoom ? "none" : "";
  renderer = new Renderer(canvas, net.color);
  renderer.hints = hintMode();
  renderer.resize();
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
  if (!(bx >= 0 && bx < 8 && by >= 0 && by < 8)) return;

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
  recordings.set(id, recording);
  const row: Match = {
    id,
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

const civLabel = (civ: string | null | undefined) =>
  civ ? civ[0].toUpperCase() + civ.slice(1) : "None";

function renderProfile(): void {
  const rows = matchHistory();
  const list = $("pf-list");
  list.textContent = "";
  const played = rows.filter((m) => !m.solo && m.winner !== "unfinished");
  const won = played.filter((m) => m.winner === m.seat).length;
  const drawn = played.filter((m) => m.winner === "draw").length;
  const rest = rows.length - played.length;
  $("pf-summary").textContent = rows.length
    ? `${rows.length} game${rows.length === 1 ? "" : "s"}: ` +
      `${won} won, ${played.length - won - drawn} lost, ${drawn} drawn` +
      `${rest ? `, ${rest} solo or unfinished` : ""}.`
    : "No games yet.";

  for (const m of rows) {
    const row = document.createElement("div");
    row.className = "match";
    const outcome =
      m.winner === "unfinished" ? "Unfinished"
      : m.solo ? (m.winner === "draw" ? "Draw" : `${civLabel(m.winner)} wins`)
      : m.winner === "draw" ? "Draw"
      : m.winner === m.seat ? "Won" : "Lost";
    const cls = m.solo || m.winner === "draw" || m.winner === "unfinished"
      ? "" : m.winner === m.seat ? "win" : "loss";
    const sides = m.solo
      ? `${civLabel(m.civs.white)} vs ${civLabel(m.civs.black)}`
      : `${civLabel(m.civs[m.seat ?? "white"])} vs ` +
        `${civLabel(m.civs[m.seat === "white" ? "black" : "white"])}`;
    const mins = Math.floor(m.seconds / 60);
    row.innerHTML =
      `<span class="what"><span class="result ${cls}">${outcome}</span> ` +
      `${m.solo ? "solo" : `as ${m.seat}`}, ${sides}` +
      `<span class="when">${m.tempo}, ` +
      `${mins ? `${mins}m ` : ""}${m.seconds % 60}s, ` +
      `${new Date(m.at).toLocaleString()}</span></span>`;
    const rec = recordings.get(m.id);
    if (rec) {
      const btn = document.createElement("button");
      btn.textContent = "Watch replay";
      btn.addEventListener("click", () =>
        watchReplay(rec, m.solo ? null : m.seat, "profile"));
      row.append(btn);
    } else {
      const gone = document.createElement("span");
      gone.className = "gone";
      gone.textContent = "replay expired";
      row.append(gone);
    }
    list.append(row);
  }
}

/**
 * Watch a recording, either the game just finished or one picked out of the
 * profile. `from` is where Close goes back to, because those are the only two
 * places a replay can be started from.
 */
function watchReplay(rec: Recording, color: string | null,
                     from: "postgame" | "profile"): void {
  if (!rec.frames.length) return;
  replayReturn = from;
  lobby.style.display = "none";
  pregame.style.display = "none";
  gameEl.style.display = "block";
  postgame.style.display = "none";
  gameBar.style.display = "none"; // resigning an old game means nothing
  replayBar.style.display = "flex";
  selectedId = null;
  dragId = null;
  renderer = new Renderer(canvas, color);
  renderer.hints = hintMode();
  player = new Player(rec);
  setSpeed(1);
}

function exitReplay(): void {
  player = null;
  replayBar.style.display = "none";
  if (replayReturn === "profile") {
    gameEl.style.display = "none";
    lobby.style.display = "flex";
    showTab("profile");
    return;
  }
  gameBar.style.display = "flex";
  postgame.style.display = "block";
}

function setSpeed(speed: number): void {
  if (player) player.speed = speed;
  for (const el of $("rp-speeds").children) {
    el.classList.toggle("on", parseFloat((el as HTMLElement).dataset.speed!) === speed);
  }
}

const seek = $("rp-seek") as HTMLInputElement;
let seeking = false;

function updateReplayBar(): void {
  if (!player) return;
  const total = player.recording.duration;
  if (!seeking) seek.value = String(total ? (player.t / total) * 1000 : 0);
  // Plain shapes, not the media-control codepoints: those fall back to a
  // tofu box in fonts that lack them.
  $("rp-play").textContent = player.playing ? "\u275a\u275a" : "\u25b6";
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
  soloRoom = false;
  baseParams = null;
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
  gameBar.style.display = "flex";
  $("btn-resign").style.display = "";
  ($("btn-ready") as HTMLButtonElement).disabled = false;
  $("pg-status").textContent = "";
  gameEl.style.display = "none";
  pregame.style.display = "none";
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
  if (name === "profile") renderProfile();
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

$("btn-create").addEventListener("click", async () => {
  if (await ensureConnected()) net.createRoom(readParams(), false, readView());
});
$("btn-join").addEventListener("click", async () => {
  const code = ($("room-code") as HTMLInputElement).value.trim();
  if (!code) return setStatus("enter a room code", true);
  if (await ensureConnected()) net.joinRoom(code);
});
$("btn-quick").addEventListener("click", async () => {
  if (await ensureConnected()) net.quickMatch(readParams(), readView());
});
$("btn-solo").addEventListener("click", async () => {
  if (await ensureConnected()) net.createRoom(readParams(), true, readView());
});
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

$("btn-ready").addEventListener("click", () => {
  // One message per seat. The server takes the colour only from a solo room,
  // where this client owns both; anywhere else the seat is the one it dealt.
  const ready = (seat: "white" | "black") => {
    const civ = picks[seat];
    net.send({ type: P.SET_READY, ready: true, color: seat,
               civ: civ === "none" ? null : civ, params: readyParams(civ) });
  };
  if (soloRoom) {
    ready("white");
    ready("black");
  } else {
    ready("white");
  }
  ($("btn-ready") as HTMLButtonElement).disabled = true;
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
$("btn-clear-history").addEventListener("click", () => {
  localStorage.removeItem(HISTORY_KEY);
  recordings.clear();
  renderProfile();
});

$("btn-replay").addEventListener("click", () =>
  watchReplay(recording, soloRoom ? null : net.color, "postgame"));
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
$("rp-play").addEventListener("click", () => player?.toggle());
for (const speed of SPEEDS) {
  const b = document.createElement("button");
  b.textContent = `${speed}\u00d7`;
  b.dataset.speed = String(speed);
  b.addEventListener("click", () => setSpeed(speed));
  $("rp-speeds").append(b);
}
seek.addEventListener("pointerdown", () => { seeking = true; });
seek.addEventListener("input", () => {
  if (player) player.seek((parseFloat(seek.value) / 1000) * player.recording.duration);
});
seek.addEventListener("pointerup", () => { seeking = false; });

// Precise mode: held on a keyboard, latched by the button for touch, where
// there is no modifier to hold.
$("btn-precise").addEventListener("click", () => {
  preciseLatched = !precise;
  setPrecise(preciseLatched);
});

// Personal settings. They apply immediately; nothing is sent anywhere.
const moveModeSel = $("s-movemode") as HTMLSelectElement;
const dragInput = $("s-drag") as HTMLInputElement;
const hintsInput = $("s-hints") as HTMLInputElement;
moveModeSel.value = settings.moveMode;
dragInput.value = String(settings.dragThreshold);
hintsInput.checked = settings.showHints;
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
const preciseSel = $("s-precise") as HTMLSelectElement;
preciseSel.value = settings.preciseKey;
preciseSel.addEventListener("change", () => {
  saveSettings({ preciseKey: preciseSel.value as typeof settings.preciseKey });
  // Swapping the key while the old one is held would leave precise mode on
  // with nothing left to release it.
  if (precise && !preciseLatched) setPrecise(false);
});

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
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") selectedId = null;
  if (e.key === "f" && renderer) renderer.flipped = !renderer.flipped;
  if (e.key === settings.preciseKey && !precise) setPrecise(true);
  if (e.key === " " && player) {
    e.preventDefault();
    player.toggle();
  }
});
window.addEventListener("keyup", (e) => {
  // The button latches precise mode; releasing the key must not cancel that.
  if (e.key === settings.preciseKey && !preciseLatched) setPrecise(false);
});

// A room code in the fragment makes games shareable as a link.
if (location.hash.length > 1) {
  ($("room-code") as HTMLInputElement).value = location.hash.slice(1).toUpperCase();
}

requestAnimationFrame(frame);
