// Tờ in BÃI ĐÁP khổ A3 dọc: tag to (tâm bãi) + tag nhỏ phía TRÊN, cùng chiều, đúng khoảng cách.
// Mẫu tag nhỏ phải trùng mẫu Pi tự suy (giao ước GCS <-> Pi 0.8 mục 8.7, tags.yaml pad_small_tag_*):
// id = id tag to + 10, cạnh 100 mm, tâm cách tâm tag to 210 mm về phía mép TRÊN (220 -> 210, 10-09). Đổi mẫu thì sửa cả
// tags.yaml phía Pi, không thì Pi đặt tag nhỏ sai chỗ so với tờ in.
import { TAG36H11_MAX_ID, tag36h11Grid } from "../../lib/apriltag36h11";
import type { Tag } from "../../lib/types";

const PAD_SMALL = { idOffset: 10, sizeMm: 100, forwardMm: 210 };
const PAGE_W = 297, PAGE_H = 420;   // A3 dọc (mm)
const PRINT_MARGIN = 5;             // lề máy in không in tới được
const BORDER_CELLS = 8;             // cạnh đen 36h11 = 8 ô (lib/apriltag36h11)

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

// Hình cơ bản dùng chung cho bản HTML (SVG) và bản PDF - hai bản luôn trùng nhau. Đơn vị mm, gốc trên-trái.
type Prim =
  | { k: "box"; x: number; y: number; w: number; h: number; stroke?: number }   // stroke: khung rỗng
  | { k: "line"; x1: number; y1: number; x2: number; y2: number; w: number }
  | { k: "poly"; pts: [number, number][] }
  | { k: "text"; x: number; y: number; size: number; s: string };

/** Các ô đen của tag, đặt sao cho MÉP ĐEN có góc trên-trái tại (x, y), cạnh đen sizeMm. */
function tagCells(tagId: number, x: number, y: number, sizeMm: number): Prim[] {
  const grid = tag36h11Grid(tagId);
  const cell = sizeMm / BORDER_CELLS;
  const out: Prim[] = [];
  grid.forEach((row, r) => row.forEach((white, c) => {
    // Chồng 0,05 mm: các ô kề nhau không lộ khe khử răng cưa thành vạch trắng khi in.
    if (!white) out.push({ k: "box", x: x + (c - 1) * cell, y: y + (r - 1) * cell, w: cell + 0.05, h: cell + 0.05 });
  }));
  return out;
}

type Pad = { prims: Prim[]; warn: string[] } | { error: string };

function buildPad(t: Tag): Pad {
  const S = t.tag_size_m * 1000, s = PAD_SMALL.sizeMm, F = PAD_SMALL.forwardMm;
  const cellB = S / BORDER_CELLS, cellS = s / BORDER_CELLS;
  // Tag nhỏ không được đè viền trắng (1 ô) của tag to.
  if (F - s / 2 - S / 2 < cellB) {
    const maxS = (F - s / 2) / (0.5 + 1 / BORDER_CELLS);
    return { error: `cạnh ${S.toFixed(0)} mm quá lớn cho mẫu bãi — tag nhỏ sẽ đè viền trắng tag to (tối đa ${maxS.toFixed(0)} mm)` };
  }
  const cx = PAGE_W / 2;
  const smallTop = PRINT_MARGIN + cellS;               // đủ viền trắng 1 ô phía trên tag nhỏ
  // F = 210 mm (10-09): tag to cách mép dưới 17,5 mm thay vì 7,5 mm khi F còn 220.
  const bigCy = smallTop + s / 2 + F;
  const bigBottom = bigCy + S / 2;
  if (bigBottom > PAGE_H - PRINT_MARGIN || S > PAGE_W - 2 * PRINT_MARGIN) {
    return { error: `cạnh ${S.toFixed(0)} mm không vừa khổ A3 cùng tag nhỏ` };
  }
  const warn: string[] = [];
  const side = (PAGE_W - S) / 2, bottom = PAGE_H - bigBottom;
  if (side < cellB || bottom < cellB) {
    warn.push(`viền trắng quanh tag to chỉ còn ${Math.min(side, bottom).toFixed(0)} mm (cần ≥ 1 ô = ${cellB.toFixed(0)} mm) — ` +
      "đặt bãi trên nền trắng hoặc dán thêm giấy trắng ở hai bên / phía dưới");
  }
  const small = t.tag_id + PAD_SMALL.idOffset;
  const smallCy = smallTop + s / 2;
  const textX = 8;                                      // bên trái tag nhỏ, ngoài viền trắng của nó
  const lines = [`▲ TRÊN = hướng bãi`, `Bãi: ${t.label} (${t.kind})`, `Tag to: id ${t.tag_id}, cạnh ${S.toFixed(0)} mm`,
    `Tag nhỏ: id ${small}, cạnh ${s} mm`, `Tâm nhỏ → tâm to: ${F} mm`];
  const prims: Prim[] = [...tagCells(small, cx - s / 2, smallTop, s), ...tagCells(t.tag_id, cx - S / 2, bigCy - S / 2, S)];
  lines.forEach((l, i) => prims.push({ k: "text", x: textX, y: smallCy - 14 + i * 7, size: 4.6, s: l }));
  // Mũi tên TRÊN và ô hiệu chỉnh 20 mm: bên phải tag nhỏ, trên viền trắng tag to.
  const arrowX = PAGE_W - 25, arrowBot = bigCy - S / 2 - cellB - 4, arrowTop = smallTop;
  prims.push({ k: "line", x1: arrowX, y1: arrowBot, x2: arrowX, y2: arrowTop + 8, w: 1.2 },
    { k: "poly", pts: [[arrowX, arrowTop], [arrowX - 5, arrowTop + 10], [arrowX + 5, arrowTop + 10]] },
    { k: "text", x: arrowX - 9, y: arrowBot, size: 4.6, s: "TRÊN" });
  const calX = cx + s / 2 + cellS + 6, calY = smallCy - 10;
  prims.push({ k: "box", x: calX, y: calY, w: 20, h: 20, stroke: 0.4 },
    { k: "text", x: calX + 2, y: calY + 11, size: 3.5, s: "20 mm" });
  return { prims, warn };
}

