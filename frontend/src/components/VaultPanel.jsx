import { useCallback, useState } from 'react';
import { useAuth } from '../context/AuthContext.jsx';
import { usePolling } from '../hooks/usePolling.js';
import { API_BASE } from '../lib/api.js';

export default function VaultPanel() {
  const { apiFetch } = useAuth();
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const fetchStats = useCallback(async () => {
    const res = await apiFetch('/api/market/cache-stats');
    if (!res.ok) throw new Error('failed to load cache stats');
    return res.json();
  }, [apiFetch]);

  const { data } = usePolling(fetchStats, 8000);
  const ratioPct = data ? Math.round(data.hit_ratio * 100) : 0;

  async function runTest() {
    setTesting(true);
    try {
      const r1 = await fetch(API_BASE + '/api/market/profile/AAPL');
      const r2 = await fetch(API_BASE + '/api/market/profile/AAPL');
      setTestResult({ first: r1.headers.get('X-Cache') || '?', second: r2.headers.get('X-Cache') || '?' });
    } catch (e) {
      setTestResult({ error: true });
    } finally {
      setTesting(false);
    }
  }

  return (
    <section id="vault" className="panel-section">
      <h2 className="eyebrow">
        Vault <span className="eyebrow__hint">Redis cache-aside</span>
      </h2>
      <div className="panel-pair">
        <div className="card">
          <h3 className="card__title">Hit ratio</h3>
          <div className="meter">
            <div className="meter__fill" style={{ width: `${ratioPct}%` }} />
          </div>
          <div className="meter-stats">
            <span>hits: {data?.hits ?? 0}</span>
            <span>misses: {data?.misses ?? 0}</span>
            <span>ratio: {ratioPct}%</span>
          </div>
        </div>
        <div className="card">
          <h3 className="card__title">
            Live demo <span className="card__hint">X-Cache header</span>
          </h3>
          <p className="empty-note">
            Calls <code>/api/market/profile/AAPL</code> twice in a row.
          </p>
          <button className="btn" onClick={runTest} disabled={testing}>
            {testing ? 'Running…' : 'Run cache test'}
          </button>
          {testResult && !testResult.error && (
            <div className="cache-test-result">
              <div>
                Call 1 → <span className={`xcache-tag xcache-tag--${testResult.first}`}>{testResult.first}</span> (queried Postgres,
                then cached)
              </div>
              <div>
                Call 2 → <span className={`xcache-tag xcache-tag--${testResult.second}`}>{testResult.second}</span> (served from Redis)
              </div>
            </div>
          )}
          {testResult?.error && <p className="empty-note">Network error running the cache test.</p>}
        </div>
      </div>
    </section>
  );
}
