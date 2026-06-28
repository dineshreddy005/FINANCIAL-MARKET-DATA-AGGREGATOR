import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext.jsx';
import AnomalyRadar from './AnomalyRadar.jsx';

const SYMBOLS = ['AAPL', 'MSFT', 'TSLA', 'BTC', 'ETH', 'SOL'];

function useTypewriter(text) {
  const [shown, setShown] = useState('');
  useEffect(() => {
    if (!text) {
      setShown('');
      return undefined;
    }
    setShown('');
    let i = 0;
    const speed = Math.max(6, Math.min(18, 1800 / text.length));
    const id = setInterval(() => {
      i += 1;
      setShown(text.slice(0, i));
      if (i >= text.length) clearInterval(id);
    }, speed);
    return () => clearInterval(id);
  }, [text]);
  return shown;
}

export default function AnalystPanel() {
  const { apiFetch } = useAuth();
  const [symbol, setSymbol] = useState('AAPL');
  const [insight, setInsight] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const typed = useTypewriter(insight?.narrative);

  const generate = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await apiFetch(`/api/ai/insights/${symbol}`);
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || 'No insight available yet — try again once a few ticks have arrived.');
        setInsight(null);
        return;
      }
      setInsight(data);
    } catch (e) {
      setError('Network error generating insight.');
    } finally {
      setLoading(false);
    }
  }, [apiFetch, symbol]);

  return (
    <section id="analyst" className="panel-section">
      <h2 className="eyebrow">
        Analyst <span className="eyebrow__hint">AI-grounded commentary</span>
      </h2>
      <div className="panel-pair">
        <div className="card">
          <div className="symbol-chips">
            {SYMBOLS.map((s) => (
              <button key={s} type="button" className={`chip ${symbol === s ? 'chip--active' : ''}`} onClick={() => setSymbol(s)}>
                {s}
              </button>
            ))}
          </div>
          <button className="btn btn--primary" onClick={generate} disabled={loading}>
            {loading ? 'Generating…' : 'Generate insight'}
          </button>
          <div className="ai-narrative">
            {error
              ? error
              : insight
              ? typed
              : 'Pick a symbol and generate an insight — grounded in your own ingested ticks, with an optional live-LLM upgrade.'}
            {insight && typed.length < insight.narrative.length && <span className="cursor-blink" />}
          </div>
          {insight && (
            <div className="ai-meta">
              <span className={`source-tag source-tag--${insight.source}`}>{insight.source}</span>
              <span>{insight.cache_hit ? 'served from cache' : 'freshly computed'}</span>
              <span>
                · {insight.stats.window_hours}h window · {insight.stats.sample_count} samples
              </span>
            </div>
          )}
        </div>
        <AnomalyRadar />
      </div>
    </section>
  );
}
