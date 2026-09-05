/**
 * Canvas renderer. Layout follows client/renderer.py; the palette is the
 * page's - ivory and walnut pieces on the board's own wood, so the canvas and
 * the surrounding chrome are one material.
 *
 * Board coordinates match the server: (0,0) top-left (black back rank),
 * (8,8) bottom-right (white back rank).
 */

import { canCastle, DIAMETER_PIECE, type Piece } from "./geometry.ts";
import { type GameState, forPiece } from "./protocol.ts";
import { civName, pieceMarks } from "./civs.ts";

const C_BG = "#241a12";
const C_LIGHT = "#f0d9b5";
const C_DARK = "#b58863";
const C_BOARD_BORDER = "#64503c";
const C_WHITE_FILL = "#f7eddb";
const C_BLACK_FILL = "#33231a";
const C_WHITE_BORDER = "#8f6f47";
const C_BLACK_BORDER = "#16100b";
const C_WHITE_ICON = "#3a2a19";
const C_BLACK_ICON = "#efe0c2";
const C_SELECT = "#50d250";
const C_DEST_MARKER = "#dcc832";
const C_GHOST_FILL = "#a0a0c8";
const C_MANA_BG = "#1c130d";
const C_MANA_WHITE = "#4682c8";
const C_MANA_BLACK = "#b43c3c";
const C_TEXT = "#e9dcbe";
const C_TIMER_PREP = "#dcb932";
const C_TIMER_COOL = "#46a0dc";
const C_WIN_TEXT = "#ffdc64";
// Civilization markers: a thickened arc of the piece's own outline. Gold
// across the top for what a civilization improves, red across the bottom for
// what it costs. Position carries the meaning as much as colour does, so the
// two survive being small and stay apart on a piece that has both.
const C_MARK_UP = "#e8bf3a";
const C_MARK_DOWN = "#e0604a";
const C_HINT_OK = "rgba(100,210,100,0.31)";      // legal and affordable
const C_HINT_NO_MANA = "rgba(220,140,40,0.31)";  // legal direction, too far for the mana
const C_HINT_ILLEGAL = "rgba(180,60,60,0.31)";   // not currently legal
// Precise mode repaints the same wedges heavier, so the mode is visible at a
// glance rather than being a key you have to remember you are holding.
const C_HINT_OK_STRONG = "rgba(120,235,120,0.6)";
const C_HINT_NO_MANA_STRONG = "rgba(230,150,45,0.6)";
const C_HINT_ILLEGAL_STRONG = "rgba(200,70,70,0.6)";

const SQRT2 = Math.SQRT2;
const ORTHO: [number, number][] = [[1, 0], [-1, 0], [0, 1], [0, -1]];
const DIAG: [number, number][] = [[1, 1], [1, -1], [-1, 1], [-1, -1]].map(
  ([x, y]) => [x / SQRT2, y / SQRT2] as [number, number],
);
const ALL8: [number, number][] = [...ORTHO, ...DIAG];
const KNIGHT: [number, number][] = [
  [2, 1], [2, -1], [-2, 1], [-2, -1], [1, 2], [1, -2], [-1, 2], [-1, -2],
];

/** Distance from (bx,by) to the board edge along (lx,ly). */
function maxToEdge(bx: number, by: number, lx: number, ly: number): number {
  let t = Infinity;
  if (lx > 1e-9) t = Math.min(t, (8 - bx) / lx);
  else if (lx < -1e-9) t = Math.min(t, bx / -lx);
  if (ly > 1e-9) t = Math.min(t, (8 - by) / ly);
  else if (ly < -1e-9) t = Math.min(t, by / -ly);
  return Math.max(0, t);
}

const ICONS: Record<string, string> = {
  "pawn/white": "♙", "pawn/black": "♟",
  "rook/white": "♖", "rook/black": "♜",
  "knight/white": "♘", "knight/black": "♞",
  "bishop/white": "♗", "bishop/black": "♝",
  "queen/white": "♕", "queen/black": "♛",
  "king/white": "♔", "king/black": "♚",
};

export class Renderer {
  private ctx: CanvasRenderingContext2D;
  private sq = 80;
  private boardX = 0;
  private boardY = 0;
  private manaH = 22;
  flipped = false;
  /** off: draw none. on: the usual translucent wedges. strong: precise mode. */
  hints: "off" | "on" | "strong" = "on";

