/**
 * Canvas renderer. Colours and layout follow client/renderer.py.
 *
 * Board coordinates match the server: (0,0) top-left (black back rank),
 * (8,8) bottom-right (white back rank).
 */

import type { Piece } from "./geometry.ts";
import { type GameState, perOwner } from "./protocol.ts";

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

  render(state: GameState, selectedId: string | null, rtt: number): void {
    const ctx = this.ctx;
    const rect = this.canvas.getBoundingClientRect();
    ctx.fillStyle = C_BG;
    ctx.fillRect(0, 0, rect.width, rect.height);

    this.drawBoard();
    this.drawDestinationMarkers(state, selectedId);
    for (const p of state.pieces) this.drawPiece(p, state, selectedId);
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

  private drawPiece(p: Piece, state: GameState, _selectedId: string | null): void {
    const ctx = this.ctx;
    const [cx, cy] = this.boardToPx(p.x, p.y);

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
      const total = perOwner(state.prep_period, p.owner, 0.5);
      if (total > 0) this.drawTimerArc(cx, cy, 1 - timer / total, C_TIMER_PREP);
    } else if (p.state === "cooldown") {
      const total = perOwner(state.cooldown, p.owner, 0.8);
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

  private drawCentered(text: string, rect: DOMRect, color: string, size: number): void {
    const ctx = this.ctx;
    ctx.fillStyle = "rgba(0,0,0,0.63)";
    ctx.fillRect(0, rect.height / 2 - size, rect.width, size * 2);
    ctx.fillStyle = color;
    ctx.font = `bold ${Math.round(size)}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, rect.width / 2, rect.height / 2);
  }
}
