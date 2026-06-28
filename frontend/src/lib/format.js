export function fmt(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '--';
  const num = Number(n);
  return num >= 1000
    ? num.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : num.toFixed(num < 10 ? 4 : 2);
}

export function fmtMoney(n) {
  const num = Number(n) || 0;
  return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function fmtPct(n) {
  const num = Number(n) || 0;
  return (num >= 0 ? '+' : '') + num.toFixed(2) + '%';
}
