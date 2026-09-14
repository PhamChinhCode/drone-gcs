// Quản lý tag (F1): bảng CRUD, dạy vị trí từ drone, đồng bộ bản đồ + map_crc hai bên.
import { useState } from "react";
import { del, errMsg, post, put } from "../../lib/api";
import type { Tag, TagKind } from "../../lib/types";
import { hex8 } from "../../lib/units";
import { useIsAdmin } from "../../store/auth";
import { useLive } from "../../store/live";
import { useSite } from "../../store/site";

const EMPTY: Tag = { tag_id: 0, label: "", pos_n_m: 0, pos_e_m: 0, pos_d_m: 0, yaw_deg: 0, tag_size_m: 0.3, kind: "pickup", landing_tol_m: 0.3, enabled: true };

export function TagsPage() {
  const { tags, issues, load } = useSite();
  const map = useLive((s) => s.map);
  const link = useLive((s) => s.link);
  const admin = useIsAdmin();
  const [edit, setEdit] = useState<Record<number, Tag>>({});
  const [draft, setDraft] = useState<Tag>({ ...EMPTY, tag_id: Math.max(-1, ...tags.map((t) => t.tag_id)) + 1 });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setErr(null); setBusy(true);
    try { await fn(); await load(); } catch (e) { setErr(errMsg(e)); }
    setBusy(false);
  };

  const field = (t: Tag, k: keyof Tag, onChange: (t: Tag) => void, type: "num" | "text" = "num") => (
    <input className={type === "num" ? "num" : ""} type={type === "num" ? "number" : "text"} step="0.01" value={String(t[k] ?? "")} disabled={!admin}
      onChange={(e) => onChange({ ...t, [k]: type === "num" ? +e.target.value : e.target.value })} />
  );
  const kindSel = (t: Tag, onChange: (t: Tag) => void) => (
    <select value={t.kind} disabled={!admin} onChange={(e) => onChange({ ...t, kind: e.target.value as TagKind })}>
      {["home", "pickup", "dropoff", "waypoint"].map((k) => <option key={k}>{k}</option>)}
    </select>
  );

  const errors = issues.filter((i) => i.level === "error");
  return (
    <div className="page">
      <div className="panel">
        <h3>Đồng bộ bản đồ tag</h3>
        <div className="sync-row">
          <div>map_crc GCS <code>{hex8(map?.gcs_crc)}</code></div>
          <div>map_crc drone <code>{hex8(map?.drone_crc)}</code></div>
          <div className={map?.in_sync ? "good" : "bad"}><b>{map?.drone_crc === null || !map ? "chưa rõ" : map.in_sync ? "KHỚP" : "LỆCH"}</b></div>
          {admin && <button className="primary" disabled={busy || link?.state !== "ok" || errors.length > 0} onClick={() => run(() => post("/api/tags/sync"))}>Đồng bộ xuống drone</button>}
        </div>
        {errors.length > 0 && <div className="error small">Thiết kế còn {errors.length} lỗi — chặn đồng bộ: {errors.map((e) => e.code).join(", ")}</div>}
        <p className="muted small">Vùng bay / vùng cấm KHÔNG nằm trong map_crc và không gửi xuống drone (8.4.5).</p>
      </div>
      {err && <div className="error">{err}</div>}
      <div className="panel">
        <h3>Danh sách tag</h3>
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>ID</th><th>Nhãn</th><th>Loại</th><th>N (m)</th><th>E (m)</th><th>D (m)</th><th>Yaw°</th><th>Cỡ in (m)</th><th>Dung sai (m)</th><th>Bật</th><th /></tr></thead>
            <tbody>
              {tags.map((t0) => {
                const t = edit[t0.id!] ?? t0;
                const set = (n: Tag) => setEdit({ ...edit, [t0.id!]: n });
                const dirty = !!edit[t0.id!];
                return (
                  <tr key={t0.id}>
                    <td>{field(t, "tag_id", set)}</td><td>{field(t, "label", set, "text")}</td><td>{kindSel(t, set)}</td>
                    <td>{field(t, "pos_n_m", set)}</td><td>{field(t, "pos_e_m", set)}</td><td>{field(t, "pos_d_m", set)}</td>
                    <td>{field(t, "yaw_deg", set)}</td><td>{field(t, "tag_size_m", set)}</td><td>{field(t, "landing_tol_m", set)}</td>
                    <td><input type="checkbox" checked={t.enabled} disabled={!admin} onChange={(e) => set({ ...t, enabled: e.target.checked })} /></td>
                    <td className="nowrap">
                      {admin && dirty && <button onClick={() => run(async () => { const { id: _i, site_id: _s, ...body } = t; await put(`/api/tags/${t0.id}`, body); const n = { ...edit }; delete n[t0.id!]; setEdit(n); })}>Lưu</button>}
                      {admin && <button title="Bay drone tới tag rồi ghi vị trí hiện tại" disabled={link?.state !== "ok"} onClick={() => run(() => post(`/api/tags/teach/${t0.id}`))}>Dạy</button>}
                      {admin && <button className="link danger" onClick={() => confirm(`Xóa tag ${t0.label}?`) && run(() => del(`/api/tags/${t0.id}`))}>xóa</button>}
                    </td>
                  </tr>
                );
              })}
              {admin && (
                <tr className="new-row">
                  <td>{field(draft, "tag_id", setDraft)}</td><td>{field(draft, "label", setDraft, "text")}</td><td>{kindSel(draft, setDraft)}</td>
                  <td>{field(draft, "pos_n_m", setDraft)}</td><td>{field(draft, "pos_e_m", setDraft)}</td><td>{field(draft, "pos_d_m", setDraft)}</td>
                  <td>{field(draft, "yaw_deg", setDraft)}</td><td>{field(draft, "tag_size_m", setDraft)}</td><td>{field(draft, "landing_tol_m", setDraft)}</td>
                  <td />
                  <td><button className="primary" disabled={!draft.label} onClick={() => run(async () => { await post("/api/tags", draft); setDraft({ ...EMPTY, tag_id: draft.tag_id + 1 }); })}>Thêm</button></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="muted small">Tờ in tag đúng kích thước (tag-sheet.pdf) chưa làm — cần bảng mã AprilTag 36h11; xem docs/KIEN_TRUC_TRIEN_KHAI.md.</p>
      </div>
    </div>
  );
}
