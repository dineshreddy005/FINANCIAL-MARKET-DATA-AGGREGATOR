import { useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext.jsx';

function useClock() {
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return time;
}

export default function Masthead() {
  const { auth, logout } = useAuth();
  const time = useClock();
  const hh = String(time.getUTCHours()).padStart(2, '0');
  const mm = String(time.getUTCMinutes()).padStart(2, '0');
  const ss = String(time.getUTCSeconds()).padStart(2, '0');

  return (
    <header className="masthead">
      <div className="masthead__brand">
        FMDA
        <span className="masthead__brand-sub">Financial Market Data Aggregator</span>
      </div>
      <div className="masthead__right">
        <span className="masthead__user">{auth.username}</span>
        <span className={`role-pill role-pill--${auth.role}`}>{auth.role}</span>
        <span className="masthead__clock">
          {hh}:{mm}:{ss} UTC
        </span>
        <button className="link-btn" onClick={logout}>
          Sign out
        </button>
      </div>
    </header>
  );
}
