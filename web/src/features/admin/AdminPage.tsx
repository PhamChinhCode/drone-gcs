// Quản trị (F7): người dùng, ngưỡng an toàn + bảng đối chiếu lệch cấu hình với drone (10.1), nhật ký kiểm toán.
import { useEffect, useState } from "react";
import { del, errMsg, get, post, put } from "../../lib/api";
import type { ConfigRow, User } from "../../lib/types";
import { dateTimeStr } from "../../lib/units";
import { useLive } from "../../store/live";

interface AuditRow { id: number; user_id: number | null; t_utc: string; action: string; target: string | null; detail: unknown }

export function AdminPage() {
  const [users, setUsers] = useState<(User & { last_login: string | null })[]>([]);
  const [config, setConfig] = useState<ConfigRow[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [push, setPush] = useState(false);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [nu, setNu] = useState({ username: "", password: "", role: "operator" });
  const [err, setErr] = useState<string | null>(null);
  const drift = useLive((s) => s.drift);

  const load = async () => {
    const [u, c, a] = await Promise.all([get<typeof users>("/api/users"), get<ConfigRow[]>("/api/config"), get<AuditRow[]>("/api/audit")]);
    setUsers(u); setConfig(c); setAudit(a);
  };
  useEffect(() => { void load(); }, [drift]);

  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); await load(); } catch (e) { setErr(errMsg(e)); }
  };

  return (
    <div className="page">
      {err && <div className="error">{err}</div>}
      <div className="panel">
        <h3>Ngưỡng an toàn — phải khớp safety.yaml phía Pi 4</h3>
        <table className="grid">
          <thead><tr><th>Khóa</th><th>GCS</th><th>Drone</th><th>Khớp</th><th>Kiểm lúc</th></tr></thead>
          <tbody>{config.map((c) => (
            <tr key={c.key} className={c.in_sync === 0 ? "sev2" : ""}>
              <td><code>{c.key}</code></td>
              <td><input className="num" value={edits[c.key] ?? c.value} onChange={(e) => setEdits({ ...edits, [c.key]: e.target.value })} /></td>
              <td>{c.drone_value ?? <span className="muted">chưa đọc</span>}</td>
              <td>{c.in_sync === null ? "—" : c.in_sync ? <span className="good">✓</span> : <b className="bad">LỆCH</b>}</td>
              <td className="small">{dateTimeStr(c.last_checked)}</td>
            </tr>
          ))}</tbody>
        </table>
        <div className="btn-row">
          <label className="check"><input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} /> Đồng thời gửi PARAM_SET xuống drone</label>
          <button className="primary" disabled={!Object.keys(edits).length} onClick={() => run(async () => {
            await put("/api/config", { values: Object.fromEntries(Object.entries(edits).map(([k, v]) => [k, +v])), push_to_drone: push });
            setEdits({});
          })}>Lưu ngưỡng</button>
          <button onClick={() => run(() => post("/api/config/check"))}>Đối chiếu lại với drone</button>
        </div>
        <p className="muted small">Đối chiếu tự động mỗi khi liên kết nối lại (REQUEST(PARAMS) → PARAM_VALUE).</p>
      </div>
      <div className="panel">
        <h3>Người dùng</h3>
        <table className="grid">
          <thead><tr><th>#</th><th>Tên</th><th>Quyền</th><th>Đăng nhập cuối</th><th /></tr></thead>
          <tbody>{users.map((u) => (
            <tr key={u.id}><td>{u.id}</td><td>{u.username}</td><td>{u.role}</td><td>{dateTimeStr(u.last_login)}</td>
              <td><button className="link danger" onClick={() => confirm(`Xóa ${u.username}?`) && run(() => del(`/api/users/${u.id}`))}>xóa</button></td></tr>
          ))}</tbody>
        </table>
        <div className="form-row">
          <input placeholder="tên đăng nhập" value={nu.username} onChange={(e) => setNu({ ...nu, username: e.target.value })} />
          <input placeholder="mật khẩu (≥ 6)" type="password" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
          <select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}><option>operator</option><option>admin</option></select>
          <button onClick={() => run(async () => { await post("/api/users", nu); setNu({ username: "", password: "", role: "operator" }); })}>Thêm</button>
        </div>
      </div>
      <div className="panel">
        <h3>Nhật ký kiểm toán</h3>
        <div className="table-wrap tall">
          <table className="grid small">
            <thead><tr><th>Lúc</th><th>User</th><th>Hành động</th><th>Đối tượng</th><th>Chi tiết</th></tr></thead>
            <tbody>{audit.map((a) => <tr key={a.id}><td>{dateTimeStr(a.t_utc)}</td><td>{a.user_id}</td><td>{a.action}</td><td>{a.target}</td><td><code>{a.detail ? JSON.stringify(a.detail) : ""}</code></td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
