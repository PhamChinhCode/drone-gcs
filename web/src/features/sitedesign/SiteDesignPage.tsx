// Tab Thiết kế khu vực hoạt động (F8, mục 8.4). Sơ đồ 2D nhìn từ trên — KHÔNG dùng cảnh 3D để đặt điểm (8.4.1).
// Quy đổi: px_x = (e − e0)·scale, px_y = −(n − n0)·scale — Bắc hướng lên màn hình.
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, errMsg, post, put } from "../../lib/api";
import type { Area, BgAnchor, Issue, Site, Tag, TagKind } from "../../lib/types";
import { useAuth, useIsAdmin } from "../../store/auth";
import { useSite } from "../../store/site";
import { KIND_COLOR } from "../scene3d/objects/labels";

type Tool = "select" | "tag" | "home" | "operating" | "no_fly" | "measure" | "bg" | "gcs";
type Layer = "grid" | "bg" | "operating" | "no_fly" | "tags" | "homeLines" | "link" | "route";
interface Draft { site: Site; tags: Tag[]; areas: Area[] }
type Drag = { kind: "tag"; idx: number } | { kind: "vertex"; area: number; v: number } | { kind: "pan"; sx: number; sy: number; n0: number; e0: number } | { kind: "measure" } | { kind: "gcs" } | null;

const TOOLS: [Tool, string][] = [["select", "Chọn / kéo"], ["tag", "Đặt tag"], ["home", "Đặt Home"], ["operating", "Vẽ vùng bay"],
  ["no_fly", "Vẽ vùng cấm"], ["measure", "Thước đo"], ["bg", "Ảnh nền"], ["gcs", "Vị trí trạm"]];
const LAYERS: [Layer, string][] = [["grid", "Lưới"], ["bg", "Ảnh nền"], ["operating", "Vùng bay"], ["no_fly", "Vùng cấm"], ["tags", "Tag"],
  ["homeLines", "Home→tag"], ["link", "Bán kính liên kết"], ["route", "Tuyến mẫu"]];

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const snap = (v: number, on: boolean) => (on ? Math.round(v * 2) / 2 : Math.round(v * 1000) / 1000);

/** Biến đổi đồng dạng ảnh→bản đồ từ 2 điểm mốc, trong mặt phẳng (e, −n) cùng chiều trục ảnh. */
function bgMatrix(anchors: BgAnchor[] | null, imgW: number): [number, number, number, number] {
  if (anchors && anchors.length === 2) {
    const [p1, p2] = anchors;
    const dpx = p2.px - p1.px, dpy = p2.py - p1.py;
    const dwx = p2.e - p1.e, dwy = -(p2.n - p1.n);
    const den = dpx * dpx + dpy * dpy || 1;
    const ar = (dwx * dpx + dwy * dpy) / den, ai = (dwy * dpx - dwx * dpy) / den;
    return [ar, ai, p1.e - (ar * p1.px - ai * p1.py), -p1.n - (ai * p1.px + ar * p1.py)];
  }
  const s = 50 / (imgW || 1000);
  return [s, 0, -25, -25];
}

