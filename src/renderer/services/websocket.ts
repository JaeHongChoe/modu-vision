/**
 * src/renderer/services/websocket.ts
 * Asynchronous WebSocket client for real-time training telemetry and hardware monitoring.
 */

import { getBackendPort } from './api';

type MessageHandler = (event: string, data: any) => void;

class WebSocketTelemetryService {
  private ws: WebSocket | null = null;
  private listeners: Set<MessageHandler> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingInterval: ReturnType<typeof setInterval> | null = null;
  private isConnected = false;
  private port = 8000;

  public async connect(): Promise<void> {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      this.port = await getBackendPort();
      const wsUrl = `ws://127.0.0.1:${this.port}/ws/telemetry`;
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.isConnected = true;
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }

        // Start keepalive ping every 10s
        if (this.pingInterval) clearInterval(this.pingInterval);
        this.pingInterval = setInterval(() => {
          if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            try {
              this.ws.send('ping');
            } catch {
              // ignore
            }
          }
        }, 10000);
      };

      this.ws.onmessage = (event) => {
        if (event.data === 'pong') return;
        try {
          const payload = JSON.parse(event.data);
          const eventType = payload.event || payload.type || 'message';
          const eventData = payload.data || payload;
          for (const listener of this.listeners) {
            listener(eventType, eventData);
          }
        } catch {
          // ignore keepalive pings or non-json
        }
      };

      this.ws.onclose = () => {
        this.isConnected = false;
        this.cleanPing();
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.isConnected = false;
      };
    } catch {
      this.scheduleReconnect();
    }
  }

  private cleanPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 2000);
  }

  public subscribe(handler: MessageHandler): () => void {
    this.listeners.add(handler);
    return () => {
      this.listeners.delete(handler);
    };
  }

  public disconnect(): void {
    this.cleanPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.isConnected = false;
  }

  public getStatus(): boolean {
    return this.isConnected;
  }
}

export const telemetryService = new WebSocketTelemetryService();
