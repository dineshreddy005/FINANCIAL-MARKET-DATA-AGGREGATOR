import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext.jsx';
import { fmt, fmtMoney } from '../lib/format.js';

export default function LedgerPanel({ prices }) {
  const { auth, apiFetch } = useAuth();
  const [account, setAccount] = useState(null);
  const [status, setStatus] = useState('loading'); // loading | ok | none | error

  const load = useCallback(async () => {
    setStatus('loading');
    try {
      const res = await apiFetch('/api/accounts/me');
      if (res.status === 404) {
        setStatus('none');
        return;
      }
      if (!res.ok) {
        setStatus('error');
        return;
      }
      setAccount(await res.json());
      setStatus('ok');
    } catch (e) {
      setStatus('error');
    }
  }, [apiFetch]);

  useEffect(() => {
    load();
  }, [load]);

  const isMasked = account && String(account.account_number).includes('*');

  return (
    <section id="ledger" className="panel-section">
      <h2 className="eyebrow">
        Ledger <span className="eyebrow__hint">zero-trust field masking</span>
      </h2>
      <div className="ledger-card">
        {status === 'loading' && <p className="empty-note">Fetching account…</p>}
        {status === 'none' && <p className="empty-note">No brokerage account on file for this login — expected for the service seat.</p>}
        {status === 'error' && <p className="empty-note">Couldn't load the account.</p>}

        {status === 'ok' && account && (
          <>
            <span className={`stamp ${isMasked ? 'stamp--masked' : 'stamp--full'}`}>
              {isMasked ? 'masked · last 4 only' : 'unrestricted · admin'}
            </span>

            <dl className="ledger-grid">
              <div>
                <dt>Holder</dt>
                <dd>{account.full_name}</dd>
              </div>
              <div>
                <dt>Broker</dt>
                <dd>{account.broker_name}</dd>
              </div>
              <div>
                <dt>Account #</dt>
                <dd className={`mono ${isMasked ? 'masked-text' : ''}`}>{account.account_number}</dd>
              </div>
              <div>
                <dt>Routing #</dt>
                <dd className={`mono ${isMasked ? 'masked-text' : ''}`}>{account.routing_number || '—'}</dd>
              </div>
              <div>
                <dt>Type</dt>
                <dd>{account.account_type}</dd>
              </div>
              <div>
                <dt>Cash balance</dt>
                <dd className="mono">${fmtMoney(account.cash_balance)}</dd>
              </div>
            </dl>

            {account.holdings && account.holdings.length > 0 && (
              <table className="holdings-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Qty</th>
                    <th>Avg cost</th>
                    <th>Mark</th>
                    <th>Unrealized</th>
                  </tr>
                </thead>
                <tbody>
                  {account.holdings.map((h) => {
                    const live = prices[h.symbol]?.price;
                    const pl = live != null ? (live - h.avg_cost) * h.quantity : null;
                    return (
                      <tr key={h.symbol}>
                        <td>{h.symbol}</td>
                        <td className="mono">{h.quantity}</td>
                        <td className="mono">{Number(h.avg_cost).toFixed(2)}</td>
                        <td className="mono">{live != null ? fmt(live) : '—'}</td>
                        <td className={`mono ${pl == null ? '' : pl >= 0 ? 'gain' : 'loss'}`}>
                          {pl == null ? '—' : `${pl >= 0 ? '+' : '-'}$${Math.abs(pl).toFixed(2)}`}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}

            <p className="lock-note">
              {isMasked
                ? 'account_number and routing_number are redacted to the last four digits by the API itself — the unmasked value never leaves the server for this role.'
                : `Signed in as ${auth.role}, so identifiers are returned unmasked. Sign in as jane.c to see the masked view of the same data.`}
            </p>
          </>
        )}
      </div>
    </section>
  );
}
