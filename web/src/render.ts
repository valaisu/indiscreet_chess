/**
 * Canvas renderer. Colours and layout follow client/renderer.py.
 *
 * Board coordinates match the server: (0,0) top-left (black back rank),
 * (8,8) bottom-right (white back rank).
 */

import { canCastle, type Piece } from "./geometry.ts";
import { type GameState, forPiece } from "./protocol.ts";

const C_BG = "#1e1e1e";
const C_LIGHT = "#f0d9b5";
const C_DARK = "#b58863";
const C_BOARD_BORDER = "#64503c";
const C_WHITE_FILL = "#ffffff";
const C_BLACK_FILL = "#161616";
const C_WHITE_BORDER = "#c8c8c8";
const C_BLACK_BORDER = "#2d2d2d";
const C_WHITE_ICON = "#0c0c0c";
const C_BLACK_ICON = "#f5f5f5";
const C_SELECT = "#50d250";
const C_DEST_MARKER = "#dcc832";
const C_GHOST_FILL = "#a0a0c8";
const C_MANA_BG = "#191937";
const C_MANA_WHITE = "#4682c8";
const C_MANA_BLACK = "#b43c3c";
const C_TEXT = "#dcdcdc";
const C_TIMER_PREP = "#dcb932";
const C_TIMER_COOL = "#46a0dc";
const C_WIN_TEXT = "#ffdc64";
const C_HINT_OK = "rgba(100,210,100,0.31)";      // legal and affordable
const C_HINT_NO_MANA = "rgba(220,140,40,0.31)";  // legal direction, too far for the mana
const C_HINT_ILLEGAL = "rgba(180,60,60,0.31)";   // not currently legal

const SQRT2 = Math.SQRT2;
const DIAMETER_PIECE = 0.6;
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
  private pieceR = 24;
  private manaH = 22;
  flipped = false;

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
    this.pieceR = Math.max(4, Math.round(this.sq * 0.3));
    this.manaH = Math.max(6, Math.round(this.sq * 0.275));
    this.boardX = Math.round((w - this.sq * 8) / 2);
    this.boardY = Math.round((h - this.sq * 8) / 2 + this.manaH * 0.6);
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
    this.drawStatus(rtt, rect.width);

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
    ctx.fillStyle = color;
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
    if (!selectedId) return;
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
        ctx.fillStyle = Math.hypot(a, b) <= maxDist ? C_HINT_OK : C_HINT_NO_MANA;
        ctx.fill();
      }
    } else if (ptype === "pawn") {
      const fwd = owner === "white" ? -1 : 1;
      const maxFwd = piece.has_moved ? 1.0 : 2.0;
      this.wedgeMana(cx, cy, this.dirToAngle(0, fwd), fr, maxFwd * this.sq, manaR);

      // Diagonal capture landing circles: red overall, with the arc that a
      // reachable enemy actually opens painted on top.
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
        ctx.fillStyle = C_HINT_ILLEGAL;
        ctx.fill();

        for (const other of state.pieces) {
          if (other.id === piece.id || other.owner === owner) continue;
          const odx = other.x - ccxB;
          const ody = other.y - ccyB;
          const otherD = Math.hypot(odx, ody);
          if (otherD > diagRBoard + DIAMETER_PIECE + 1e-6) continue;
          let alpha: number;
          if (otherD < 1e-9) {
            alpha = Math.PI;
          } else {
            const cosA =
              (diagRBoard * diagRBoard + otherD * otherD - DIAMETER_PIECE * DIAMETER_PIECE) /
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
      ctx.arc(cx, cy, Math.max(3, this.pieceR * 0.35), 0, Math.PI * 2);
      ctx.fillStyle = C_DEST_MARKER;
      ctx.fill();
    }
    if (selectedId) {
      const sel = state.pieces.find((p) => p.id === selectedId);
      if (sel) {
        const [cx, cy] = this.boardToPx(sel.x, sel.y);
        ctx.beginPath();
        ctx.arc(cx, cy, this.pieceR + 5, 0, Math.PI * 2);
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

    if (p.type === "ghost") {
      ctx.beginPath();
      ctx.arc(cx, cy, this.pieceR, 0, Math.PI * 2);
      ctx.fillStyle = C_GHOST_FILL + "70";
      ctx.fill();
      ctx.strokeStyle = C_GHOST_FILL;
      ctx.lineWidth = 2;
      ctx.stroke();
      return;
    }

    const isWhite = p.owner === "white";
    ctx.beginPath();
    ctx.arc(cx, cy, this.pieceR, 0, Math.PI * 2);
    ctx.fillStyle = isWhite ? C_WHITE_FILL : C_BLACK_FILL;
    ctx.fill();
    ctx.strokeStyle = isWhite ? C_BLACK_BORDER : C_WHITE_BORDER;
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = isWhite ? C_WHITE_ICON : C_BLACK_ICON;
    ctx.font = `${Math.round(this.pieceR * 1.6)}px "DejaVu Sans", "Segoe UI Symbol", sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(ICONS[`${p.type}/${p.owner}`] ?? "?", cx, cy);

    if (p.state === "cooldown") {
      ctx.beginPath();
      ctx.arc(cx, cy, this.pieceR, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0,0,0,0.39)";
      ctx.fill();
    }

    const timer = p.state_timer ?? 0;
    if (p.state === "preparation") {
      const total = forPiece(state, p, "preparation_period", state.prep_period, 0.5);
      if (total > 0) this.drawTimerArc(cx, cy, 1 - timer / total, C_TIMER_PREP);
    } else if (p.state === "cooldown") {
      const total = forPiece(state, p, "cooldown", state.cooldown, 0.8);
      if (total > 0) this.drawTimerArc(cx, cy, 1 - timer / total, C_TIMER_COOL);
    }
  }

  private drawTimerArc(cx: number, cy: number, fraction: number, color: string): void {
    if (fraction <= 0) return;
    const ctx = this.ctx;
    const f = Math.min(1, fraction);
    ctx.beginPath();
    ctx.arc(cx, cy, this.pieceR + 4, -Math.PI / 2, -Math.PI / 2 + f * Math.PI * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.stroke();
  }

  private drawMana(state: GameState, width: number): void {
    const ctx = this.ctx;
    const barW = this.sq * 8;
    const x = this.boardX;
    const showBoth = this.playerColor === null;
    const own = this.playerColor ?? "white";
    const rows: [string, number][] = showBoth
      ? [["black", this.boardY - this.manaH - 6],
         ["white", this.boardY + this.sq * 8 + 6]]
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
    }
    void width;
  }

  private drawStatus(rtt: number, width: number): void {
    const ctx = this.ctx;
    ctx.fillStyle = C_TEXT;
    ctx.font = "13px system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "top";
    ctx.fillText(rtt ? `${rtt} ms` : "", width - 10, 8);
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
