/**
 * Entry point: lobby wiring, input handling, render loop.
 */

import { Net } from "./net.ts";
import { Renderer } from "./render.ts";
import { interpolate } from "./interp.ts";
import { snapDestination, findPieceAt, type Piece } from "./geometry.ts";
import * as P from "./protocol.ts";
import { type GameState, perOwner } from "./protocol.ts";

const CLICK_R_SELECT = 0.5; // forgiving radius when nothing is selected
const CLICK_R_SWITCH = 0.3; // strict radius once a piece is selected
const MOVE_SNAP_MAX = 0.625; // ignore clicks further than this from a legal spot

const DEFAULT_URL =
  (import.meta as any).env?.VITE_SERVER_URL ??
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname}:8765`;

const $ = (id: string) => document.getElementById(id)!;
const lobby = $("lobby") as HTMLDivElement;
const gameEl = $("game") as HTMLDivElement;
const canvas = $("board") as HTMLCanvasElement;
const statusEl = $("status") as HTMLDivElement;
const banner = $("banner") as HTMLDivElement;

let net: Net;
let renderer: Renderer;
let state: GameState | null = null;
let stateAt = 0;
let selectedId: string | null = null;

function setStatus(text: string, isError = false): void {
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
  net.on(P.ERROR, (m) => setStatus(m.reason ?? "server error", true));
  net.on(P.ROOM_CREATED, (m) => {
    setStatus(m.solo ? "Solo game starting…" : `Room ${m.code} — waiting for opponent`);
    ($("room-code") as HTMLInputElement).value = m.code;
    if (!m.solo) history.replaceState(null, "", `#${m.code}`);
  });
  net.on(P.ROOM_JOINED, (m) => setStatus(`Joined ${m.code} as ${m.color}`));
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
  net.on("close", () => showBanner("Disconnected"));

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
  gameEl.style.display = "block";
  renderer = new Renderer(canvas, net.color);
  renderer.resize();
}

// --- input ------------------------------------------------------------------

function onPointerDown(ev: PointerEvent): void {
  if (!state || state.game_over || state.countdown !== null) return;
  const [bx, by] = renderer.pxToBoard(ev.clientX, ev.clientY);
  if (!(bx >= 0 && bx < 8 && by >= 0 && by < 8)) return;

  const pieces = state.pieces;
  const solo = net.color === null;

  if (selectedId === null) {
    const clicked = findPieceAt(bx, by, pieces, CLICK_R_SELECT);
    if (clicked && clicked.state === "idle" && (solo || clicked.owner === net.color)) {
      selectedId = clicked.id;
    }
    return;
  }

  const clicked = findPieceAt(bx, by, pieces, CLICK_R_SWITCH);
  if (clicked && clicked.id === selectedId) {
    selectedId = null; // click the selected piece again to drop it
    return;
  }
  const sel = pieces.find((p) => p.id === selectedId);
  if (clicked && sel && clicked.state === "idle" && clicked.owner === sel.owner) {
    selectedId = clicked.id; // precise click on a friendly piece switches
    return;
  }
  if (sel) {
    const freedom = perOwner(state.freedom_deg, sel.owner, 5.0);
    const snap = snapDestination(bx, by, sel as Piece, freedom, pieces);
    if (Number.isFinite(snap.d) && snap.d <= MOVE_SNAP_MAX) {
      net.queueMove(selectedId, snap.x, snap.y);
      selectedId = null;
    }
  }
}

// --- render loop ------------------------------------------------------------

function frame(): void {
  if (state && renderer) {
    renderer.render(interpolate(state, performance.now() - stateAt), selectedId, net.rtt);
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

canvas.addEventListener("pointerdown", onPointerDown);
canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  selectedId = null;
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
