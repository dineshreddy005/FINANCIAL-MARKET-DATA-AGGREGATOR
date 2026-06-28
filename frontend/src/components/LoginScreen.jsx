import { useState } from 'react';
import { useAuth } from '../context/AuthContext.jsx';
import { API_BASE } from '../lib/api.js';

const DEMO_USERS = [
  { user: 'admin', pass: 'admin123', label: 'Admin desk' },
  { user: 'jane.c', pass: 'client123', label: 'Client view' },
  { user: 'ingest-svc', pass: 'service123', label: 'Service feed' },
];

export default function LoginScreen() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(user, pass) {
    setError('');
    setBusy(true);
    try {
      const form = new URLSearchParams();
      form.set('username', user);
      form.set('password', pass);
      const res = await fetch(API_BASE + '/auth/login', { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || 'Sign-in failed');
        return;
      }
      login(data.access_token, data.role, data.username);
    } catch (e) {
      setError(`Can't reach the API at ${API_BASE}`);
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e) {
    e.preventDefault();
    submit(username.trim(), password);
  }

  return (
    <div className="login-stage">
      <div className="login-card">
        <div className="login-card__mark">FMDA</div>
        <p className="login-card__tagline">Financial Market Data Aggregator — sign in to the desk</p>

        <form onSubmit={onSubmit} className="login-form">
          <label className="field">
            <span>Username</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" autoComplete="username" />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
            />
          </label>
          {error && <p className="login-error">{error}</p>}
          <button className="btn btn--primary btn--block" disabled={busy}>
            {busy ? 'Signing in…' : 'Enter the desk'}
          </button>
        </form>

        <div className="login-demo">
          <span className="login-demo__label">Quick demo seats</span>
          <div className="login-demo__row">
            {DEMO_USERS.map((d) => (
              <button
                key={d.user}
                type="button"
                className="chip"
                onClick={() => {
                  setUsername(d.user);
                  setPassword(d.pass);
                  submit(d.user, d.pass);
                }}
              >
                {d.label}
              </button>
            ))}
          </div>
          <p className="login-demo__hint">
            Seeded in sql/seed.sql — RBAC and field masking are enforced server-side, so the same request returns
            different data depending on which seat you sign in with.
          </p>
        </div>
      </div>
    </div>
  );
}