function toSvg(prims: Prim[]): string {
  const f = (v: number) => v.toFixed(3);
  const body = prims.map((p) => {
    switch (p.k) {
      case "box": return p.stroke
        ? `<rect x="${f(p.x)}" y="${f(p.y)}" width="${f(p.w)}" height="${f(p.h)}" fill="none" stroke="#000" stroke-width="${p.stroke}"/>`
        : `<rect x="${f(p.x)}" y="${f(p.y)}" width="${f(p.w)}" height="${f(p.h)}"/>`;
      case "line": return `<line x1="${f(p.x1)}" y1="${f(p.y1)}" x2="${f(p.x2)}" y2="${f(p.y2)}" stroke="#000" stroke-width="${p.w}"/>`;
      case "poly": return `<polygon points="${p.pts.map(([x, y]) => `${f(x)},${f(y)}`).join(" ")}"/>`;
      case "text": return `<text x="${f(p.x)}" y="${f(p.y)}" font-size="${p.size}">${esc(p.s)}</text>`;
    }
  }).join("");
  // crispEdges ở <g>: ô tag không bị khử răng cưa thành viền xám.
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${PAGE_W}mm" height="${PAGE_H}mm" viewBox="0 0 ${PAGE_W} ${PAGE_H}">` +
    `<rect width="${PAGE_W}" height="${PAGE_H}" fill="#fff"/><g fill="#000" font-family="sans-serif" shape-rendering="crispEdges">` +
    body + `</g></svg>`;
}

/** Các bãi in được (tag to đang Bật, id 0–9) + ghi chú bãi bị bỏ / cảnh báo. */
function buildPads(tags: Tag[]): { pages: Prim[][]; notes: string[] } {
  const pads = tags.filter((t) => t.enabled).sort((a, b) => a.tag_id - b.tag_id);
  const pages: Prim[][] = [], notes: string[] = [];
  for (const t of pads) {
    if (t.tag_id > 9 || t.tag_id + PAD_SMALL.idOffset > TAG36H11_MAX_ID) {
      notes.push(`Bỏ qua tag ${t.tag_id} (${t.label}): chỉ tag 0–9 có tag nhỏ (10–19) mà Pi khai trong apriltag.yaml`);
      continue;
    }
    const p = buildPad(t);
    if ("error" in p) { notes.push(`Bỏ qua bãi ${t.label} (tag ${t.tag_id}): ${p.error}`); continue; }
    p.warn.forEach((w) => notes.push(`Bãi ${t.label} (tag ${t.tag_id}): ${w}`));
    pages.push(p.prims);
  }
  return { pages, notes };
}

/** PDF vector khổ A3 dọc ĐÚNG KÍCH THƯỚC, mỗi bãi một trang - không phụ thuộc hộp thoại in của trình duyệt
 * (in HTML ở khổ giấy khác A3 thì tag tràn sang tờ sau). Chữ dùng Helvetica có sẵn của PDF, không nhúng font,
 * nên bỏ dấu tiếng Việt; tag và khoảng cách vẫn chính xác. */