export function SiteDesignPage() {
  const site = useSite();
  const admin = useIsAdmin();
  const token = useAuth((s) => s.token);
  const navigate = useNavigate();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [tool, setTool] = useState<Tool>("select");
  const [layers, setLayers] = useState<Record<Layer, boolean>>({ grid: true, bg: true, operating: true, no_fly: true, tags: true, homeLines: true, link: true, route: true });
  const [view, setView] = useState({ n0: 0, e0: 0, scale: 8 });
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [drag, setDrag] = useState<Drag>(null);
  const [poly, setPoly] = useState<[number, number][]>([]);
  const [measure, setMeasure] = useState<[[number, number], [number, number]] | null>(null);
  const [selTag, setSelTag] = useState<number | null>(null);
  const [selArea, setSelArea] = useState<number | null>(null);
  const [newTag, setNewTag] = useState<Tag | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [highlight, setHighlight] = useState<Issue | null>(null);
  const [ack, setAck] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [route, setRoute] = useState<{ a: number | ""; b: number | "" }>({ a: "", b: "" });
  const [bgUrl, setBgUrl] = useState<string | null>(null);
  const [bgDataUrl, setBgDataUrl] = useState<string | null>(null);
  const [imgW, setImgW] = useState(1000);
  const [pendingAnchor, setPendingAnchor] = useState<{ px: number; py: number } | null>(null);
  const [anchorInput, setAnchorInput] = useState({ n: 0, e: 0 });
  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  // nạp bản thiết kế đã lưu
  useEffect(() => {
    if (site.site && !dirty) {
      setDraft({ site: clone(site.site), tags: clone(site.tags), areas: clone(site.areas) });
      if (site.site.bg_image_path && token) setBgUrl(`/api/sites/${site.site.id}/bg-image?token=${encodeURIComponent(token)}&v=${Date.now()}`);
    }
  }, [site.site, site.tags, site.areas]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, [!!draft]); // eslint-disable-line react-hooks/exhaustive-deps -- khung vẽ chỉ gắn sau khi có draft

  // bộ kiểm tra chạy tự động sau mỗi thay đổi, debounce 300 ms (8.4.4)
  useEffect(() => {
    if (!draft) return;
    const t = window.setTimeout(() => {
      post<{ issues: Issue[] }>(`/api/sites/${draft.site.id}/validate`, draft).then((r) => setIssues(r.issues)).catch(() => undefined);
    }, 300);
    return () => window.clearTimeout(t);
  }, [draft]);

  const update = (fn: (d: Draft) => void) => {
    if (!draft || !admin) return;
    const d = clone(draft);
    fn(d);
    setDraft(d);
    setDirty(true);
    setAck(false);
  };

  const { n0, e0, scale } = view;
  const toPx = (n: number, e: number): [number, number] => [size.w / 2 + (e - e0) * scale, size.h / 2 - (n - n0) * scale];
  const toNed = (x: number, y: number): [number, number] => [n0 - (y - size.h / 2) / scale, e0 + (x - size.w / 2) / scale];
  const evNed = (ev: React.MouseEvent): [number, number] => {
    const r = svgRef.current!.getBoundingClientRect();
    return toNed(ev.clientX - r.left, ev.clientY - r.top);
  };
  const nearTag = (n: number, e: number, px = 12) => draft?.tags.findIndex((t) => Math.hypot(t.pos_n_m - n, t.pos_e_m - e) * scale < px) ?? -1;

  const enabledAreas = draft?.areas.map((a, i) => ({ a, i })).filter(({ a }) => a.enabled) ?? [];
  const hlTags = new Set(highlight?.refs.filter((r) => r.type === "tag").map((r) => r.tag_id!) ?? []);
  const hlAreas = new Set(highlight?.refs.filter((r) => r.type === "area").map((r) => enabledAreas[r.index!]?.i) ?? []);

  const bgM = useMemo(() => bgMatrix(draft?.site.bg_anchors ?? null, imgW), [draft?.site.bg_anchors, imgW]);

  // ── thao tác chuột ─────────────────────────────────────────────────────────────────────
  const onDown = (ev: React.MouseEvent) => {
    if (!draft) return;
    const [n, e] = evNed(ev);
    if (ev.button === 1 || ev.button === 2) { setDrag({ kind: "pan", sx: ev.clientX, sy: ev.clientY, n0, e0 }); return; }
    switch (tool) {
      case "select": {
        const ti = nearTag(n, e);
        if (ti >= 0) { setSelTag(ti); setSelArea(null); if (admin) setDrag({ kind: "tag", idx: ti }); return; }
        for (const { a, i } of enabledAreas) {
          const v = a.vertices.findIndex(([vn, ve]) => Math.hypot(vn - n, ve - e) * scale < 9);
          if (v >= 0) { setSelArea(i); setSelTag(null); if (admin) setDrag({ kind: "vertex", area: i, v }); return; }
        }
        setSelTag(null);
        setDrag({ kind: "pan", sx: ev.clientX, sy: ev.clientY, n0, e0 });
        return;
      }
      case "tag": {
        if (!admin) return;
        const nextId = Math.max(-1, ...draft.tags.map((t) => t.tag_id)) + 1;
        setNewTag({ tag_id: nextId, label: "", pos_n_m: snap(n, ev.shiftKey), pos_e_m: snap(e, ev.shiftKey), pos_d_m: 0, yaw_deg: 0, tag_size_m: draft.tags[0]?.tag_size_m ?? 0.3, kind: "pickup", landing_tol_m: 0.3, enabled: true });
        return;
      }
      case "home": {
        const ti = nearTag(n, e);
        if (ti >= 0) update((d) => { d.tags.forEach((t, i) => { if (i === ti) t.kind = "home"; else if (t.kind === "home") t.kind = "waypoint"; }); });
        return;
      }
      case "operating": case "no_fly": {
        if (!admin) return;
        const p: [number, number] = [snap(n, ev.shiftKey), snap(e, ev.shiftKey)];
        if (poly.length >= 3) {
          const [fx, fy] = toPx(poly[0][0], poly[0][1]);
          const [cx, cy] = toPx(n, e);
          if (Math.hypot(fx - cx, fy - cy) < 10) { // bấm đỉnh đầu → khép kín
            const kind = tool;
            update((d) => {
              if (kind === "operating") d.areas = d.areas.filter((a) => a.kind !== "operating"); // một vùng bay mỗi khu vực
              d.areas.push({ kind, name: kind === "operating" ? "Vùng bay" : `Vùng cấm ${d.areas.filter((a) => a.kind === "no_fly").length + 1}`, min_alt_m: 0, max_alt_m: kind === "operating" ? 20 : 30, enabled: true, vertices: poly });
            });
            setPoly([]);
            return;
          }
        }
        setPoly([...poly, p]);
        return;
      }
      case "measure": {
        const ti = nearTag(n, e);
        const p: [number, number] = ti >= 0 ? [draft.tags[ti].pos_n_m, draft.tags[ti].pos_e_m] : [n, e];
        setMeasure([p, p]);
        setDrag({ kind: "measure" });
        return;
      }
      case "gcs": update((d) => { d.site.gcs_pos_n_m = snap(n, ev.shiftKey); d.site.gcs_pos_e_m = snap(e, ev.shiftKey); }); setDrag({ kind: "gcs" }); return;
      case "bg": {
        if (!bgDataUrl && !bgUrl) return;
        // đảo biến đổi ảnh để lấy tọa độ pixel trong ảnh
        const [ar, ai, br, bi] = bgM;
        const wx = e - br, wy = -n - bi;
        const den = ar * ar + ai * ai;
        setPendingAnchor({ px: (ar * wx + ai * wy) / den, py: (-ai * wx + ar * wy) / den });
        setAnchorInput({ n: Math.round(n * 10) / 10, e: Math.round(e * 10) / 10 });
        return;
      }
    }
  };

  const onMove = (ev: React.MouseEvent) => {
    const [n, e] = evNed(ev);
    setCursor([n, e]);
    if (!drag || !draft) return;
    if (drag.kind === "pan") {
      setView({ ...view, n0: drag.n0 + (ev.clientY - drag.sy) / scale, e0: drag.e0 - (ev.clientX - drag.sx) / scale });
    } else if (drag.kind === "tag") {
      update((d) => { d.tags[drag.idx].pos_n_m = snap(n, ev.shiftKey); d.tags[drag.idx].pos_e_m = snap(e, ev.shiftKey); });
    } else if (drag.kind === "vertex") {
      update((d) => { d.areas[drag.area].vertices[drag.v] = [snap(n, ev.shiftKey), snap(e, ev.shiftKey)]; });
    } else if (drag.kind === "measure" && measure) {
      const ti = nearTag(n, e);
      setMeasure([measure[0], ti >= 0 ? [draft.tags[ti].pos_n_m, draft.tags[ti].pos_e_m] : [n, e]]);
    } else if (drag.kind === "gcs") {
      update((d) => { d.site.gcs_pos_n_m = snap(n, ev.shiftKey); d.site.gcs_pos_e_m = snap(e, ev.shiftKey); });
    }
  };

  const onWheel = (ev: React.WheelEvent) => {
    const [n, e] = evNed(ev);
    const k = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
    const s = Math.min(200, Math.max(0.5, scale * k));
    // giữ điểm dưới con trỏ đứng yên
    setView({ scale: s, n0: n - (n - n0) * (scale / s), e0: e - (e - e0) * (scale / s) });
  };

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape") { setPoly([]); setNewTag(null); setPendingAnchor(null); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!draft) return <div className="page">Đang tải…</div>;

  // ── lưu ────────────────────────────────────────────────────────────────────────────────
  const errors = issues.filter((i) => i.level === "error");
  const warnings = issues.filter((i) => i.level === "warning");
  const save = async () => {
    setMsg(null);
    try {
      const { id: _id, bg_image_path: _bg, ...siteBody } = draft.site;
      await put(`/api/sites/${draft.site.id}/design`, { site: siteBody, tags: draft.tags.map(({ id: _i, site_id: _s, ...t }) => t), areas: draft.areas.map(({ id: _i, ...a }) => a), ack_warnings: ack });
      if (bgDataUrl) {
        await post(`/api/sites/${draft.site.id}/bg-image`, { data_url: bgDataUrl, anchors: draft.site.bg_anchors });
        setBgDataUrl(null);
      }
      setDirty(false);
      await site.load(draft.site.id);
      setMsg("Đã lưu. map_crc đã tính lại — nhớ đồng bộ bản đồ xuống drone (tab Tag).");
    } catch (e) {
      if (e instanceof ApiError && Array.isArray(e.data)) setIssues(e.data as Issue[]);
      setMsg(errMsg(e));
    }
  };

  const focusIssue = (i: Issue) => {
    setHighlight(i);
    if (i.at) setView({ ...view, n0: i.at[0], e0: i.at[1] });
  };

  const exportPng = () => {
    const svg = svgRef.current!;
    const xml = new XMLSerializer().serializeToString(svg);
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = size.w * 2; c.height = size.h * 2;
      const ctx = c.getContext("2d")!;
      ctx.scale(2, 2);
      ctx.fillStyle = "#f4f7ef"; ctx.fillRect(0, 0, size.w, size.h);
      ctx.drawImage(img, 0, 0);
      const a = document.createElement("a");
      a.download = `so_do_${draft.site.name}.png`;
      a.href = c.toDataURL("image/png");
      a.click();
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
  };

  const home = draft.tags.find((t) => t.kind === "home" && t.enabled);
  const grid = (() => {
    if (!layers.grid) return null;
    const step = scale > 20 ? 1 : scale > 4 ? 5 : 10;
    const [nTop, eLeft] = toNed(0, 0), [nBot, eRight] = toNed(size.w, size.h);
    const lines = [];
    for (let e = Math.floor(eLeft / step) * step; e <= eRight; e += step) {
      const [x] = toPx(0, e);
      lines.push(<line key={`e${e}`} x1={x} x2={x} y1={0} y2={size.h} className={e % (step * 5) === 0 ? "grid-major" : "grid-minor"} />);
    }
    for (let n = Math.floor(nBot / step) * step; n <= nTop; n += step) {
      const [, y] = toPx(n, 0);
      lines.push(<line key={`n${n}`} y1={y} y2={y} x1={0} x2={size.w} className={n % (step * 5) === 0 ? "grid-major" : "grid-minor"} />);
    }
    return <g>{lines}</g>;
  })();

  const [gx, gy] = toPx(draft.site.gcs_pos_n_m, draft.site.gcs_pos_e_m);
  const meas = draft.site.link_radius_meas_m;
  const routeTags = route.a !== "" && route.b !== "" && home ? [home, draft.tags.find((t) => t.tag_id === route.a), draft.tags.find((t) => t.tag_id === route.b), home].filter(Boolean) as Tag[] : [];

  return (
    <div className="design-layout">
      <div className="design-tools panel">
        <h3>Công cụ</h3>
        {TOOLS.map(([t, label]) => (
          <button key={t} className={tool === t ? "active" : ""} disabled={!admin && t !== "select" && t !== "measure"} onClick={() => { setTool(t); setPoly([]); }}>{label}</button>
        ))}
        <p className="muted small">Shift = bắt lưới 0,5 m · lăn chuột = zoom · kéo nền = di chuyển · Esc = hủy</p>
        <h3>Lớp</h3>
        {LAYERS.map(([l, label]) => (
          <label key={l} className="check"><input type="checkbox" checked={layers[l]} onChange={(e) => setLayers({ ...layers, [l]: e.target.checked })} /> {label}</label>
        ))}
        {layers.route && (
          <div className="form-grid small">
            <label>Tuyến mẫu: lấy<select value={route.a} onChange={(e) => setRoute({ ...route, a: e.target.value === "" ? "" : +e.target.value })}><option value="">—</option>{draft.tags.map((t) => <option key={t.tag_id} value={t.tag_id}>{t.label}</option>)}</select></label>
            <label>giao<select value={route.b} onChange={(e) => setRoute({ ...route, b: e.target.value === "" ? "" : +e.target.value })}><option value="">—</option>{draft.tags.map((t) => <option key={t.tag_id} value={t.tag_id}>{t.label}</option>)}</select></label>
          </div>
        )}
        {tool === "bg" && admin && (
          <div className="small">
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(ev) => {
              const f = ev.target.files?.[0];
              if (!f) return;
              const r = new FileReader();
              r.onload = () => { setBgDataUrl(r.result as string); update((d) => { d.site.bg_anchors = null; }); };
              r.readAsDataURL(f);
            }} />
            <p className="muted">Bấm lên ảnh tại 2 điểm mốc rồi nhập tọa độ thật (N, E). Tỉ lệ + góc xoay tự tính — không căn bằng mắt.</p>
            <div>Điểm mốc: {draft.site.bg_anchors?.length ?? 0}/2 {draft.site.bg_anchors?.length ? <button className="link" onClick={() => update((d) => { d.site.bg_anchors = null; })}>xóa</button> : null}</div>
          </div>
        )}
        <div className="btn-col">
          <button className="primary" disabled={!admin || !dirty || errors.length > 0 || (warnings.length > 0 && !ack)} onClick={save}>Lưu thiết kế</button>
          {warnings.length > 0 && <label className="check small"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> Tôi đã hiểu {warnings.length} cảnh báo</label>}
          <button onClick={() => { if (!dirty || confirm("Có thay đổi chưa lưu. Vẫn mở 3D?")) navigate("/"); }}>Xem trong 3D</button>
          <button onClick={exportPng}>Xuất PNG</button>
          {dirty && <button className="link" onClick={() => { setDirty(false); setDraft({ site: clone(site.site!), tags: clone(site.tags), areas: clone(site.areas) }); }}>Bỏ thay đổi</button>}
        </div>
        {msg && <div className="small note">{msg}</div>}
      </div>

      <div className="design-canvas" ref={wrapRef}>
        <svg ref={svgRef} width={size.w} height={size.h} xmlns="http://www.w3.org/2000/svg"
          onMouseDown={onDown} onMouseMove={onMove} onMouseUp={() => { setDrag(null); if (tool !== "measure") setMeasure(null); }}
          onMouseLeave={() => { setDrag(null); setCursor(null); }} onWheel={onWheel} onContextMenu={(e) => e.preventDefault()}
          className={`tool-${tool}`}>
          <defs>
            <pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="8" stroke="#ef4444" strokeWidth="3" />
            </pattern>
          </defs>
          <rect width={size.w} height={size.h} fill="#f4f7ef" />
          {layers.bg && (bgDataUrl || bgUrl) && (
            <g transform={`translate(${size.w / 2 - e0 * scale} ${size.h / 2 + n0 * scale}) scale(${scale}) matrix(${bgM[0]} ${bgM[1]} ${-bgM[1]} ${bgM[0]} ${bgM[2]} ${bgM[3]})`}>
              <image href={bgDataUrl ?? bgUrl!} opacity={0.55} onLoad={(ev) => setImgW((ev.target as SVGImageElement).getBBox().width || 1000)} />
              {draft.site.bg_anchors?.map((a, i) => <circle key={i} cx={a.px} cy={a.py} r={6 / scale / Math.hypot(bgM[0], bgM[1])} fill="#ca8a04" />)}
            </g>
          )}
          {grid}
          {layers.link && (
            <g>
              {meas ? <>
                <circle cx={gx} cy={gy} r={meas * scale} className="link-meas" />
                <circle cx={gx} cy={gy} r={meas * 0.7 * scale} className="link-safe" />
                <text x={gx + meas * 0.7 * scale * 0.707 + 4} y={gy - meas * 0.7 * scale * 0.707} className="lbl small-lbl">biên an toàn 70 % · {(meas * 0.7).toFixed(0)} m</text>
              </> : <text x={gx + 12} y={gy + 22} className="lbl warn-lbl">bán kính liên kết: chưa đo (chưa chạy T2)</text>}
              {draft.site.design_radius_m ? <circle cx={gx} cy={gy} r={draft.site.design_radius_m * scale} className="link-design" /> : null}
              <rect x={gx - 6} y={gy - 6} width={12} height={12} className="gcs-mark" />
              <text x={gx + 9} y={gy + 4} className="lbl">Trạm GCS</text>
            </g>
          )}
          {draft.areas.map((a, i) => {
            if (!a.enabled || !layers[a.kind]) return null;
            const pts = a.vertices.map(([n, e]) => toPx(n, e).join(",")).join(" ");
            const cls = `${a.kind === "operating" ? "area-op" : "area-nf"}${hlAreas.has(i) || selArea === i ? " hl" : ""}`;
            return (
              <g key={i}>
                {a.kind === "no_fly" && <polygon points={pts} fill="url(#hatch)" opacity={0.35} />}
                <polygon points={pts} className={cls} />
                {(selArea === i || tool === "select") && a.vertices.map(([n, e], v) => { const [x, y] = toPx(n, e); return <circle key={v} cx={x} cy={y} r={4} className="vertex" />; })}
                {a.vertices[0] && <text x={toPx(...a.vertices[0])[0] + 6} y={toPx(...a.vertices[0])[1] - 6} className="lbl small-lbl">{a.name}{a.kind === "operating" ? " — cảnh báo, không cưỡng chế" : ""}</text>}
              </g>
            );
          })}
          {poly.length > 0 && <polyline points={[...poly, ...(cursor ? [cursor] : [])].map(([n, e]) => toPx(n, e).join(",")).join(" ")} className="drawing" />}
          {layers.homeLines && home && draft.tags.filter((t) => t !== home && t.enabled).map((t) => {
            const [x1, y1] = toPx(home.pos_n_m, home.pos_e_m), [x2, y2] = toPx(t.pos_n_m, t.pos_e_m);
            return <g key={t.tag_id}><line x1={x1} y1={y1} x2={x2} y2={y2} className="home-line" /><text x={(x1 + x2) / 2} y={(y1 + y2) / 2} className="lbl small-lbl">{Math.hypot(t.pos_n_m - home.pos_n_m, t.pos_e_m - home.pos_e_m).toFixed(1)} m</text></g>;
          })}
          {layers.route && routeTags.length === 4 && <polyline points={routeTags.map((t) => toPx(t.pos_n_m, t.pos_e_m).join(",")).join(" ")} className="route" />}
          {layers.tags && draft.tags.map((t, i) => {
            const [x, y] = toPx(t.pos_n_m, t.pos_e_m);
            const r = Math.max(5, (t.tag_size_m / 2) * scale);
            const hl = hlTags.has(t.tag_id) || selTag === i;
            return (
              <g key={i} opacity={t.enabled ? 1 : 0.4}>
                <circle cx={x} cy={y} r={Math.max(t.landing_tol_m * scale, r + 2)} className="tol" />
                <rect x={x - r} y={y - r} width={r * 2} height={r * 2} fill={KIND_COLOR[t.kind]} className={hl ? "tag hl" : "tag"}
                  transform={`rotate(${t.yaw_deg} ${x} ${y})`} />
                <text x={x + r + 4} y={y - r - 2} className="lbl">{t.label} #{t.tag_id}</text>
              </g>
            );
          })}
          {measure && (() => {
            const [a, b] = measure;
            const [x1, y1] = toPx(...a), [x2, y2] = toPx(...b);
            return <g><line x1={x1} y1={y1} x2={x2} y2={y2} className="measure" /><text x={x2 + 8} y={y2 - 8} className="lbl measure-lbl">{Math.hypot(a[0] - b[0], a[1] - b[1]).toFixed(2)} m</text></g>;
          })()}
          {cursor && <text x={8} y={size.h - 10} className="lbl">N {cursor[0].toFixed(2)} · E {cursor[1].toFixed(2)} m · 1 m = {scale.toFixed(1)} px</text>}
          {/* chỉ hướng Bắc */}
          <g transform={`translate(${size.w - 30} 40)`}><polygon points="0,-18 7,6 0,0 -7,6" fill="#dc2626" /><text x={-4} y={22} className="lbl">N</text></g>
        </svg>

        {newTag && (
          <div className="dialog">
            <h4>Đặt tag mới</h4>
            <label>ID<input type="number" value={newTag.tag_id} onChange={(e) => setNewTag({ ...newTag, tag_id: +e.target.value })} /></label>
            {draft.tags.some((t) => t.tag_id === newTag.tag_id) && <div className="error small">ID {newTag.tag_id} đã tồn tại</div>}
            <label>Nhãn<input autoFocus value={newTag.label} onChange={(e) => setNewTag({ ...newTag, label: e.target.value })} /></label>
            <label>Loại<select value={newTag.kind} onChange={(e) => setNewTag({ ...newTag, kind: e.target.value as TagKind })}>{["home", "pickup", "dropoff", "waypoint"].map((k) => <option key={k}>{k}</option>)}</select></label>
            <label>Kích thước in (m)<input type="number" step={0.01} value={newTag.tag_size_m} onChange={(e) => setNewTag({ ...newTag, tag_size_m: +e.target.value })} /></label>
            <div className="btn-row">
              <button className="primary" disabled={!newTag.label || draft.tags.some((t) => t.tag_id === newTag.tag_id)} onClick={() => {
                update((d) => { if (newTag.kind === "home") d.tags.forEach((t) => { if (t.kind === "home") t.kind = "waypoint"; }); d.tags.push(newTag); });
                setNewTag(null);
              }}>Thêm</button>
              <button onClick={() => setNewTag(null)}>Hủy</button>
            </div>
          </div>
        )}
        {pendingAnchor && (
          <div className="dialog">
            <h4>Điểm mốc {(draft.site.bg_anchors?.length ?? 0) + 1}/2</h4>
            <div className="small muted">pixel ảnh ({pendingAnchor.px.toFixed(0)}, {pendingAnchor.py.toFixed(0)})</div>
            <label>N thật (m)<input type="number" step={0.01} value={anchorInput.n} onChange={(e) => setAnchorInput({ ...anchorInput, n: +e.target.value })} /></label>
            <label>E thật (m)<input type="number" step={0.01} value={anchorInput.e} onChange={(e) => setAnchorInput({ ...anchorInput, e: +e.target.value })} /></label>
            <div className="btn-row">
              <button className="primary" onClick={() => {
                update((d) => { const cur = d.site.bg_anchors ?? []; d.site.bg_anchors = [...(cur.length >= 2 ? [] : cur), { ...pendingAnchor, ...anchorInput }]; });
                setPendingAnchor(null);
              }}>Ghi điểm mốc</button>
              <button onClick={() => setPendingAnchor(null)}>Hủy</button>
            </div>
          </div>
        )}
      </div>

      <div className="design-side">
        <div className="panel">
          <h3>Kiểm tra thiết kế <span className={errors.length ? "bad" : warnings.length ? "warn" : "good"}>{errors.length} lỗi · {warnings.length} cảnh báo</span></h3>
          <ul className="issues">
            {issues.map((i, k) => (
              <li key={k} className={`${i.level}${highlight === i ? " sel" : ""}`} onClick={() => focusIssue(i)}><code>{i.code}</code> {i.message}</li>
            ))}
            {!issues.length && <li className="good">Không có lỗi hay cảnh báo</li>}
          </ul>
          {errors.length > 0 && <div className="small error">Còn Lỗi → không lưu được và không đồng bộ bản đồ.</div>}
        </div>
        <div className="panel">
          <h3>Khu vực</h3>
          <div className="form-grid small">
            <label>Tên<input value={draft.site.name} disabled={!admin} onChange={(e) => update((d) => { d.site.name = e.target.value; })} /></label>
            <label>Cao hành trình mặc định (m)<input type="number" value={draft.site.cruise_alt_default_m} disabled={!admin} onChange={(e) => update((d) => { d.site.cruise_alt_default_m = +e.target.value; })} /></label>
            <label>Bán kính mục tiêu (m)<input type="number" value={draft.site.design_radius_m ?? ""} disabled={!admin} onChange={(e) => update((d) => { d.site.design_radius_m = e.target.value === "" ? null : +e.target.value; })} /></label>
            <label>Bán kính liên kết ĐO ĐƯỢC từ T2 (m)<input type="number" placeholder="chưa đo" value={draft.site.link_radius_meas_m ?? ""} disabled={!admin} onChange={(e) => update((d) => { d.site.link_radius_meas_m = e.target.value === "" ? null : +e.target.value; })} /></label>
            <label>Trạm GCS N<input type="number" value={draft.site.gcs_pos_n_m} disabled={!admin} onChange={(e) => update((d) => { d.site.gcs_pos_n_m = +e.target.value; })} /></label>
            <label>Trạm GCS E<input type="number" value={draft.site.gcs_pos_e_m} disabled={!admin} onChange={(e) => update((d) => { d.site.gcs_pos_e_m = +e.target.value; })} /></label>
          </div>
        </div>
        <div className="panel">
          <h3>Tag (nhập số — bàn phím luôn thắng chuột)</h3>
          <div className="table-wrap">
            <table className="grid small">
              <thead><tr><th>ID</th><th>Nhãn</th><th>Loại</th><th>N</th><th>E</th><th>D</th><th>Cỡ</th><th /></tr></thead>
              <tbody>{draft.tags.map((t, i) => (
                <tr key={i} className={selTag === i ? "sel" : ""} onClick={() => setSelTag(i)}>
                  <td><input className="num" type="number" value={t.tag_id} disabled={!admin} onChange={(e) => update((d) => { d.tags[i].tag_id = +e.target.value; })} /></td>
                  <td><input value={t.label} disabled={!admin} onChange={(e) => update((d) => { d.tags[i].label = e.target.value; })} /></td>
                  <td><select value={t.kind} disabled={!admin} onChange={(e) => update((d) => { if (e.target.value === "home") d.tags.forEach((x) => { if (x.kind === "home") x.kind = "waypoint"; }); d.tags[i].kind = e.target.value as TagKind; })}>{["home", "pickup", "dropoff", "waypoint"].map((k) => <option key={k}>{k}</option>)}</select></td>
                  {(["pos_n_m", "pos_e_m", "pos_d_m", "tag_size_m"] as const).map((k) => (
                    <td key={k}><input className="num" type="number" step={0.01} value={t[k]} disabled={!admin} onChange={(e) => update((d) => { d.tags[i][k] = +e.target.value; })} /></td>
                  ))}
                  <td>{admin && <button className="link danger" onClick={(e) => { e.stopPropagation(); update((d) => { d.tags.splice(i, 1); }); setSelTag(null); }}>✕</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
        {selArea !== null && draft.areas[selArea] && (
          <div className="panel">
            <h3>Vùng: {draft.areas[selArea].name}</h3>
            <div className="form-grid small">
              <label>Tên<input value={draft.areas[selArea].name} disabled={!admin} onChange={(e) => update((d) => { d.areas[selArea].name = e.target.value; })} /></label>
              <label>Cao tối đa (m)<input type="number" value={draft.areas[selArea].max_alt_m} disabled={!admin} onChange={(e) => update((d) => { d.areas[selArea].max_alt_m = +e.target.value; })} /></label>
            </div>
            <table className="grid small">
              <thead><tr><th>#</th><th>N</th><th>E</th><th /></tr></thead>
              <tbody>{draft.areas[selArea].vertices.map((v, k) => (
                <tr key={k}><td>{k}</td>
                  {[0, 1].map((c) => <td key={c}><input className="num" type="number" step={0.1} value={v[c]} disabled={!admin} onChange={(e) => update((d) => { d.areas[selArea].vertices[k][c] = +e.target.value; })} /></td>)}
                  <td>{admin && <button className="link" onClick={() => update((d) => { d.areas[selArea].vertices.splice(k, 1); })}>✕</button>}</td>
                </tr>
              ))}</tbody>
            </table>
            {admin && <button className="danger" onClick={() => { update((d) => { d.areas.splice(selArea, 1); }); setSelArea(null); }}>Xóa vùng</button>}
          </div>
        )}
      </div>
    </div>
  );
}
