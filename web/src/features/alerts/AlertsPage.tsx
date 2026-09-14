// Cảnh báo (F5): cảnh báo đang hoạt động, lịch sử sự cố, trạng thái failsafe.
import { useEffect, useState } from "react";
import { get, post } from "../../lib/api";
import type { Alert, EventRow } from "../../lib/types";
import { SEVERITY, timeStr } from "../../lib/units";
import { useLive } from "../../store/live";

export function AlertsPage() {
  const active = useLive((s) => s.alerts);
  const liveEvents = useLive((s) => s.events);
  const shadow = useLive((s) => s.shadow);
  const [events, setEvents] = useState<EventRow[]>([]);
  useEffect(() => { get<{ events: EventRow[] }>("/api/alerts").then((r) => setEvents(r.events)); }, [liveEvents.length, active.length]);

  return (
    <div className="page">
      <div className="panel">
        <h3>Failsafe</h3>
        <p>Loại failsafe drone báo: <b className={shadow?.failsafe_type ? "bad" : "good"}>{shadow?.failsafe_name ?? "—"}</b> · FSM {shadow?.fsm_name ?? "—"}</p>
        <p className="muted small">GCS chỉ phát hiện sớm và đề nghị; drone tự quyết định leo thang failsafe (2.2, 10.3).</p>
      </div>
      <div className="panel">
        <h3>Đang hoạt động ({active.length})</h3>
        <table className="grid">
          <thead><tr><th>Mức</th><th>Mã</th><th>Nội dung</th><th>Lúc</th><th /></tr></thead>
          <tbody>{active.map((a: Alert) => (
            <tr key={a.code} className={`sev${a.severity}`}>
              <td>{SEVERITY[a.severity]}</td><td><code>{a.code}</code></td><td>{a.message}</td><td>{timeStr(a.raised_at)}</td>
              <td>{a.acked ? <span className="muted">đã xác nhận</span> : <button onClick={() => post(`/api/alerts/${a.code}/ack`)}>Xác nhận</button>}</td>
            </tr>
          ))}
            {!active.length && <tr><td colSpan={5} className="good">Không có cảnh báo</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <h3>Nhật ký sự kiện</h3>
        <ul className="events">
          {events.map((e) => <li key={e.id} className={`sev${e.severity}`}>{timeStr(e.t_utc)} <b>[{e.category}]</b> {e.message}{e.mission_id ? ` · NV #${e.mission_id}` : ""}</li>)}
        </ul>
      </div>
    </div>
  );
}