  constructor(private canvas: HTMLCanvasElement, public playerColor: string | null) {
    this.ctx = canvas.getContext("2d")!;
    this.flipped = playerColor === "black";
    this.resize();
  }

  /** Match the backing store to the display size, or lines render soft. */
  resize(): void {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.round(rect.width * dpr);
    this.canvas.height = Math.round(rect.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const w = rect.width;
    const h = rect.height;
    this.sq = Math.max(8, Math.floor(Math.min(w / 9.2, h / 9.9)));
    this.manaH = Math.max(6, Math.round(this.sq * 0.275));
    this.boardX = Math.round((w - this.sq * 8) / 2);
    this.boardY = Math.round((h - this.sq * 8) / 2 + this.manaH * 0.6);
  }

  /** A piece's drawn radius in pixels. Hitboxes can differ per piece type. */
  private radiusPx(p: { d?: number }): number {
    return Math.max(4, Math.round(((p.d ?? DIAMETER_PIECE) / 2) * this.sq));
  }

  private hintColor(base: string): string {
    if (this.hints !== "strong") return base;
    if (base === C_HINT_OK) return C_HINT_OK_STRONG;
    if (base === C_HINT_NO_MANA) return C_HINT_NO_MANA_STRONG;
    if (base === C_HINT_ILLEGAL) return C_HINT_ILLEGAL_STRONG;
    return base;
  }

  boardToPx(bx: number, by: number): [number, number] {
    if (this.flipped) {
      return [this.boardX + (8 - bx) * this.sq, this.boardY + (8 - by) * this.sq];
    }
    return [this.boardX + bx * this.sq, this.boardY + by * this.sq];
  }

  pxToBoard(px: number, py: number): [number, number] {
    const rect = this.canvas.getBoundingClientRect();
    const x = px - rect.left;
    const y = py - rect.top;
    if (this.flipped) {
      return [8 - (x - this.boardX) / this.sq, 8 - (y - this.boardY) / this.sq];
    }
    return [(x - this.boardX) / this.sq, (y - this.boardY) / this.sq];
  }

  render(state: GameState, selectedId: string | null, rtt: number,
         dragId: string | null = null,
         dragPos: [number, number] | null = null): void {
    const ctx = this.ctx;
    const rect = this.canvas.getBoundingClientRect();
    ctx.fillStyle = C_BG;
    ctx.fillRect(0, 0, rect.width, rect.height);

    this.drawBoard();
    this.drawMoveHints(state, selectedId);
    this.drawDestinationMarkers(state, selectedId);
    for (const p of state.pieces) {
      if (p.id === dragId && dragPos) continue;
      this.drawPiece(p, state, selectedId);
    }
    if (dragId && dragPos) {
      const dragged = state.pieces.find((p) => p.id === dragId);
      if (dragged) {
        const rect2 = this.canvas.getBoundingClientRect();
        this.drawPiece(
          { ...dragged, state: "idle" },
          state,
          null,
          [dragPos[0] - rect2.left, dragPos[1] - rect2.top],
        );
      }
    }
    this.drawMana(state, rect.width);
    this.drawStatus(rtt, rect.height);

    if (state.countdown !== null && state.countdown !== undefined) {
      this.drawCentered(String(state.countdown || "GO"), rect, C_WIN_TEXT, this.sq * 1.5);
    } else if (state.game_over) {
      const text =
        state.winner === "draw" ? "Draw" : `${state.winner ?? "?"} wins`;
      this.drawCentered(text, rect, C_WIN_TEXT, this.sq * 0.7);
    }
  }

  private drawBoard(): void {
    const ctx = this.ctx;
    for (let row = 0; row < 8; row++) {
      for (let col = 0; col < 8; col++) {
        ctx.fillStyle = (row + col) % 2 === 0 ? C_LIGHT : C_DARK;
        ctx.fillRect(
          this.boardX + col * this.sq,
          this.boardY + row * this.sq,
          this.sq,
          this.sq,
        );
      }
    }
    ctx.strokeStyle = C_BOARD_BORDER;
    ctx.lineWidth = 2;
    ctx.strokeRect(this.boardX, this.boardY, this.sq * 8, this.sq * 8);
  }

  /** Board-space direction to screen angle, corrected for board flip. */
  private dirToAngle(lx: number, ly: number): number {
    return this.flipped ? Math.atan2(-ly, -lx) : Math.atan2(ly, lx);
  }

  private wedge(cx: number, cy: number, angle: number, half: number,
                r: number, color: string): void {
    if (r < 1) return;
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, angle - half, angle + half);
    ctx.closePath();
    ctx.fillStyle = this.hintColor(color);
    ctx.fill();
  }

