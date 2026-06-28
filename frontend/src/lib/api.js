// Vite only exposes import.meta.env.VITE_* to client code -- see .env.example.
// Defaults match the backend's docker-compose port mapping so the app works
// out of the box with zero configuration.
const PROD_API = 'https://financial-market-data-aggregatorfmda.onrender.com';
const PROD_WS  = 'wss://financial-market-data-aggregatorfmda.onrender.com';

export const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.PROD ? PROD_API : 'http://localhost:8000');
export const WS_URL = (import.meta.env.VITE_WS_BASE || (import.meta.env.PROD ? PROD_WS : 'ws://localhost:8000')) + '/ws/live';

