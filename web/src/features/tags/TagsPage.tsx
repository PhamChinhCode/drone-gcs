// Quản lý tag (F1): bảng CRUD, dạy vị trí từ drone, đồng bộ bản đồ + map_crc hai bên.
import { useState } from "react";
import { del, download, errMsg, post, put } from "../../lib/api";
import { TAG36H11_MAX_ID, tag36h11Svg } from "../../lib/apriltag36h11";
import type { Tag, TagKind } from "../../lib/types";
import { hex8 } from "../../lib/units";
import { useIsAdmin } from "../../store/auth";
import { useLive } from "../../store/live";
import { useSite } from "../../store/site";
import { buildPadSheet } from "./padSheet";

const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** Dựng trang HTML in tag đúng kích thước vật lý (mm, không co giãn) và mở ở tab mới để người dùng
 *  bấm in/lưu PDF từ trình duyệt — không cần thư viện PDF ở backend. */
function buildPrintSheet(tags: Tag[]): string {
  const toPrint = tags.filter((t) => t.enabled);
  const ok = toPrint.filter((t) => t.tag_id <= TAG36H11_MAX_ID);
  const bad = toPrint.filter((t) => t.tag_id > TAG36H11_MAX_ID);
  const cards = ok.map((t) => {
    const sizeMm = t.tag_size_m * 1000;
    // Mũi tên "TRÊN": hướng bãi (yaw_deg) là hướng của mép này — đặt tờ in đúng chiều (giao ước 0.8, 8.6).
    return `<div class="card"><div class="up">▲ TRÊN</div>${tag36h11Svg(t.tag_id, sizeMm)}` +
      `<div class="label">tag_id ${t.tag_id} — ${escapeHtml(t.label)}<br/>${t.kind} — cạnh đen ${sizeMm.toFixed(0)} mm</div></div>`;
  }).join("");
  const warn = bad.length
    ? `<p class="warn">Bỏ qua ${bad.length} tag ngoài phạm vi họ AprilTag 36h11 (id &gt; ${TAG36H11_MAX_ID}): ${bad.map((t) => t.tag_id).join(", ")}</p>`
    : "";
  return `<!doctype html><html><head><meta charset="utf-8"><title>In tag AprilTag</title><style>
body { font-family: sans-serif; margin: 10mm; }
.note { font-size: 11px; color: #555; max-width: 600px; }
.warn { color: #b00020; }
.cal { margin: 4mm 0 8mm; }
.cal svg { display: block; }
.cards { display: flex; flex-wrap: wrap; gap: 8mm; }
.card { break-inside: avoid; text-align: center; }
.label { font-size: 10px; margin-top: 2mm; }
.up { font-size: 10px; margin-bottom: 1mm; }
@media print { .note { display: none; } }
</style></head><body>
<p class="note"><b>Trước khi in:</b> trong hộp thoại in, đặt Tỷ lệ = "Không" / "Actual size" — <b>KHÔNG</b> chọn
"Vừa trang" / "Fit to page", vì tag in sai cỡ làm sai khoảng cách drone ước lượng đúng bằng chừng ấy phần trăm.
Đo lại ô vuông hiệu chỉnh bên dưới sau khi in: cạnh phải đúng 20&nbsp;mm.</p>
<div class="cal"><svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" viewBox="0 0 20 20">
<rect x="0.25" y="0.25" width="19.5" height="19.5" fill="none" stroke="#000" stroke-width="0.5"/>
<text x="2" y="11" font-size="3">20 mm</text></svg></div>
${warn}
<div class="cards">${cards}</div>
</body></html>`;
}

const EMPTY: Tag = { tag_id: 0, label: "", pos_n_m: 0, pos_e_m: 0, pos_d_m: 0, yaw_deg: 0, yaw_valid: false, tag_size_m: 0.3, kind: "pickup", landing_tol_m: 0.3, enabled: true };