  /** Green out to what the mana affords, orange beyond it. */
  private wedgeMana(cx: number, cy: number, angle: number, half: number,
                    fullR: number, manaR: number): void {
    if (manaR >= fullR) {
      this.wedge(cx, cy, angle, half, fullR, C_HINT_OK);
    } else if (manaR <= 0) {
      this.wedge(cx, cy, angle, half, fullR, C_HINT_NO_MANA);
    } else {
      this.wedge(cx, cy, angle, half, fullR, C_HINT_NO_MANA);
      this.wedge(cx, cy, angle, half, manaR, C_HINT_OK);
    }
  }

  private drawMoveHints(state: GameState, selectedId: string | null): void {
    if (!selectedId || this.hints === "off") return;
    const piece = state.pieces.find((p) => p.id === selectedId);
    if (!piece || piece.state !== "idle") return;

    const ctx = this.ctx;
    const { x: bx, y: by, type: ptype, owner } = piece;
    const [cx, cy] = this.boardToPx(bx, by);
    const fr =
      (forPiece(state, piece, "movement_freedom_deg", state.freedom_deg, 5.0) * Math.PI) / 180;

    const mana = state.mana?.[owner] ?? 0;
    const pp = state.player_params?.[owner];
    const baseCost = forPiece(state, piece, "base_move_cost", undefined,
                              pp?.base_move_cost ?? 1.0);
    const distCost = forPiece(state, piece, "distance_cost", undefined,
                              pp?.distance_cost ?? 0.2);
    const maxDist = distCost > 1e-9 ? Math.max(0, (mana - baseCost) / distCost) : 8.0;
    const manaR = maxDist * this.sq;

    ctx.save();
    ctx.beginPath();
    ctx.rect(this.boardX, this.boardY, this.sq * 8, this.sq * 8);
    ctx.clip();

    if (ptype === "knight") {
      const rPx = Math.max(4, Math.sqrt(5) * Math.tan(fr) * this.sq);
      for (const [a, b] of KNIGHT) {
        const tx = bx + a;
        const ty = by + b;
        if (tx < 0 || tx > 8 || ty < 0 || ty > 8) continue;
        const [px2, py2] = this.boardToPx(tx, ty);
        ctx.beginPath();
        ctx.arc(px2, py2, rPx, 0, Math.PI * 2);
        ctx.fillStyle = this.hintColor(
          Math.hypot(a, b) <= maxDist ? C_HINT_OK : C_HINT_NO_MANA);
        ctx.fill();
      }
    } else if (ptype === "pawn") {
      const fwd = owner === "white" ? -1 : 1;
      const maxFwd = piece.has_moved ? 1.0 : 2.0;
      this.wedgeMana(cx, cy, this.dirToAngle(0, fwd), fr, maxFwd * this.sq, manaR);

      // Diagonal capture landing circles: red overall, with the arc that a
      // reachable enemy actually opens painted on top.
      const pawnD = piece.d ?? DIAMETER_PIECE;
      const diagRBoard = SQRT2 * Math.tan(fr);
      const diagRPx = Math.max(4, diagRBoard * this.sq);
      const diagColor = SQRT2 <= maxDist ? C_HINT_OK : C_HINT_NO_MANA;

      for (const xdir of [1, -1]) {
        const ccxB = bx + xdir;
        const ccyB = by + fwd;
        if (!(ccxB > 0 && ccxB < 8 && ccyB > 0 && ccyB < 8)) continue;
        const [ccxPx, ccyPx] = this.boardToPx(ccxB, ccyB);
        ctx.beginPath();
        ctx.arc(ccxPx, ccyPx, diagRPx, 0, Math.PI * 2);
        ctx.fillStyle = this.hintColor(C_HINT_ILLEGAL);
        ctx.fill();

        for (const other of state.pieces) {
          if (other.id === piece.id || other.owner === owner) continue;
          const odx = other.x - ccxB;
          const ody = other.y - ccyB;
          const otherD = Math.hypot(odx, ody);
          if (otherD > diagRBoard + pawnD + 1e-6) continue;
          let alpha: number;
          if (otherD < 1e-9) {
            alpha = Math.PI;
          } else {
            const cosA =
              (diagRBoard * diagRBoard + otherD * otherD - pawnD * pawnD) /
              (2 * diagRBoard * otherD);
            if (cosA >= 1.0) continue;
            alpha = Math.acos(Math.max(-1, cosA));
          }
          this.wedge(ccxPx, ccyPx, this.dirToAngle(odx, ody), alpha, diagRPx, diagColor);
        }
      }
    } else if (ptype === "king") {
      for (const [lx, ly] of ALL8) {
        // Two squares sideways is castling, and only when a rook is waiting.
        const cap =
          ly === 0 && canCastle(piece, lx, state.pieces)
            ? 2.0
            : lx === 0 || ly === 0
              ? 1.0
              : SQRT2;
        this.wedgeMana(cx, cy, this.dirToAngle(lx, ly), fr, cap * this.sq, manaR);
      }
    } else {
      const dirs =
        ptype === "rook" ? ORTHO : ptype === "bishop" ? DIAG : ptype === "queen" ? ALL8 : [];
      for (const [lx, ly] of dirs) {
        const fullR = maxToEdge(bx, by, lx, ly) * this.sq;
        this.wedgeMana(cx, cy, this.dirToAngle(lx, ly), fr, fullR, manaR);
      }
    }

    ctx.restore();
  }

