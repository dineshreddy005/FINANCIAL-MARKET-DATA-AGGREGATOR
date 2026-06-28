import { useCallback } from 'react';
import { useAuth } from '../context/AuthContext.jsx';
import { usePolling } from '../hooks/usePolling.js';
import { fmt } from '../lib/format.js';

export default function AnomalyRadar() {
  const { apiFetch } = useAuth();

  const fetchAnomalies = useCallback(async () => {
    const res = await apiFetch('/api/ai/anomalies');
    if (!res.ok) throw new Error('failed to load anomalies');
    return res.json();
  }, [apiFetch]);

  const { data, error } = usePolling(fetchAnomalies, 30000);

  return (
    <div className="card">
      <h3 className="card__title">
        Anomaly radar <span className="card__hint">rolling z-score</span>
      </h3>
      {error && <p className="empty-note">Could not load the anomaly scan.</p>}
      {!error && !data && <p className="empty-note">Scanning recent ticks…</p>}
      {!error && data && data.flagged.length === 0 && (
        <p className="empty-note">
          No statistical outliers in the last {data.window_hours}h (|z| ≥ {data.z_threshold}).
        </p>
      )}
      {!error && data && data.flagged.length > 0 && (
        <ul className="anomaly-list">
          {data.flagged.map((a) => (
            <li key={a.symbol} className={`anomaly-item anomaly-item--${a.direction}`}>
              <span>
                {a.symbol} {a.direction === 'spike_up' ? '▲' : '▼'}
              </span>
              <span className="mono">
                z={a.z_score} · {fmt(a.latest_price)} vs {fmt(a.rolling_mean)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
