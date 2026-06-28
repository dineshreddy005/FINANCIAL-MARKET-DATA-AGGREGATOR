import { useCallback, useState } from 'react';
import { useAuth } from '../context/AuthContext.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { usePolling } from '../hooks/usePolling.js';

export default function DeskPanel() {
  const { apiFetch } = useAuth();
  const toast = useToast();
  const [log, setLog] = useState([]);

  const fetchBreakers = useCallback(async () => {
    const res = await apiFetch('/api/market/circuit-status');
    if (!res.ok) throw new Error('failed to load breaker status');
    return res.json();
  }, [apiFetch]);
  const { data: breakers } = usePolling(fetchBreakers, 10000);

  function appendLog(text, ok) {
    setLog((l) => [{ text, ok, id: Date.now() + Math.random() }, ...l].slice(0, 20));
  }

  async function handleFile(file) {
    if (!file) return;
    appendLog(`uploading ${file.name}…`, true);
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await apiFetch('/api/ingest/batch', { method: 'POST', body: form });
      const data = await res.json();
      if (res.ok) {
        appendLog(`${file.name}: ${data.rows_inserted} inserted, ${data.rows_updated} updated, ${data.rows_deduped} deduped`, true);
        toast('Batch ingested — check the Registry for any overwritten rows.', 'ok');
      } else {
        appendLog(`${file.name}: ${data.detail || 'upload failed'}`, false);
        toast(data.detail || 'Upload failed', 'err');
      }
    } catch (e) {
      appendLog(`${file.name}: network error`, false);
    }
  }

  function onDrop(e) {
    e.preventDefault();
    if (e.dataTransfer.files?.length) handleFile(e.dataTransfer.files[0]);
  }

  const breakerEntries = breakers ? Object.entries(breakers) : [['coingecko', 'CLOSED'], ['yfinance', 'CLOSED']];

  return (
    <section id="desk" className="panel-section">
      <h2 className="eyebrow">
        Desk <span className="eyebrow__hint">ingestion &amp; resilience</span>
      </h2>
      <div className="panel-pair">
        <div className="card">
          <h3 className="card__title">EOD batch upload</h3>
          <label className="dropzone" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
            Drop a .csv / .json file, or click to choose
            <span className="dropzone__hint">idempotent pipeline — safe to re-upload · requires admin or service role</span>
            <input type="file" accept=".csv,.json" onChange={(e) => handleFile(e.target.files[0])} hidden />
          </label>
          <ul className="upload-log">
            {log.map((l) => (
              <li key={l.id} className={l.ok ? 'ok' : 'err'}>
                {l.text}
              </li>
            ))}
          </ul>
        </div>
        <div className="card">
          <h3 className="card__title">Circuit breakers</h3>
          <div className="breaker-list">
            {breakerEntries.map(([provider, st]) => (
              <div className="breaker-row" key={provider}>
                <span>{provider}</span>
                <span className={`state-pill state-pill--${st}`}>{st}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
