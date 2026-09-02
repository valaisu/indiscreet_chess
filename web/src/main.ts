/**
 * Entry point: lobby wiring, input handling, render loop.
 */

import { Net } from "./net.ts";
import { Renderer } from "./render.ts";
import { interpolate } from "./interp.ts";
import { snapDestination, findPieceAt, type Piece } from "./geometry.ts";
import * as P from "./protocol.ts";
import { presetParams } from "./presets.ts";
import { withCiv, piecePayload } from "./civs.ts";
import { type GameState, forPiece } from "./protocol.ts";

const CLICK_R_SELECT = 0.5; // forgiving radius when nothing is selected
const CLICK_R_SWITCH = 0.3; // strict radius once a piece is selected
const MOVE_SNAP_MAX = 0.625; // ignore clicks further than this from a legal spot
const RECONNECT_TRIES = 10; // 10 x 3s covers both a Fly deploy and the 30s grace
const RECONNECT_DELAY_MS = 3000;

const DEFAULT_URL =
  (import.meta as any).env?.VITE_SERVER_URL ??
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname}:8765`;

const $ = (id: string) => document.getElementById(id)!;
const lobby = $("lobby") as HTMLDivElement;
const gameEl = $("game") as HTMLDivElement;
const canvas = $("board") as HTMLCanvasElement;
const statusEl = $("status") as HTMLDivElement;
const banner = $("banner") as HTMLDivElement;
const modeSel = $("mode") as HTMLSelectElement;
const civSel = $("civ") as HTMLSelectElement;

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
  const url = ($("server-url") as HTMLInputElement).value.trim() || DEFAULT_URL;
  net = new Net(url);
  net.on(P.ERROR, (m) => {
    if (rejoining) {
      // Rooms live only in the server's memory. If it restarted, the game is
      // gone and no amount of retrying brings it back — say so and stop.
      rejoining = false;
      sessionStorage.removeItem("seat");
      showBanner("Game ended \u2014 the server restarted");
      return;
    }
    setStatus(m.reason ?? "server error", true);
  });
  net.on(P.ROOM_CREATED, (m) => {
    setStatus(m.solo ? "Solo game starting…" : `Room ${m.code} — waiting for opponent`);
    ($("room-code") as HTMLInputElement).value = m.code;
    if (!m.solo) history.replaceState(null, "", `#${m.code}`);
  });
  net.on(P.ROOM_JOINED, (m) => {
    rejoining = false;
    rejoinAttempts = 0;
    showBanner("");
    setStatus(`Joined ${m.code} as ${m.color}`);
  });
  net.on(P.ROOM_STATE, (m) => {
    if (m.waiting) setStatus(`Room ${m.code} — ${m.players}/2 players`);
  });
  net.on(P.GAME_STATE, (m: GameState) => {
    if (lobby.style.display !== "none") enterGame();
    state = m;
    stateAt = performance.now();
    if (m.game_over) showBanner(m.winner === "draw" ? "Draw" : `${m.winner} wins`);
  });
  net.on(P.MOVE_REJECTED, (m) => setStatus(`rejected: ${m.reason}`, true));
  net.on(P.OPPONENT_LEFT, (m) => startLeftCountdown(m.grace_seconds ?? 30));
  net.on(P.OPPONENT_REJOINED, () => {
    stopLeftCountdown();
    showBanner("");
  });
  net.on("version-mismatch", () => {
    // The banner only exists on the game screen, and a mismatch is noticed at
    // connect time — usually still in the lobby. Say it in both places, and
    // set the flag last so setStatus can pin it against later messages.
    const text = "New version available \u2014 reload the page";
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
  showBanner(`Opponent disconnected — ${left}s`);
  stopLeftCountdown();
  leftTimer = window.setInterval(() => {
    left -= 1;
    if (left <= 0) stopLeftCountdown();
    else showBanner(`Opponent disconnected — ${left}s`);
  }, 1000);
}
function stopLeftCountdown(): void {
  if (leftTimer !== null) window.clearInterval(leftTimer);
  leftTimer = null;
}

// --- reconnect --------------------------------------------------------------

function savedSeat(): { code: string; token: string } | null {
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
    showBanner("Disconnected \u2014 reload to start a new game");
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
 * Write the current mode+civ into the settings fields. "Custom" means the
 * fields were hand-set, so neither is reapplied — a civ multiplier compounds
 * if it lands on values it has already modified.
 */
function applySettings(): void {
  const base = presetParams(modeSel.value);
  if (!base) return;
  const params = withCiv(base, civSel.value);
  for (const input of document.querySelectorAll<HTMLInputElement>("[data-param]")) {
    const value = params[input.dataset.param!];
    if (value !== undefined) input.value = String(value);
  }
}

function readParams(): object {
  const params: Record<string, number> = {};
  for (const input of document.querySelectorAll<HTMLInputElement>("[data-param]")) {
    const value = parseFloat(input.value);
    if (!Number.isNaN(value)) params[input.dataset.param!] = value;
  }
  // A civ may single out a piece type. Those are derived from the fields above
  // rather than shown, so they follow whatever the fields currently say.
  const pieces = piecePayload(params, civSel.value);
  return Object.keys(pieces).length ? { ...params, pieces } : params;
}

function enterGame(): void {
  lobby.style.display = "none";
  gameEl.style.display = "block";
  renderer = new Renderer(canvas, net.color);
  renderer.resize();
}

// --- input ------------------------------------------------------------------

function playable(): boolean {
  return !!state && !state.game_over && state.countdown === null;
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
  const solo = net.color === null;
  const mine = (p: Piece) => solo || p.owner === net.color;

  // Picking up an own idle piece starts a drag; the same press also selects it,
  // so releasing in place leaves the click-click flow intact.
  const grabbed = findPieceAt(bx, by, pieces, CLICK_R_SELECT);
  if (grabbed && grabbed.state === "idle" && mine(grabbed)) {
    if (selectedId !== null && selectedId !== grabbed.id) {
      const sel = pieces.find((p) => p.id === selectedId);
      // A precise click on a friendly piece switches rather than moves.
      const precise = findPieceAt(bx, by, pieces, CLICK_R_SWITCH);
      if (sel && !precise && tryMove(sel, bx, by)) {
        selectedId = null;
        return;
      }
    }
    dragWasSelected = selectedId === grabbed.id;
    dragId = grabbed.id;
    dragPos = [ev.clientX, ev.clientY];
    selectedId = grabbed.id;
    canvas.setPointerCapture(ev.pointerId);
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
  const movedFar = Math.hypot(bx - sel.x, by - sel.y) > CLICK_R_SWITCH;

  if (!movedFar) {
    // Released where it started: a plain click. Toggles the selection.
    if (dragWasSelected) selectedId = null;
    return;
  }
  if (bx >= 0 && bx < 8 && by >= 0 && by < 8 && tryMove(sel, bx, by)) {
    selectedId = null;
  }
  // Otherwise keep it selected so the click-click flow can still be used.
}

// --- render loop ------------------------------------------------------------

function frame(): void {
  if (state && renderer) {
    renderer.render(interpolate(state, performance.now() - stateAt), selectedId,
                    net.rtt, dragId, dragPos);
  }
  requestAnimationFrame(frame);
}

// --- wiring -----------------------------------------------------------------

($("server-url") as HTMLInputElement).value =
  localStorage.getItem("serverUrl") ?? DEFAULT_URL;

$("btn-create").addEventListener("click", async () => {
  if (await ensureConnected()) net.createRoom(readParams());
});
$("btn-join").addEventListener("click", async () => {
  const code = ($("room-code") as HTMLInputElement).value.trim();
  if (!code) return setStatus("enter a room code", true);
  if (await ensureConnected()) net.joinRoom(code);
});
$("btn-quick").addEventListener("click", async () => {
  if (await ensureConnected()) net.quickMatch(readParams());
});
$("btn-solo").addEventListener("click", async () => {
  if (await ensureConnected()) net.createRoom(readParams(), true);
});
($("server-url") as HTMLInputElement).addEventListener("change", (e) => {
  localStorage.setItem("serverUrl", (e.target as HTMLInputElement).value);
});

modeSel.addEventListener("change", applySettings);
civSel.addEventListener("change", applySettings);
// Editing any field by hand means the settings are no longer a named mode.
for (const input of document.querySelectorAll<HTMLInputElement>("[data-param]")) {
  input.addEventListener("input", () => {
    modeSel.value = "custom";
  });
}
// The fields are marked up with the server defaults; start them on the
// selected mode so what is shown is what will be sent.
applySettings();

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
});

// A room code in the fragment makes games shareable as a link.
if (location.hash.length > 1) {
  ($("room-code") as HTMLInputElement).value = location.hash.slice(1).toUpperCase();
}

requestAnimationFrame(frame);
