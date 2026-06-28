import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { API_BASE } from '../lib/api.js';

const AuthContext = createContext(null);

function loadStoredSession() {
  const token = localStorage.getItem('fmda_token');
  const role = localStorage.getItem('fmda_role');
  const username = localStorage.getItem('fmda_username');
  return token && role && username ? { token, role, username } : null;
}

// This context only mirrors what the server already decided at login -- the
// role here drives which panels render, never what data they're allowed to
// contain. Masking and RBAC are enforced by the API (app/rbac.py,
// app/masking.py); a client could spoof this state in devtools and would
// still only get back what their JWT's verified role entitles them to.
export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(loadStoredSession);

  const login = useCallback((token, role, username) => {
    localStorage.setItem('fmda_token', token);
    localStorage.setItem('fmda_role', role);
    localStorage.setItem('fmda_username', username);
    setAuth({ token, role, username });
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('fmda_token');
    localStorage.removeItem('fmda_role');
    localStorage.removeItem('fmda_username');
    setAuth(null);
  }, []);

  const apiFetch = useCallback(
    async (path, opts = {}) => {
      const headers = Object.assign({}, opts.headers || {}, auth ? { Authorization: `Bearer ${auth.token}` } : {});
      const res = await fetch(API_BASE + path, { ...opts, headers });
      if (res.status === 401) logout();
      return res;
    },
    [auth, logout]
  );

  const value = useMemo(() => ({ auth, login, logout, apiFetch }), [auth, login, logout, apiFetch]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
