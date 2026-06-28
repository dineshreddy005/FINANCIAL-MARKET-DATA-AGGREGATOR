import { fmt, fmtPct } from '../lib/format.js';
import FlipNumber from './FlipNumber.jsx';
import Sparkline from './Sparkline.jsx';

export default function BlotterPanel({ prices }) {
  const items = Object.values(prices).sort((a, b) => a.symbol.localeCompare(b.symbol));

  return (
    <section id="blotter" className="panel-section">
      <h2 className="eyebrow">
        Blotter <span className="eyebrow__hint">live tape · {items.length} instruments</span>
      </h2>
      {items.length === 0 ? (
        <p className="empty-note">Waiting on the first tick from the live feed…</p>
      ) : (
        <div className="blotter-grid">
          {items.map((it) => {
            const dir = it.prevPrice == null ? 'flat' : it.price > it.prevPrice ? 'up' : it.price < it.prevPrice ? 'down' : 'flat';
            const pct = it.prevPrice ? ((it.price - it.prevPrice) / it.prevPrice) * 100 : 0;
            return (
              <article className={`price-card price-card--${dir}`} key={it.symbol}>
                <div className="price-card__top">
                  <span className="price-card__sym">{it.symbol}</span>
                  <span className="price-card__src">{it.source}</span>
                </div>
                <FlipNumber value={fmt(it.price)} className={`price-card__value price-card__value--${dir}`} />
                {it.prevPrice != null ? (
                  <span className={`price-card__delta price-card__delta--${dir}`}>{fmtPct(pct)}</span>
                ) : (
                  <span className="price-card__delta price-card__delta--flat">first tick</span>
                )}
                <Sparkline data={it.history} positive={dir !== 'down'} />
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
