// Auto-detect environment at runtime based on hostname.
// This avoids issues with Vite env vars being baked incorrectly at build time.
const isLocal = typeof window !== 'undefined' && (
  window.location.hostname === 'localhost' ||
  window.location.hostname === '127.0.0.1'
);

const PROD_API = 'https://financial-market-data-aggregator.onrender.com';
const PROD_WS = 'wss://financial-market-data-aggregator.onrender.com';
const LOCAL_API = 'http://localhost:8000';
const LOCAL_WS = 'ws://localhost:8000';

export const API_BASE = isLocal ? LOCAL_API : PROD_API;
export const WS_URL = (isLocal ? LOCAL_WS : PROD_WS) + '/ws/live';

