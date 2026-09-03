/**
 * Lobby + game connection. One socket lives from the menu through the game,
 * so this outlives any single screen.
 */

import * as P from "./protocol.ts";

type Handler = (msg: any) => void;

export class Net {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Handler[]>();
  private pingTimer: number | null = null;

  rtt = 0;
  code: string | null = null;
  color: string | null = null;
  token: string | null = null;

  constructor(public url: string) {}

  on(type: string, fn: Handler): void {
    const list = this.handlers.get(type) ?? [];
    list.push(fn);
    this.handlers.set(type, list);
  }

  private emit(type: string, msg: any): void {
    for (const fn of this.handlers.get(type) ?? []) fn(msg);
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url);
      this.ws = ws;
      ws.onopen = () => {
        this.startPings();
        resolve();
      };
      ws.onerror = () => reject(new Error(`cannot reach ${this.url}`));
      ws.onclose = () => {
        this.stopPings();
        this.emit("close", {});
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === P.PONG) {
          this.rtt = Math.round(performance.now() - msg.t);
          return;
        }
        if (msg.type === P.SERVER_HELLO) {
          // A cached bundle can outlive the server it was built against.
          if (msg.version !== P.VERSION) this.emit("version-mismatch", msg);
          return;
        }
        if (msg.type === P.ROOM_CREATED || msg.type === P.ROOM_JOINED) {
          this.code = msg.code;
          this.color = msg.color;
          this.token = msg.token;
          // Survive a reload: the seat can be reclaimed within the grace
          // window. Solo rides along, because after a reload the client has to
          // know it still owns both seats or it can only move white. Only
          // ROOM_CREATED carries the flag, so a rejoin to the same room keeps
          // what was stored.
          const prior = JSON.parse(sessionStorage.getItem("seat") ?? "null");
          const solo = msg.solo ?? (prior?.code === msg.code ? !!prior.solo : false);
          sessionStorage.setItem("seat", JSON.stringify(
            { code: msg.code, token: msg.token, solo }));
        }
        this.emit(msg.type, msg);
      };
    });
  }

  isClosed(): boolean {
    return !this.ws || this.ws.readyState > WebSocket.OPEN;
  }

  send(msg: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private startPings(): void {
    this.pingTimer = window.setInterval(
      () => this.send({ type: P.PING, t: performance.now() }),
      2000,
    );
  }

  private stopPings(): void {
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  createRoom(params: object, solo = false, view?: object): void {
    this.send({ type: P.CREATE_ROOM, params, solo, view });
  }

  joinRoom(code: string): void {
    this.send({ type: P.JOIN_ROOM, code: code.trim().toUpperCase() });
  }

  quickMatch(params: object, view?: object): void {
    this.send({ type: P.QUICK_MATCH, params, view });
  }

  rejoin(code: string, token: string): void {
    this.send({ type: P.REJOIN, code, token });
  }

  queueMove(pieceId: string, x: number, y: number): void {
    this.send({ type: P.QUEUE_MOVE, piece_id: pieceId, destination: [x, y] });
  }
}
