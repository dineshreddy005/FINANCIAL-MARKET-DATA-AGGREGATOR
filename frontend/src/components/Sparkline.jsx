export default function Sparkline({ data, width = 160, height = 44, positive = true }) {
  if (!data || data.length < 2) {
    return <svg width={width} height={height} className="sparkline" aria-hidden="true" />;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);

  const points = data
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / span) * (height - 6) - 3;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const areaPoints = `0,${height} ${points} ${width},${height}`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="sparkline" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={areaPoints} className={`sparkline__area ${positive ? 'sparkline__area--up' : 'sparkline__area--down'}`} />
      <polyline points={points} fill="none" className={`sparkline__line ${positive ? 'sparkline__line--up' : 'sparkline__line--down'}`} />
    </svg>
  );
}