export function TagsPage() {
  const { tags, issues, load } = useSite();
  const map = useLive((s) => s.map);
  const link = useLive((s) => s.link);
  const admin = useIsAdmin();
  const [edit, setEdit] = useState<Record<number, Tag>>({});
  const [draft, setDraft] = useState<Tag>({ ...EMPTY, tag_id: Math.max(-1, ...tags.map((t) => t.tag_id)) + 1 });
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [, setBusy] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setErr(null); setOk(null); setBusy(true);
    try { await fn(); await load(); } catch (e) { setErr(errMsg(e)); }
    setBusy(false);
  };

  const uploadWire = () => run(async () => {
    await post("/api/tags/upload");
    setOk("Đã gửi bản đồ tag qua dây (ACCEPTED) — Pi cần khởi động lại stack mới có hiệu lực. " +
         "Theo dõi ô KHỚP/LỆCH ở trên: đổi thành KHỚP nghĩa là đã xong.");
  });

  const printSheet = () => {
    if (!tags.some((t) => t.enabled)) { setErr("Không có tag nào đang bật để in"); return; }
    setErr(null); setOk(null);
    const blob = new Blob([buildPrintSheet(tags)], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
  };

  const printPads = () => {
    if (!tags.some((t) => t.enabled)) { setErr("Không có tag nào đang bật để in"); return; }
    setErr(null); setOk(null);
    window.open(URL.createObjectURL(new Blob([buildPadSheet(tags)], { type: "text/html" })), "_blank");
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
          {admin && <button className="primary" disabled={!link?.up} title={link?.up ? "" : "chưa có liên kết với drone"}
                            onClick={uploadWire}>Nạp qua dây</button>}
          {admin && <button onClick={() => run(() => download("/api/tags/export", "tags.yaml"))}>Tải tags.yaml</button>}
        </div>
        {errors.length > 0 && <div className="error small">Thiết kế còn {errors.length} lỗi — chặn nạp/xuất bản đồ: {errors.map((e) => e.code).join(", ")}</div>}
        <p className="muted small">
          "Nạp qua dây" chỉ đổi được toạ độ của tag <b>đã khai</b> trên Pi (<code>apriltag.yaml</code>) —
          không thêm được tag mới. Sau khi Pi báo đã nhận, vẫn cần <b>khởi động lại stack bên Pi</b> mới
          có hiệu lực thật (giao ước 8.7); ô KHỚP/LỆCH ở trên tự cập nhật khi đó. Thêm tag mới thì vẫn
          phải tải <code>tags.yaml</code> và sửa <code>apriltag.yaml</code> bằng tay trên Pi.
          Vùng bay / vùng cấm là khái niệm của GCS, không nằm trong CRC và drone không biết tới.
        </p>
      </div>
      {err && <div className="error">{err}</div>}
      {ok && <div className="good">{ok}</div>}
      <div className="panel">
        <h3>Danh sách tag</h3>
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>ID</th><th>Nhãn</th><th>Loại</th><th>N (m)</th><th>E (m)</th><th>D (m)</th><th title="Hướng mép TRÊN của tag, độ từ trục N bản đồ, chiều kim đồng hồ">Hướng°</th><th title="Đã đo hướng ngoài thực địa — chỉ khi đánh dấu, drone mới quay mũi theo hướng bãi và dùng tag nhỏ (giao ước 0.8)">Đã đo</th><th>Cỡ in (m)</th><th>Dung sai (m)</th><th>Bật</th><th /></tr></thead>
            <tbody>
              {tags.map((t0) => {
                const t = edit[t0.id!] ?? t0;
                const set = (n: Tag) => setEdit({ ...edit, [t0.id!]: n });
                const dirty = !!edit[t0.id!];
                return (
                  <tr key={t0.id}>
                    <td>{field(t, "tag_id", set)}</td><td>{field(t, "label", set, "text")}</td><td>{kindSel(t, set)}</td>
                    <td>{field(t, "pos_n_m", set)}</td><td>{field(t, "pos_e_m", set)}</td><td>{field(t, "pos_d_m", set)}</td>
                    <td>{field(t, "yaw_deg", set)}</td>
                    <td><input type="checkbox" checked={t.yaw_valid} disabled={!admin} onChange={(e) => set({ ...t, yaw_valid: e.target.checked })} /></td>
                    <td>{field(t, "tag_size_m", set)}</td><td>{field(t, "landing_tol_m", set)}</td>
                    <td><input type="checkbox" checked={t.enabled} disabled={!admin} onChange={(e) => set({ ...t, enabled: e.target.checked })} /></td>
                    <td className="nowrap">
                      {admin && dirty && <button onClick={() => run(async () => { const { id: _i, site_id: _s, ...body } = t; await put(`/api/tags/${t0.id}`, body); const n = { ...edit }; delete n[t0.id!]; setEdit(n); })}>Lưu</button>}
                      {admin && <button title="Bay drone tới tag rồi ghi vị trí hiện tại" disabled={!link?.up} onClick={() => run(() => post(`/api/tags/teach/${t0.id}`))}>Dạy</button>}
                      {admin && <button className="link danger" onClick={() => confirm(`Xóa tag ${t0.label}?`) && run(() => del(`/api/tags/${t0.id}`))}>xóa</button>}
                    </td>
                  </tr>
                );
              })}
              {admin && (
                <tr className="new-row">
                  <td>{field(draft, "tag_id", setDraft)}</td><td>{field(draft, "label", setDraft, "text")}</td><td>{kindSel(draft, setDraft)}</td>
                  <td>{field(draft, "pos_n_m", setDraft)}</td><td>{field(draft, "pos_e_m", setDraft)}</td><td>{field(draft, "pos_d_m", setDraft)}</td>
                  <td>{field(draft, "yaw_deg", setDraft)}</td>
                  <td><input type="checkbox" checked={draft.yaw_valid} onChange={(e) => setDraft({ ...draft, yaw_valid: e.target.checked })} /></td>
                  <td>{field(draft, "tag_size_m", setDraft)}</td><td>{field(draft, "landing_tol_m", setDraft)}</td>
                  <td />
                  <td><button className="primary" disabled={!draft.label} onClick={() => run(async () => { await post("/api/tags", draft); setDraft({ ...EMPTY, tag_id: draft.tag_id + 1 }); })}>Thêm</button></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="sync-row">
          <button onClick={printSheet}>In tờ tag (đúng kích thước)</button>
          <span className="muted small">Mở trang HTML ở tab mới — in ở tỷ lệ 100%, không "Vừa trang". Chỉ in tag đang Bật.</span>
        </div>
        <div className="sync-row">
          <button onClick={printPads}>In bãi đáp (A3)</button>
          <span className="muted small">Mỗi bãi một trang A3 dọc: tag to + tag nhỏ (id + 10, 100 mm) phía TRÊN, tâm cách 220 mm — đúng mẫu Pi tự suy (giao ước 0.8). Chỉ tag 0–9 đang Bật.</span>
        </div>
      </div>
    </div>
  );
}