  private drawDestinationMarkers(state: GameState, selectedId: string | null): void {
    const ctx = this.ctx;
    for (const p of state.pieces) {
      if (p.state !== "moving" && p.state !== "preparation") continue;
      const dx = (p as any).dest_x;
      const dy = (p as any).dest_y;
      if (dx === undefined) continue;
      const [cx, cy] = this.boardToPx(dx, dy);
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(3, this.radiusPx(p) * 0.35), 0, Math.PI * 2);
      ctx.fillStyle = C_DEST_MARKER;
      ctx.fill();
    }
    if (selectedId) {
      const sel = state.pieces.find((p) => p.id === selectedId);
      if (sel) {
        const [cx, cy] = this.boardToPx(sel.x, sel.y);
        ctx.beginPath();
        ctx.arc(cx, cy, this.radiusPx(sel) + 5, 0, Math.PI * 2);
        ctx.strokeStyle = C_SELECT;
        ctx.lineWidth = 3;
        ctx.stroke();
      }
    }
  }

  private drawPiece(p: Piece, state: GameState, _selectedId: string | null,
                    atPx: [number, number] | null = null): void {
    const ctx = this.ctx;
    const [cx, cy] = atPx ?? this.boardToPx(p.x, p.y);
    const r = this.radiusPx(p);

    if (p.type === "ghost") {
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = C_GHOST_FILL + "70";
      ctx.fill();
      ctx.strokeStyle = C_GHOST_FILL;
      ctx.lineWidth = 2;
      ctx.stroke();
      return;
    }

    const isWhite = p.owner === "white";
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = isWhite ? C_WHITE_FILL : C_BLACK_FILL;
    ctx.fill();
    ctx.strokeStyle = isWhite ? C_BLACK_BORDER : C_WHITE_BORDER;
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = isWhite ? C_WHITE_ICON : C_BLACK_ICON;
    ctx.font = `${Math.round(r * 1.6)}px "DejaVu Sans", "Segoe UI Symbol", sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(ICONS[`${p.type}/${p.owner}`] ?? "?", cx, cy);

    // Screen top and bottom, not the piece's own: the board flips for black,
    // and "raised" reading as up the screen is the whole point of the mark.
    const marks = pieceMarks(state.civs?.[p.owner], p.type);
    if (marks.up || marks.down) {
      const quarter = Math.PI / 4;
      ctx.lineWidth = Math.max(2.5, r * 0.2);
      if (marks.up) {
        ctx.beginPath();
        ctx.arc(cx, cy, r, -Math.PI / 2 - quarter, -Math.PI / 2 + quarter);
        ctx.strokeStyle = C_MARK_UP;
        ctx.stroke();
      }
      if (marks.down) {
        ctx.beginPath();
        ctx.arc(cx, cy, r, Math.PI / 2 - quarter, Math.PI / 2 + quarter);
        ctx.strokeStyle = C_MARK_DOWN;
        ctx.stroke();
      }
    }

    if (p.state === "cooldown") {
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0,0,0,0.39)";
      ctx.fill();
    }

    const timer = p.state_timer ?? 0;
    if (p.state === "preparation") {
      const total = forPiece(state, p, "preparation_period", state.prep_period, 0.5);
      if (total > 0) this.drawTimerArc(cx, cy, r, 1 - timer / total, C_TIMER_PREP);
    } else if (p.state === "cooldown") {
      const total = forPiece(state, p, "cooldown", state.cooldown, 0.8);
      if (total > 0) this.drawTimerArc(cx, cy, r, 1 - timer / total, C_TIMER_COOL);
    }
  }

  private drawTimerArc(cx: number, cy: number, r: number, fraction: number,
                       color: string): void {
    if (fraction <= 0) return;
    const ctx = this.ctx;
    const f = Math.min(1, fraction);
    ctx.beginPath();
    ctx.arc(cx, cy, r + 4, -Math.PI / 2, -Math.PI / 2 + f * Math.PI * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.stroke();
  }

  private drawMana(state: GameState, width: number): void {
    const ctx = this.ctx;
    const barW = this.sq * 8;
    const x = this.boardX;
    // The server sends only the pools this player may see, so the presence of
    // a key is the permission: no separate flag to keep in step with it.
    const own = this.playerColor ?? "white";
    const other = own === "white" ? "black" : "white";
    const showBoth = state.mana?.[other] !== undefined;
    const rows: [string, number][] = showBoth
      ? [[other, this.boardY - this.manaH - 6],
         [own, this.boardY + this.sq * 8 + 6]]
      : [[own, this.boardY + this.sq * 8 + 6]];

    for (const [color, y] of rows) {
      const value = state.mana?.[color] ?? 0;
      const max = state.max_mana?.[color] ?? 5;
      ctx.fillStyle = C_MANA_BG;
      ctx.fillRect(x, y, barW, this.manaH);
      ctx.fillStyle = color === "white" ? C_MANA_WHITE : C_MANA_BLACK;
      ctx.fillRect(x, y, barW * Math.max(0, Math.min(1, value / max)), this.manaH);
      ctx.strokeStyle = C_TEXT;
      ctx.lineWidth = 1;
      ctx.strokeRect(x, y, barW, this.manaH);
      ctx.fillStyle = C_TEXT;
      ctx.font = `${Math.round(this.manaH * 0.7)}px system-ui, sans-serif`;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(`${color}  ${value.toFixed(1)} / ${max.toFixed(1)}`,
                   x + 6, y + this.manaH / 2);
      // The side's civilization on its own bar: the row already says whose
      // this is, so naming it here costs no new furniture. The base one is
      // named too - it is a pick like the other eight, not the absence of one.
      ctx.textAlign = "right";
      ctx.fillText(civName(state.civs?.[color]), x + barW - 6,
                   y + this.manaH / 2);
      ctx.textAlign = "left";
    }
    void width;
  }

  /** Bottom left: the top corners belong to the Precise button and the
      resign and exit buttons, which are HTML and would sit on top of it. */
  private drawStatus(rtt: number, height: number): void {
    const ctx = this.ctx;
    ctx.fillStyle = C_TEXT;
    ctx.font = "13px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.fillText(rtt ? `${rtt} ms` : "", 10, height - 8);
  }

  private drawCentered(text: string, _rect: DOMRect, color: string, size: number): void {
    const ctx = this.ctx;
    const cx = this.boardX + this.sq * 4;
    const cy = this.boardY + this.sq * 4;
    ctx.font = `bold ${Math.round(size)}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    // Box the text rather than banding the whole viewport.
    const padX = size * 0.5;
    const padY = size * 0.35;
    const w = ctx.measureText(text).width + padX * 2;
    const h = size + padY * 2;
    ctx.fillStyle = "rgba(0,0,0,0.63)";
    ctx.beginPath();
    ctx.roundRect(cx - w / 2, cy - h / 2, w, h, size * 0.2);
    ctx.fill();

    ctx.fillStyle = color;
    ctx.fillText(text, cx, cy);
  }
}
