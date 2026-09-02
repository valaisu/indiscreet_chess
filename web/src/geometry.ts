/**
 * Input snapping — a port of _snap_destination from the pygame client.
 *
 * This module predicts what server/rules.py:validate_move will accept. When the
 * two disagree the player clicks and nothing happens, so the small pullback
 * factors below are load-bearing, not cosmetic. tools/parity_test.py asserts
 * the agreement; change nothing here without running it.
 */

export interface Piece {
  id: string;
  type: string;
  owner: string;
  x: number;
  y: number;
  state: string;
  state_timer?: number;
  vel_x?: number;
  vel_y?: number;
  has_moved?: boolean;
}

export const SQRT2 = Math.sqrt(2.0);
export const DIAMETER_PIECE = 0.6; // mirrors server/params.py default

const KNIGHT_OFFSETS: [number, number][] = [
  [2, 1], [2, -1], [-2, 1], [-2, -1], [1, 2], [1, -2], [-1, 2], [-1, -2],
];

export interface SnapResult {
  x: number;
  y: number;
  d: number;
}

/**
 * Nearest point in the piece's legal movement area to (bx, by). The legal area
 * is a union of sectors, each a cone of +/- freedomDeg around a direction. A
 * click inside a sector keeps its exact position (clamped to range); outside,
 * it snaps to the nearest sector edge.
 */