export function buildPadPdf(tags: Tag[]): { pdf: Blob | null; notes: string[] } {
  const { pages, notes } = buildPads(tags);
  if (!pages.length) return { pdf: null, notes };
  const K = 72 / 25.4, H = PAGE_H * K;                 // mm -> pt; PDF gốc dưới-trái nên lật trục y
  const X = (v: number) => (v * K).toFixed(3), Y = (v: number) => (H - v * K).toFixed(3);
  const ascii = (t: string) => t.replace(/đ/g, "d").replace(/Đ/g, "D").normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/▲\s*/g, "").replace(/→/g, "->").replace(/[^\x20-\x7e]/g, "").replace(/[\\()]/g, (c) => "\\" + c);
  const content = (prims: Prim[]) => prims.map((p) => {
    switch (p.k) {
      case "box": return p.stroke
        ? `${X(p.stroke)} w ${X(p.x)} ${Y(p.y + p.h)} ${X(p.w)} ${X(p.h)} re S`
        : `${X(p.x)} ${Y(p.y + p.h)} ${X(p.w)} ${X(p.h)} re f`;
      case "line": return `${X(p.w)} w ${X(p.x1)} ${Y(p.y1)} m ${X(p.x2)} ${Y(p.y2)} l S`;
      case "poly": return p.pts.map(([x, y], i) => `${X(x)} ${Y(y)} ${i ? "l" : "m"}`).join(" ") + " h f";
      case "text": return `BT /F1 ${X(p.size)} Tf ${X(p.x)} ${Y(p.y)} Td (${ascii(p.s)}) Tj ET`;
    }
  }).join("\n");
  // Đối tượng: 1 catalog, 2 pages, 3 font, rồi mỗi trang (page, content). Toàn ASCII nên độ dài chuỗi = số byte.
  const objs = ["<< /Type /Catalog /Pages 2 0 R >>", "",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"];
  const kids: string[] = [];
  for (const prims of pages) {
    const c = "0 g 0 G\n" + content(prims), n = objs.length + 1;
    kids.push(`${n} 0 R`);
    objs.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${X(PAGE_W)} ${H.toFixed(3)}] ` +
      `/Resources << /Font << /F1 3 0 R >> >> /Contents ${n + 1} 0 R >>`,
      `<< /Length ${c.length} >>\nstream\n${c}\nendstream`);
  }
  objs[1] = `<< /Type /Pages /Kids [${kids.join(" ")}] /Count ${kids.length} >>`;
  let out = "%PDF-1.4\n";
  const offs = objs.map((o, i) => { const at = out.length; out += `${i + 1} 0 obj\n${o}\nendobj\n`; return at; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n` + offs.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("") +
    `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return { pdf: new Blob([out], { type: "application/pdf" }), notes };
}

/** Trang HTML nhiều trang A3, mỗi bãi (tag to đang Bật, id 0–9) một trang. */
export function buildPadSheet(tags: Tag[]): string {
  const built = buildPads(tags);
  const pages = built.pages.map((p) => `<div class="page">${toSvg(p)}</div>`), notes = built.notes.map(esc);
  return `<!doctype html><html><head><meta charset="utf-8"><title>In bãi đáp A3</title><style>
@page { size: A3 portrait; margin: 0; }
body { margin: 0; font-family: sans-serif; }
.note { font-size: 13px; color: #333; max-width: 720px; margin: 12px; }
.warn { color: #b00020; }
.page { width: 297mm; height: 420mm; page-break-after: always; break-after: page; }
.page svg { display: block; }
@media print { .note { display: none; } }
</style></head><body>
<div class="note"><b>In khổ A3 dọc, tỷ lệ 100%</b> — KHÔNG chọn "Vừa trang" / "Fit to page" và tắt lề của trình duyệt
(Lề = Không). Sau khi in, đo ô hiệu chỉnh: cạnh phải đúng 20 mm, và đo cạnh đen tag to bằng thước rồi ghi vào
apriltag.yaml phía Pi. Đặt bãi sao cho mũi tên TRÊN chỉ đúng "Hướng°" đã khai ở trang Tag rồi đánh dấu "Đã đo".
${notes.map((n) => `<p class="warn">${n}</p>`).join("")}
${pages.length ? "" : '<p class="warn">Không có bãi nào in được.</p>'}</div>
${pages.join("\n")}
</body></html>`;
}
