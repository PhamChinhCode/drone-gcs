// Tạo nhiệm vụ + hàng đợi (F3, 8.2). Tải lên và Bắt đầu là HAI nút tách rời (5.6):
// người vận hành phải xem trước tuyến bay trong 3D rồi mới bấm bắt đầu.
import { useEffect, useState } from "react";
import { errMsg, get, post, put } from "../../lib/api";
import type { Mission, Tag } from "../../lib/types";
import { dateTimeStr } from "../../lib/units";
import { liveBuffer, liveClock } from "../../lib/ws";
import { useLive } from "../../store/live";
import { useSite } from "../../store/site";
import { Scene } from "../scene3d/Scene";

// Trùng planner.MAX_TAG_VISIBLE_ALT_M phía backend: cao hơn thì camera không đọc được tag 25 cm.
const MAX_TAG_VISIBLE_ALT_M = 2.5;

/** Một điểm dừng = một mục kế hoạch: bay tới, tìm marker, hạ, làm action, cất cánh lại (3.2b). */
type Stop = { tag_id: number; action: string };

export function MissionPage() {
  const { tags, areas, site } = useSite();
  const map = useLive((s) => s.map);
  const version = useLive((s) => s.missionsVersion);
  const progress = useLive((s) => s.uploadProgress);
  const renderDelay = useLive((s) => s.renderDelayMs);
  const [mode, setMode] = useState<"pair" | "custom">("pair");
  const [pickup, setPickup] = useState<number | "">("");
  const [dropoff, setDropoff] = useState<number | "">("");
  const [stops, setStops] = useState<Stop[]>([]);
  const [cruise, setCruise] = useState(site?.cruise_alt_default_m ?? 5);
  const [vel, setVel] = useState(3);
  const [priority, setPriority] = useState(100);
  const [queue, setQueue] = useState<Mission[]>([]);
  const [sel, setSel] = useState<Mission | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = async () => {
    const q = await get<Mission[]>("/api/missions/queue");
    setQueue(q);
    if (sel) {
      const fresh = await get<Mission>(`/api/missions/${sel.id}`).catch(() => null);
      setSel(fresh ? { ...fresh, warnings: sel.warnings } : null);
    }
  };
  useEffect(() => { void refresh(); }, [version]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setErr(null);
    try { await fn(); await refresh(); } catch (e) { setErr(errMsg(e)); }
    setBusy(null);
  };

  const onTagClick = (t: Tag) => {
    if (mode === "custom") { setStops([...stops, { tag_id: t.tag_id, action: "none" }]); return; }
    if (pickup === "" || dropoff !== "") { setPickup(t.tag_id); setDropoff(""); }
    else if (t.tag_id !== pickup) setDropoff(t.tag_id);
  };

  const create = () => act("create", async () => {
    const body = mode === "pair"
      ? { pickup_tag: pickup, dropoff_tag: dropoff, cruise_alt_m: cruise, max_vel_mps: vel, priority }
      : { stops, cruise_alt_m: cruise, max_vel_mps: vel, priority };
    const m = await post<Mission>("/api/missions", body);
    setSel(m);
  });

  const selectMission = async (id: number) => {
    const [m, pv] = await Promise.all([get<Mission>(`/api/missions/${id}`), get<{ warnings: string[] }>(`/api/missions/${id}/preview`)]);
    setSel({ ...m, warnings: pv.warnings });
  };

  const enabled = tags.filter((t) => t.enabled && t.kind !== "home");
  const hl = new Set<number>([pickup, dropoff, ...stops.map((s) => s.tag_id)].filter((x): x is number => x !== ""));
  const blocked = !map?.in_sync;

  return (
    <div className="split">
      <div className="split-left">
        <div className="panel">
          <h3>Tạo nhiệm vụ</h3>
          <div className="tabs">
            <button className={mode === "pair" ? "active" : ""} onClick={() => setMode("pair")}>Lấy → Giao</button>
            <button className={mode === "custom" ? "active" : ""} onClick={() => setMode("custom")}>Chuỗi tùy biến</button>
          </div>
          <p className="muted small">Có thể bấm trực tiếp vào tag trong cảnh 3D để chọn.</p>
          {mode === "pair" ? (
            <div className="form-grid">
              <label>Tag lấy<select value={pickup} onChange={(e) => setPickup(e.target.value === "" ? "" : +e.target.value)}>
                <option value="">—</option>{enabled.map((t) => <option key={t.tag_id} value={t.tag_id}>{t.label} #{t.tag_id} ({t.kind})</option>)}
              </select></label>
              <label>Tag giao<select value={dropoff} onChange={(e) => setDropoff(e.target.value === "" ? "" : +e.target.value)}>
                <option value="">—</option>{enabled.map((t) => <option key={t.tag_id} value={t.tag_id}>{t.label} #{t.tag_id} ({t.kind})</option>)}
              </select></label>
            </div>
          ) : (
            <ol className="stops">
              {stops.map((s, i) => (
                <li key={i}>
                  {tags.find((t) => t.tag_id === s.tag_id)?.label} #{s.tag_id}
                  <select value={s.action} onChange={(e) => setStops(stops.map((x, j) => j === i ? { ...x, action: e.target.value } : x))}>
                    <option value="none">ghé</option><option value="pickup">lấy</option><option value="dropoff">giao</option>
                  </select>
                  <button className="link" onClick={() => setStops(stops.filter((_, j) => j !== i))}>xóa</button>
                </li>
              ))}
              {!stops.length && <li className="muted">Bấm tag trong 3D để thêm điểm dừng</li>}
            </ol>
          )}
          <div className="form-grid">
            <label>Cao hành trình (m)<input type="number" min={1} max={120} step={0.5} value={cruise} onChange={(e) => setCruise(+e.target.value)} /></label>
            {cruise > MAX_TAG_VISIBLE_ALT_M && <div className="warn small">⚠ Cao hơn {MAX_TAG_VISIBLE_ALT_M} m camera không đọc được tag 25 cm — nên đặt ~2 m</div>}
            <label>Vận tốc tối đa (m/s)<input type="number" min={0.5} max={15} step={0.5} value={vel} onChange={(e) => setVel(+e.target.value)} /></label>
            <label>Ưu tiên (nhỏ = trước)<input type="number" value={priority} onChange={(e) => setPriority(+e.target.value)} /></label>
          </div>
          <button className="primary" disabled={!!busy || (mode === "pair" ? pickup === "" || dropoff === "" : !stops.length)} onClick={create}>
            Lập kế hoạch (xem trước)
          </button>
        </div>

        {sel && (
          <div className="panel">
            <h3>#{sel.id} {sel.plan_name} <span className={`pill ${sel.state}`}>{sel.state}</span></h3>
            {sel.warnings?.map((w) => <div key={w} className="warn small">⚠ {w} (chỉ cảnh báo, drone không cưỡng chế)</div>)}
            {sel.fail_reason && <div className="error small">{sel.fail_reason}</div>}
            {blocked && <div className="error small">Bản đồ tag lệch với drone — không tải lên / bắt đầu được.</div>}
            <table className="grid small">
              <thead><tr><th>#</th><th>tag</th><th>N</th><th>E</th><th>cao trên tag</th><th>hành động</th><th>đã tới</th></tr></thead>
              <tbody>{sel.waypoints?.map((w) => (
                <tr key={w.seq}><td>{w.seq}</td><td>{w.tag_id ?? "—"}</td><td>{w.pos_n_m.toFixed(1)}</td><td>{w.pos_e_m.toFixed(1)}</td>
                  <td>{(-w.pos_d_m).toFixed(1)}</td><td>{w.action}</td>
                  <td>{w.reached_at ? "✓" : ""}</td></tr>
              ))}</tbody>
            </table>
            {progress && progress.id === sel.id && sel.state === "uploading" && <progress max={progress.total} value={progress.done} />}
            <div className="btn-row">
              <button disabled={!!busy || blocked || !["queued", "ready"].includes(sel.state)} onClick={() => act("upload", () => post(`/api/missions/${sel.id}/upload`))}>
                {busy === "upload" ? "Đang tải lên…" : "1. Tải lên drone"}
              </button>
              <button className="primary" disabled={!!busy || blocked || sel.state !== "ready"} onClick={() => act("start", () => post(`/api/missions/${sel.id}/start`))}>2. Bắt đầu</button>
              <button className="danger" disabled={!["queued", "ready", "running"].includes(sel.state)} onClick={() => act("abort", () => post(`/api/missions/${sel.id}/abort`))}>Hủy</button>
            </div>
          </div>
        )}
        {err && <div className="error">{err}</div>}

        <div className="panel">
          <h3>Hàng đợi</h3>
          <table className="grid small">
            <thead><tr><th>#</th><th>kế hoạch</th><th>trạng thái</th><th>ưu tiên</th><th>tạo lúc</th><th /></tr></thead>
            <tbody>{queue.map((m) => (
              <tr key={m.id} className={sel?.id === m.id ? "sel" : ""} onClick={() => selectMission(m.id)}>
                <td>{m.id}</td><td>{m.plan_name}</td><td><span className={`pill ${m.state}`}>{m.state}</span></td>
                <td>
                  <button className="link" onClick={(e) => { e.stopPropagation(); void act("prio", () => put(`/api/missions/${m.id}/priority`, { priority: m.priority - 10 })); }}>▲</button>
                  {m.priority}
                  <button className="link" onClick={(e) => { e.stopPropagation(); void act("prio", () => put(`/api/missions/${m.id}/priority`, { priority: m.priority + 10 })); }}>▼</button>
                </td>
                <td>{dateTimeStr(m.created_at)}</td>
                <td>{m.state === "queued" && <button className="link" onClick={(e) => { e.stopPropagation(); void act("abort", () => post(`/api/missions/${m.id}/abort`)); }}>hủy</button>}</td>
              </tr>
            ))}
              {!queue.length && <tr><td colSpan={6} className="muted">Không có nhiệm vụ chờ</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      <div className="split-right">
        <Scene buffer={liveBuffer} clock={liveClock} delayMs={renderDelay} tags={tags} areas={areas}
          plan={sel?.waypoints ?? null} planHighlight wpIndex={sel?.state === "running" ? undefined : 0}
          highlightTags={hl} onTagClick={onTagClick} />
      </div>
    </div>
  );
}
