"use client";

// 실시간 시세/체결 WebSocket (SPEC 11). 끊기면 지수 백오프로 자동 재연결하고 구독을 복구한다.
import { useCallback, useEffect, useRef, useState } from "react";

import { WS_BASE } from "@/lib/api";
import type { BookLevel, WsBook, WsQuote, WsStatusMsg } from "@/types";

export type ConnState = "connecting" | "open" | "closed";

function useReconnectingSocket(
  url: string | null,
  onMessage: (msg: unknown) => void,
  onOpen?: (ws: WebSocket) => void,
): ConnState {
  const [state, setState] = useState<ConnState>("connecting");
  const msgRef = useRef(onMessage);
  const openRef = useRef(onOpen);
  msgRef.current = onMessage;
  openRef.current = onOpen;

  useEffect(() => {
    if (!url) {
      setState("closed");
      return;
    }
    let ws: WebSocket | null = null;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const connect = () => {
      setState("connecting");
      ws = new WebSocket(url);
      ws.onopen = () => {
        attempt = 0;
        setState("open");
        openRef.current?.(ws as WebSocket);
      };
      ws.onmessage = (ev) => {
        try {
          msgRef.current(JSON.parse(ev.data));
        } catch {
          /* JSON 이 아닌 메시지는 무시 */
        }
      };
      ws.onclose = (ev) => {
        setState("closed");
        if (stopped || ev.code === 4401 || ev.code === 4404) return; // 인증 실패/계좌 없음은 재시도해도 같다
        const delay = Math.min(1000 * 2 ** attempt, 15_000);
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [url]);
  return state;
}

export interface LiveQuote {
  price: number;
  change_pct: number;
  volume: number;
  ts: string;
}

export interface LiveBook {
  asks: BookLevel[];
  bids: BookLevel[];
  ts: string;
}

/** 시세/호가 구독. codes 가 바뀌면 구독을 다시 맞춘다 (연결당 최대 30종목). */
export function useQuotes(codes: string[]) {
  const [quotes, setQuotes] = useState<Record<string, LiveQuote>>({});
  const [books, setBooks] = useState<Record<string, LiveBook>>({});
  const [status, setStatus] = useState<WsStatusMsg | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const subscribed = useRef<Set<string>>(new Set());
  const key = codes.slice(0, 30).sort().join(",");

  const onMessage = useCallback((m: unknown) => {
    const msg = m as WsQuote | WsBook | WsStatusMsg | { type: string };
    if (msg.type === "quote") {
      const q = msg as WsQuote;
      setQuotes((prev) => ({
        ...prev,
        [q.code]: { price: q.price, change_pct: q.change_pct, volume: q.volume, ts: q.ts },
      }));
    } else if (msg.type === "orderbook") {
      const b = msg as WsBook;
      setBooks((prev) => ({ ...prev, [b.code]: { asks: b.asks, bids: b.bids, ts: b.ts } }));
    } else if (msg.type === "status") {
      setStatus(msg as WsStatusMsg);
    }
  }, []);

  const sync = useCallback(
    (ws: WebSocket, fresh: boolean) => {
      const want = new Set(key ? key.split(",") : []);
      if (fresh) subscribed.current = new Set();
      const add = [...want].filter((c) => !subscribed.current.has(c));
      const remove = [...subscribed.current].filter((c) => !want.has(c));
      if (remove.length) ws.send(JSON.stringify({ op: "unsubscribe", codes: remove }));
      if (add.length) ws.send(JSON.stringify({ op: "subscribe", codes: add }));
      subscribed.current = want;
    },
    [key],
  );

  const conn = useReconnectingSocket(`${WS_BASE}/ws/quotes`, onMessage, (ws) => {
    wsRef.current = ws;
    sync(ws, true); // 재연결 시 구독 복구
  });

  useEffect(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) sync(ws, false);
  }, [sync]);

  return { quotes, books, status, conn };
}

/** 체결/주문 이벤트와 평가금액 (WS /ws/paper). */
export function usePaperSocket(token: string | null, onEvent: (msg: { type: string; [k: string]: unknown }) => void) {
  const url = token ? `${WS_BASE}/ws/paper?token=${encodeURIComponent(token)}` : null;
  return useReconnectingSocket(url, (m) => onEvent(m as { type: string }));
}
