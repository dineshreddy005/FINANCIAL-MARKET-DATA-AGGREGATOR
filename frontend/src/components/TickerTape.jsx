import { useMemo } from 'react';
import { fmt } from '../lib/format.js';

export default function TickerTape({ prices }) {
  const items = useMemo(() => Object.values(prices), [prices]);

  if (!items.length) {
    return (
      <div className="tickertape">
        <div className="tickertape__track">
          <span className="tickertape__item tickertape__item--muted">Waiting for the first tick…</span>
        </div>
      </div>
    );
  }

  // Rendered twice back-to-back so the CSS marquee can loop seamlessly.
  const renderItems = (keyPrefix) =>
    items.map((it, i) => {
      const dir = it.prevPrice == null ? 'flat' : it.price > it.prevPrice ? 'up' : it.price < it.prevPrice ? 'down' : 'flat';
      return (
        <span className="tickertape__item" key={`${keyPrefix}-${it.symbol}-${i}`}>
          <span className="tickertape__sym">{it.symbol}</span>
          <span className={`tickertape__price tickertape__price--${dir}`}>
            {fmt(it.price)} {dir === 'up' ? '▲' : dir === 'down' ? '▼' : '·'}
          </span>
        </span>
      );
    });

  return (
    <div className="tickertape">
      <div className="tickertape__track">
        {renderItems('a')}
        {renderItems('b')}
      </div>
    </div>
  );
}
