import { useEffect, useRef, useState } from "react";

export interface VerdictEvent {
  type: string;
  [k: string]: any;
}

/**
 * Subscribe to live verdict updates.
 *
 * A WebSocket is a connection the browser opens once and keeps open, so the
 * server can push a Ring 1 verdict the moment it resolves instead of the page
 * polling for it. Reconnects on its own if the connection drops.
 */
export function useVerdictStream(onEvent: (e: VerdictEvent) => void) {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      if (closed) return;
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/ws/verdicts`);

      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
      socket.onerror = () => socket?.close();
      socket.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === "ping" || data.type === "connected") return;
          handler.current(data);
        } catch {
          /* ignore malformed frames */
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, []);

  return connected;
}
