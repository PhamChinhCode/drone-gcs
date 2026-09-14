// Lịch sử & phát lại (F6, 9.6): mở một nhiệm vụ → CÙNG khung cảnh 3D với thanh tua, tốc độ 0,5×/1×/4×.
import { useEffect, useMemo, useRef, useState } from "react";
import { get } from "../../lib/api";
import type { EventRow, Mission, TelemetryRow } from "../../lib/types";
import { FSM_NAMES } from "../../lib/types";
import { dateTimeStr, timeStr } from "../../lib/units";
import { useSite } from "../../store/site";
import { Scene } from "../scene3d/Scene";
import { makeSample, TelemetryBuffer } from "../scene3d/useTelemetryBuffer";

/** ReplaySource: đồng hồ ảo có tua/tốc độ; bộ đệm nạp toàn bộ mẫu của nhiệm vụ. */
class ReplaySource {
  buffer = new TelemetryBuffer(Number.MAX_SAFE_INTEGER);
  t0 = 0; t1 = 0;
  private base = 0; private wall = 0;
  rate = 1; playing = false;
  load(rows: TelemetryRow[]) {
    this.buffer.load(rows.map((r) => makeSample(r.t_utc * 1000, [r.pos_n, r.pos_e, r.pos_d], [r.roll, r.pitch, r.yaw], r.fsm_state, r.flags, r.wp_index, r.battery_pct)));
    this.t0 = rows.length ? rows[0].t_utc * 1000 : 0;
    this.t1 = rows.length ? rows[rows.length - 1].t_utc * 1000 : 0;
    this.seek(this.t0);
  }
  now = () => {
    const t = this.playing ? this.base + (performance.now() - this.wall) * this.rate : this.base;
    if (t >= this.t1 && this.playing) { this.base = this.t1; this.playing = false; }
    return Math.min(t, this.t1);
  };
  seek(t: number) { this.base = t; this.wall = performance.now(); }
  setRate(r: number) { this.base = this.now(); this.wall = performance.now(); this.rate = r; }
  play(on: boolean) { this.base = this.now(); this.wall = performance.now(); this.playing = on; if (on && this.base >= this.t1) this.base = this.t0; }
}

function ReplayPlayer({ mission, onClose }: { mission: Mission; onClose: () => void }) {
  const { tags, areas } = useSite();
  const src = useMemo(() => new ReplaySource(), []);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [loaded, setLoaded] = useState(false);
  const raf = useRef(0);

  useEffect(() => {
    Promise.all([get<{ samples: TelemetryRow[]; events: EventRow[] }>(`/api/missions/${mission.id}/telemetry`), get<Record<string, unknown>>(`/api/missions/${mission.id}/report`)])
      .then(([tel, rep]) => { src.load(tel.samples); setEvents(tel.events); setReport(rep); setT(src.t0); setLoaded(true); });
  }, [mission.id, src]);

  useEffect(() => {
    const loop = () => { setT(src.now()); setPlaying(src.playing); raf.current = requestAnimationFrame(loop); };
    raf.current = requestAnimationFrame(loop); // cập nhật thanh tua (DOM), cảnh 3D đọc đồng hồ trực tiếp
    return () => cancelAnimationFrame(raf.current);
  }, [src]);

  const idx = src.buffer.indexAt(t);
  const cur = idx >= 0 ? src.buffer.samples[idx] : null;
  const dur = (src.t1 - src.t0) / 1000;

  return (
    <div className="replay">
      <div className="replay-scene">
        <Scene buffer={src.buffer} clock={src.now} delayMs={0} tags={tags} areas={areas} plan={mission.waypoints ?? null} />
        <div className="hud">
          <div className="hud-item"><label>Thời điểm</label><b>{loaded ? timeStr(t / 1000) : "…"}</b></div>
          <div className="hud-item"><label>Độ cao</label><b>{cur ? (-cur.ned[2]).toFixed(1) : "—"} m</b></div>
          <div className="hud-item"><label>Pin</label><b>{cur?.batt ?? "—"} %</b></div>
          <div className="hud-item wide"><label>FSM</label><b>{cur ? FSM_NAMES[cur.fsm] : "—"}</b><small>WP {cur?.wp ?? "—"}</small></div>
        </div>
      </div>
      <div className="replay-bar panel">
        <button onClick={() => { src.play(!src.playing); }}>{playing ? "❚❚" : "▶"}</button>
        {[0.5, 1, 4].map((r) => <button key={r} className={rate === r ? "active" : ""} onClick={() => { src.setRate(r); setRate(r); }}>{r}×</button>)}
        <div className="timeline">
          <input type="range" min={src.t0} max={src.t1 || 1} step={50} value={t} onChange={(e) => src.seek(+e.target.value)} />
          {dur > 0 && events.map((ev) => (
            <i key={ev.id} className={`ev-mark sev${ev.severity}`} style={{ left: `${((ev.t_utc * 1000 - src.t0) / (src.t1 - src.t0)) * 100}%` }}
              title={`${timeStr(ev.t_utc)} ${ev.message}`} onClick={() => src.seek(ev.t_utc * 1000)} />
          ))}
        </div>
        <span className="muted">{((t - src.t0) / 1000).toFixed(1)} / {dur.toFixed(1)} s</span>
        <button onClick={onClose}>Đóng</button>
      </div>
      <div className="replay-side panel">
        <h3>#{mission.id} {mission.plan_name}</h3>
        {report && (
          <table className="kv small"><tbody>
            <tr><th>Kết quả</th><td>{mission.state} {mission.fail_reason ? `(${mission.fail_reason})` : ""}</td></tr>
            <tr><th>Thời lượng</th><td>{String(report.duration_s)} s</td></tr>
            <tr><th>Quãng đường</th><td>{String(report.distance_m)} m</td></tr>
            <tr><th>Cao tối đa</th><td>{String(report.max_alt_m)} m</td></tr>
            <tr><th>Pin</th><td>{String(report.battery_start_pct)} → {String(report.battery_end_pct)} %</td></tr>
            <tr><th>WP đã tới</th><td>{String(report.waypoints_reached)} / {mission.waypoints?.length}</td></tr>
          </tbody></table>
        )}
        <h4>Sự kiện</h4>
        <ul className="events">
          {events.map((ev) => <li key={ev.id} className={`sev${ev.severity}`} onClick={() => src.seek(ev.t_utc * 1000)}>{timeStr(ev.t_utc)} <b>{ev.category}</b> {ev.message}</li>)}
        </ul>
      </div>
    </div>
  );
}