export function snapDestination(
  bx: number,
  by: number,
  piece: Piece,
  freedomDeg: number,
  pieces: Piece[] | null = null,
): SnapResult {
  const px = piece.x;
  const py = piece.y;
  const ptype = piece.type;
  const owner = piece.owner;

  const clickDx = bx - px;
  const clickDy = by - py;
  const clickR = Math.hypot(clickDx, clickDy);
  if (clickR < 1e-9) return { x: px, y: py, d: Infinity };

  const nx = clickDx / clickR;
  const ny = clickDy / clickR;
  const clickAngle = Math.atan2(clickDy, clickDx);
  const freedomRad = (freedomDeg * Math.PI) / 180;

  function boardMax(dx: number, dy: number): number {
    let t = Infinity;
    if (dx > 1e-9) t = Math.min(t, (8.0 - px) / dx);
    else if (dx < -1e-9) t = Math.min(t, px / -dx);
    if (dy > 1e-9) t = Math.min(t, (8.0 - py) / dy);
    else if (dy < -1e-9) t = Math.min(t, py / -dy);
    return Math.max(0.0, t);
  }

  let bestX = bx;
  let bestY = by;
  let bestD = Infinity;

  function trySector(dx: number, dy: number, maxT: number): void {
    if (maxT <= 0) return;
    const centerAngle = Math.atan2(dy, dx);
    const delta =
      ((clickAngle - centerAngle + Math.PI) % (2 * Math.PI) + 2 * Math.PI) %
        (2 * Math.PI) - Math.PI;

    let cx: number;
    let cy: number;
    let d: number;

    if (Math.abs(delta) <= freedomRad) {
      // Inside the sector: keep the click direction, clamp the distance.
      // 0.9999 absorbs server-side position skew — physics can nudge an idle
      // piece between the snapshot we drew and the moment it validates.
      const actualMax = Math.min(maxT, boardMax(nx, ny)) * 0.9999;
      const r = Math.min(clickR, actualMax);
      cx = px + r * nx;
      cy = py + r * ny;
      d = clickR - r;
    } else {
      // Outside: snap to the nearest edge ray. 0.99 keeps us just inside the
      // cone so the server's acos never rounds above freedomRad.
      const edgeAngle = centerAngle + Math.sign(delta) * freedomRad * 0.99;
      const ex = Math.cos(edgeAngle);
      const ey = Math.sin(edgeAngle);
      // cos/sin is not an exact unit vector, so pull back again.
      const edgeMax = Math.min(maxT, boardMax(ex, ey)) * 0.9999;
      const t = Math.max(0.0, Math.min(clickDx * ex + clickDy * ey, edgeMax));
      cx = px + t * ex;
      cy = py + t * ey;
      d = Math.hypot(bx - cx, by - cy);
    }

    if (Math.hypot(cx - px, cy - py) < 1e-6) return; // server: zero-distance move

    if (d < bestD) {
      bestD = d;
      bestX = cx;
      bestY = cy;
    }
  }

  if (ptype === "knight") {
    const landingR = Math.sqrt(5.0) * Math.tan(freedomRad);
    for (const [adx, ady] of KNIGHT_OFFSETS) {
      const x = px + adx;
      const y = py + ady;
      if (x > 0.0 && x < 8.0 && y > 0.0 && y < 8.0) {
        const d = Math.hypot(bx - x, by - y);
        if (d <= landingR) return { x: bx, y: by, d: 0.0 };
        const edgeD = d - landingR;
        if (edgeD < bestD) {
          bestD = edgeD;
          // 0.99: land inside the circle so the server's <= r check passes.
          bestX = x + ((bx - x) / d) * landingR * 0.99;
          bestY = y + ((by - y) / d) * landingR * 0.99;
        }
      }
    }
  } else if (ptype === "pawn") {
    const fwd = owner === "white" ? -1.0 : 1.0;
    const maxFwd = piece.has_moved ? 1.0 : 2.0;
    trySector(0.0, fwd, Math.min(maxFwd, boardMax(0.0, fwd)));

    // Diagonal capture circles: only an enemy in range opens a valid arc.
    const diagR = SQRT2 * Math.tan(freedomRad);
    if (pieces !== null) {
      for (const xdir of [-1.0, 1.0]) {
        const ccx = px + xdir;
        const ccy = py + fwd;
        if (!(ccx >= 0.0 && ccx <= 8.0 && ccy >= 0.0 && ccy <= 8.0)) continue;
        const dToCenter = Math.hypot(bx - ccx, by - ccy);
        for (const other of pieces) {
          if (other.id === piece.id) continue;
          if (other.owner === owner) continue;
          const otherD = Math.hypot(other.x - ccx, other.y - ccy);
          if (otherD > diagR + DIAMETER_PIECE + 1e-6) continue;
          if (
            dToCenter <= diagR &&
            Math.hypot(bx - other.x, by - other.y) <= DIAMETER_PIECE
          ) {
            return { x: bx, y: by, d: 0.0 };
          }
          let alpha: number;
          if (otherD < 1e-9) {
            alpha = Math.PI;
          } else {
            const cosA =
              (diagR * diagR + otherD * otherD - DIAMETER_PIECE * DIAMETER_PIECE) /
              (2 * diagR * otherD);
            if (cosA >= 1.0) continue; // too far; no valid arc
            alpha = Math.acos(Math.max(-1.0, cosA));
          }
          const cAngle = Math.atan2(by - ccy, bx - ccx);
          const pAngle = Math.atan2(other.y - ccy, other.x - ccx);
          const delta =
            ((cAngle - pAngle + Math.PI) % (2 * Math.PI) + 2 * Math.PI) %
              (2 * Math.PI) - Math.PI;
          const snapAngle =
            Math.abs(delta) <= alpha
              ? cAngle
              : pAngle + Math.sign(delta) * alpha * 0.99;
          const sx = ccx + Math.cos(snapAngle) * diagR * 0.99;
          const sy = ccy + Math.sin(snapAngle) * diagR * 0.99;
          const d = Math.hypot(bx - sx, by - sy);
          if (d < bestD) {
            bestD = d;
            bestX = sx;
            bestY = sy;
          }
        }
      }
    }
  } else if (ptype === "king") {
    for (const dx of [1.0, -1.0]) {
      // Reach past one square is castling, which the server only allows when
      // an unmoved, idle rook is waiting on that side. Offering it otherwise
      // means the click is silently rejected.
      const horMax = canCastle(piece, dx, pieces) ? 2.0 : 1.0;
      trySector(dx, 0.0, Math.min(horMax, boardMax(dx, 0.0)));
    }
    for (const dy of [1.0, -1.0]) {
      trySector(0.0, dy, Math.min(1.0, boardMax(0.0, dy)));
    }
    const u = 1.0 / SQRT2;
    for (const [sdx, sdy] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      trySector(sdx * u, sdy * u, Math.min(SQRT2, boardMax(sdx * u, sdy * u)));
    }
  } else {
    const u = 1.0 / SQRT2;
    const ortho: [number, number][] = [[1, 0], [-1, 0], [0, 1], [0, -1]];
    const diag: [number, number][] = [[u, u], [u, -u], [-u, u], [-u, -u]];
    const dirs =
      ptype === "rook" ? ortho : ptype === "bishop" ? diag : ortho.concat(diag);
    for (const [dx, dy] of dirs) trySector(dx, dy, boardMax(dx, dy));
  }

  return { x: bestX, y: bestY, d: bestD };
}

/** Mirrors _check_castling in server/rules.py. */
export function canCastle(king: Piece, dx: number, pieces: Piece[] | null): boolean {
  if (king.has_moved || pieces === null) return false;
  const rookX = (dx > 0 ? 7 : 0) + 0.5;
  return pieces.some(
    (p) =>
      p.owner === king.owner &&
      p.type === "rook" &&
      !p.has_moved &&
      p.state === "idle" &&
      Math.abs(p.x - rookX) < 0.1 &&
      Math.abs(p.y - king.y) < 0.1,
  );
}

export function findPieceAt(
  bx: number,
  by: number,
  pieces: Piece[],
  radius: number,
): Piece | null {
  let best: Piece | null = null;
  let bestD = radius;
  for (const p of pieces) {
    if (p.type === "ghost") continue;
    const d = Math.hypot(p.x - bx, p.y - by);
    if (d < bestD) {
      bestD = d;
      best = p;
    }
  }
  return best;
}
