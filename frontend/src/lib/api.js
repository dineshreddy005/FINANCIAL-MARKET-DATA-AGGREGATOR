// Vite only exposes import.meta.env.VITE_* to client code -- see .env.example.
// Defaults match the backend's docker-compose port mapping so the app works
// out of the box with zero configuration.
export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
export const WS_URL = (import.meta.env.VITE_WS_BASE || 'ws://localhost:8000') + '/ws/live';