export function HistoryPage() {
  const [list, setList] = useState<Mission[]>([]);
  const [filter, setFilter] = useState({ state: "", tag: "", from: "", to: "" });
  const [open, setOpen] = useState<Mission | null>(null);

  const load = () => {
    const q = new URLSearchParams();
    if (filter.state) q.set("state", filter.state);
    if (filter.tag) q.set("tag", filter.tag);
    if (filter.from) q.set("from", new Date(filter.from).toISOString());
    if (filter.to) q.set("to", new Date(filter.to).toISOString());
    get<Mission[]>(`/api/missions?${q}`).then(setList);
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (open) return <ReplayPlayer mission={open} onClose={() => setOpen(null)} />;
  return (
    <div className="page">
      <div className="panel">
        <h3>Lịch sử nhiệm vụ</h3>
        <div className="form-row">
          <select value={filter.state} onChange={(e) => setFilter({ ...filter, state: e.target.value })}>
            <option value="">mọi trạng thái</option>{["done", "failed", "aborted", "running", "ready", "queued"].map((s) => <option key={s}>{s}</option>)}
          </select>
          <input placeholder="tag id" value={filter.tag} onChange={(e) => setFilter({ ...filter, tag: e.target.value })} />
          <label>từ <input type="datetime-local" value={filter.from} onChange={(e) => setFilter({ ...filter, from: e.target.value })} /></label>
          <label>đến <input type="datetime-local" value={filter.to} onChange={(e) => setFilter({ ...filter, to: e.target.value })} /></label>
          <button onClick={load}>Lọc</button>
        </div>
        <table className="grid">
          <thead><tr><th>#</th><th>Kế hoạch</th><th>Trạng thái</th><th>Lấy/Giao</th><th>Bắt đầu</th><th>Kết thúc</th><th>Lý do</th><th /></tr></thead>
          <tbody>{list.map((m) => (
            <tr key={m.id}>
              <td>{m.id}</td><td>{m.plan_name}</td><td><span className={`pill ${m.state}`}>{m.state}</span></td>
              <td>{m.pickup_tag ?? "—"} → {m.dropoff_tag ?? "—"}</td><td>{dateTimeStr(m.started_at)}</td><td>{dateTimeStr(m.finished_at)}</td>
              <td className="small">{m.fail_reason}</td>
              <td><button disabled={!m.started_at} onClick={async () => setOpen(await get<Mission>(`/api/missions/${m.id}`))}>Phát lại 3D</button></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}
