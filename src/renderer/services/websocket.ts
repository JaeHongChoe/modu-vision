/**
 * src/renderer/services/websocket.ts
 * Asynchronous WebSocket client for real-time training telemetry and hardware monitoring.
 */

import { getApiBaseUrl, getProjectContext, subscribeProjectContext } from './api';

type MessageHandler = (event: string, data: any) => void;

class WebSocketTelemetryService {
  private ws: WebSocket | null = null;
  private listeners: Set<MessageHandler> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingInterval: ReturnType<typeof setInterval> | null = null;
  private isConnected = false;
  private enabled = false;
  private connectionEpoch = 0;
  private contextSubscription: (() => void) | null = null;

  public async connect(): Promise<void> {
    this.enabled = true;
    this.contextSubscription ??= subscribeProjectContext(() => {
      this.retireSocket();
      void this.openCommittedContext();
    });
    await this.openCommittedContext();
  }

  private async openCommittedContext(): Promise<void> {
    const context = getProjectContext();
    // App mounts before project discovery. Connect when the UI commits the
    // authoritative selection, so the handshake cannot bind an arbitrary one.
    if (!this.enabled || !context) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const epoch = ++this.connectionEpoch;
    try {
      const base=await getApiBaseUrl();
      if (!this.enabled || epoch !== this.connectionEpoch) return;
      const wsUrl = `${base.replace(/^http/,'ws')}/ws/telemetry?${new URLSearchParams({project_context:JSON.stringify(context)})}`;
      const socket = new WebSocket(wsUrl);
      this.ws = socket;
      const current = () => this.enabled && this.ws === socket && this.connectionEpoch === epoch;

      socket.onopen = () => {
        if (!current()) return;
        this.isConnected = true;
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }

        // Messages sent while the socket was down are lost; listeners catch up from the job event cursor (S1-10).
        for (const listener of this.listeners) listener('telemetry_connected', {});

        // Start keepalive ping every 10s
        if (this.pingInterval) clearInterval(this.pingInterval);
        this.pingInterval = setInterval(() => {
          if (current() && socket.readyState === WebSocket.OPEN) {
            try {
              socket.send('ping');
            } catch {
              // ignore
            }
          }
        }, 10000);
      };

      socket.onmessage = (event) => {
        if (!current()) return;
        if (event.data === 'pong') return;
        try {
          const payload = JSON.parse(event.data);
          const origin = payload.project_context;
          if (origin && (origin.workspace_id !== context.workspace_id || origin.project_id !== context.project_id)) return;
          const eventType = payload.event || payload.type || 'message';
          const eventData = payload.data || payload;
          for (const listener of this.listeners) {
            listener(eventType, eventData);
          }
        } catch {
          // ignore keepalive pings or non-json
        }
      };

      socket.onclose = () => {
        if (!current()) return;
        this.ws = null;
        this.isConnected = false;
        this.cleanPing();
        this.scheduleReconnect();
      };

      socket.onerror = () => {
        if (!current()) return;
        this.isConnected = false;
      };
    } catch {
      if (this.enabled && epoch === this.connectionEpoch) this.scheduleReconnect();
    }
  }

  private cleanPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  private scheduleReconnect(): void {
    if (!this.enabled || !getProjectContext() || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.openCommittedContext();
    }, 2000);
  }

  public subscribe(handler: MessageHandler): () => void {
    this.listeners.add(handler);
    return () => {
      this.listeners.delete(handler);
    };
  }

  public disconnect(): void {
    this.enabled = false;
    this.contextSubscription?.();
    this.contextSubscription = null;
    this.retireSocket();
  }

  private retireSocket(): void {
    this.connectionEpoch++;
    this.cleanPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      const socket = this.ws;
      this.ws = null;
      socket.close();
    }
    this.isConnected = false;
  }

  public getStatus(): boolean {
    return this.isConnected;
  }
}

export const telemetryService = new WebSocketTelemetryService();
