interface BpPoint {
  date: string;
  sys: number;
  dia: number;
}

export function Sparkline({ points, goalSys = 135 }: { points: BpPoint[]; goalSys?: number }) {
  if (!points || points.length < 2) return null;
  const width = 340;
  const height = 74;
  const padX = 8;
  const padY = 10;
  const minVal = 70;
  const maxVal = 175;
  const xOf = (i: number) => padX + (i / (points.length - 1)) * (width - padX * 2);
  const yOf = (v: number) => height - padY - ((v - minVal) / (maxVal - minVal)) * (height - padY * 2);

  const sysPath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xOf(i).toFixed(1)} ${yOf(p.sys).toFixed(1)}`).join(" ");
  const diaPath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xOf(i).toFixed(1)} ${yOf(p.dia).toFixed(1)}`).join(" ");
  const goalY = yOf(goalSys);
  const last = points[points.length - 1];

  return (
    <div className="v2-card-flat" data-testid="bp-sparkline">
      <div className="v2-order-head">
        <span className="v2-sec-label">30-day home BP · goal &lt; 135/85</span>
        <span className="voice-quote">Latest {last.sys}/{last.dia}</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height="74" role="img" aria-label="30-day blood pressure trend">
        <rect x={padX} y={goalY} width={width - padX * 2} height={height - padY - goalY} fill="#EAF4F1" opacity="0.7" />
        <line x1={padX} y1={goalY} x2={width - padX} y2={goalY} stroke="#16695D" strokeWidth="1" strokeDasharray="3 3" />
        <path d={sysPath} fill="none" stroke="#18181B" strokeWidth="2" />
        <path d={diaPath} fill="none" stroke="#6B6860" strokeWidth="1.5" />
        <circle cx={xOf(points.length - 1)} cy={yOf(last.sys)} r="3.2" fill="#B45309" />
      </svg>
    </div>
  );
}
