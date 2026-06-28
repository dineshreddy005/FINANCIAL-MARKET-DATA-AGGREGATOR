import { useCallback, useState } from 'react';
import { useAuth } from '../context/AuthContext.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { usePolling } from '../hooks/usePolling.js';

function DiffFields({ oldData, newData }) {
  if (!oldData) return <span className="diff-line diff-new">new record created</span>;
  if (!newData) return <span className="diff-line diff-old">record removed</span>;

  const keys = new Set([...Object.keys(oldData), ...Object.keys(newData)]);
  const lines = [];
  keys.forEach((k) => {
    if (k === 'updated_at' || k === 'ingested_at') return;
    if (JSON.stringify(oldData[k]) !== JSON.stringify(newData[k])) {
      lines.push(
        <span className="diff-line" key={k}>
          <b>{k}</b>: <span className="diff-old">{String(oldData[k])}</span> → <span className="diff-new">{String(newData[k])}</span>
        </span>
      );
    }
  });
  return lines.length ? <>{lines}</> : <span className="diff-line">(no field-level change)</span>;
}

export default function RegistryPanel() {
  const { apiFetch } = useAuth();
  const toast = useToast();
  const [accountId, setAccountId] = useState('');
  const [balance, setBalance] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const fetchLogs = useCallback(async () => {
    const res = await apiFetch('/api/audit/logs?limit=30');
    if (!res.ok) throw new Error('failed to load audit logs');
    return res.json();
  }, [apiFetch]);
  const { data: logs, refresh } = usePolling(fetchLogs, 15000);

  async function submitAdjustment(e) {
    e.preventDefault();
    if (!accountId || !balance || !reason.trim()) {
      toast('Fill in account id, new balance, and a reason.', 'err');
      return;
    }
    setSubmitting(true);
    try {
      const res = await apiFetch(`/api/accounts/${accountId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cash_balance: Number(balance), reason: reason.trim() }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast(data.detail || 'Adjustment failed', 'err');
        return;
      }
      toast(`Account ${accountId} adjusted by ${data.adjusted_by} — logged to audit_logs.`, 'ok');
      setBalance('');
      setReason('');
      setTimeout(refresh, 400);
    } catch (e) {
      toast('Network error during adjustment.', 'err');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section id="registry" className="panel-section">
      <h2 className="eyebrow">
        Registry <span className="eyebrow__hint">immutable audit trail</span>
      </h2>
      <div className="card">
        <div className="table-scroll">
          <table className="audit-table">
            <thead>
              <tr>
                <th>Time (UTC)</th>
                <th>Table</th>
                <th>Op</th>
                <th>Actor</th>
                <th>Changed fields</th>
              </tr>
            </thead>
            <tbody>
              {!logs && (
                <tr>
                  <td colSpan={5} className="empty-note">
                    Loading…
                  </td>
                </tr>
              )}
              {logs && logs.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty-note">
                    No audit events yet — upload a revised batch, or run an adjustment below.
                  </td>
                </tr>
              )}
              {logs &&
                logs.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.changed_at.replace('T', ' ').slice(0, 19)}</td>
                    <td>{r.table_name}</td>
                    <td>
                      <span className={`op-tag op-tag--${r.operation}`}>{r.operation}</span>
                    </td>
                    <td className="mono">{r.changed_by}</td>
                    <td>
                      <DiffFields oldData={r.old_data} newData={r.new_data} />
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>

        <form className="adjust-form" onSubmit={submitAdjustment}>
          <label className="field">
            <span>Account ID</span>
            <input type="number" value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder="1" />
          </label>
          <label className="field">
            <span>New cash balance</span>
            <input type="number" step="0.01" value={balance} onChange={(e) => setBalance(e.target.value)} placeholder="25000.00" />
          </label>
          <label className="field">
            <span>Compliance reason</span>
            <input type="text" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="manual correction" />
          </label>
          <button className="btn btn--primary" disabled={submitting}>
            {submitting ? 'Logging…' : 'Adjust & log'}
          </button>
        </form>
      </div>
    </section>
  );
}
