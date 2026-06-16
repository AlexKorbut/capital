import { useEffect, useRef } from "react";
import { useAuth } from "@/store/auth";

export interface PortfolioEvent {
  type: string;
  [key: string]: unknown;
}

/**
 * Subscribe to the authenticated /ws/portfolio push channel.
 * Reconnects on drop while a token is present. Calls `onEvent` per message.
 */
export function usePortfolioSocket(onEvent: (e: PortfolioEvent) => void): void {
  const token = useAuth((s) => s.accessToken);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    if (!token) return;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let ws: WebSocket | undefined;

    const connect = () => {
      if (closed) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(
        `${proto}://${window.location.host}/api/v1/ws/portfolio?token=${token}`,
      );
      ws.onmessage = (ev) => {
        try {
          handlerRef.current(JSON.parse(ev.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (!closed) retry = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [token]);
}
