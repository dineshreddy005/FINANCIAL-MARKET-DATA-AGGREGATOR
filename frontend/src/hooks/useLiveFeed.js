import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE, WS_URL } from '../lib/api.js';

// Owns the live tape: connects to the ingestion WebSocket, seeds itself from
// the latest REST snapshot so the UI isn't empty on first paint, and
// auto-reconnects with a fixed backoff if the connection drops.
export function useLiveFeed() {
  const [prices, setPrices] = useState({}); // symbol -> { symbol, price, prevPrice, source, history }
  const [connected, setConnected] = useState(false);
  const [tickCount, setTickCount] = useState(0);
  const wsRef = useRef(null);
  const retryRef = useRef(null);

  const applyTick = useCallback((tick) => {
    setPrices((prev) => {
      const existing = prev[tick.symbol] || { history: [] };
      const price = Number(tick.price);
      const history = [...existing.history, price].slice(-40);
      return {
        ...prev,
        [tick.symbol]: {
          symbol: tick.symbol,
          price,
          prevPrice: existing.price,
          source: tick.source,
          history,
        },
      };
    });
    setTickCount((c) => c + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadInitial() {
      try {
        const res = await fetch(API_BASE + '/api/market/latest?limit=12');
        const rows = await res.json();
        if (!cancelled) rows.forEach(applyTick);
      } catch (e) {
        // Backend not reachable yet -- the live feed will populate once it is.
      }
    }
    loadInitial();

    function connect() {
      let ws;
      try {
        ws = new WebSocket(WS_URL);
      } catch (e) {
        return;
      }
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) retryRef.current = setTimeout(connect, 4000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (event) => {
        try {
          applyTick(JSON.parse(event.data));
        } catch (e) {
          // Ignore a malformed frame rather than tearing down the socket.
        }
      };
    }
    connect();

    return () => {
      cancelled = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [applyTick]);

  return { prices, connected, tickCount };
}
