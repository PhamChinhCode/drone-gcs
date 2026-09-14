export function Sparkline({ values, label, unit, min, max, color = "#0369a1" }: { values: (number | null)[]; label: string; unit: string; min?: number; max?: number; color?: string }) {
  const W = 360, H = 90, pad = 4;
  const nums = values.filter((v): v is number => v !== null);
  const lo = min ?? (nums.length ? Math.min(...nums) : 0);
  const hi = max ?? (nums.length ? Math.max(...nums) : 1);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => (v === null ? null : [pad + (i / Math.max(values.length - 1, 1)) * (W - 2 * pad), H - pad - ((v - lo) / span) * (H - 2 * pad)]));
  let d = "";
  pts.forEach((p, i) => { if (p) d += `${!pts[i - 1] ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`; });
  const last = nums.at(-1);
  return (
    <figure className="spark">
      <figcaption>{label}: <b>{last === undefined ? "—" : `${last.toFixed(1)} ${unit}`}</b> <span className="muted">[{lo.toFixed(0)}…{hi.toFixed(0)}]</span></figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"><path d={d} fill="none" stroke={color} strokeWidth={1.5} /></svg>
    </figure>
  );
}
