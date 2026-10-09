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

/** Các ô đen của tag, đặt sao cho MÉP ĐEN có góc trên-trái tại (x, y), cạnh đen sizeMm. */
function tagRects(tagId: number, x: number, y: number, sizeMm: number): string {
  const grid = tag36h11Grid(tagId);
  const cell = sizeMm / BORDER_CELLS;
  let out = "";
  grid.forEach((row, r) => row.forEach((white, c) => {
    // crispEdges ở <g> + chồng 0,05 mm: các ô kề nhau không lộ khe khử răng cưa thành vạch trắng khi in.
    if (!white) out += `<rect x="${(x + (c - 1) * cell).toFixed(3)}" y="${(y + (r - 1) * cell).toFixed(3)}" width="${(cell + 0.05).toFixed(3)}" height="${(cell + 0.05).toFixed(3)}"/>`;
  }));
  return out;
}

type Pad = { svg: string; warn: string[] } | { error: string };

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
  const lines = [`▲ TRÊN = hướng bãi`, `Bãi: ${esc(t.label)} (${t.kind})`, `Tag to: id ${t.tag_id}, cạnh ${S.toFixed(0)} mm`,
    `Tag nhỏ: id ${small}, cạnh ${s} mm`, `Tâm nhỏ → tâm to: ${F} mm`];
  const text = lines.map((l, i) => `<text x="${textX}" y="${(smallCy - 14 + i * 7).toFixed(1)}" font-size="4.6">${l}</text>`).join("");
  // Mũi tên TRÊN và ô hiệu chỉnh 20 mm: bên phải tag nhỏ, trên viền trắng tag to.
  const arrowX = PAGE_W - 25, arrowBot = bigCy - S / 2 - cellB - 4, arrowTop = smallTop;
  const arrow = `<line x1="${arrowX}" y1="${arrowBot.toFixed(1)}" x2="${arrowX}" y2="${(arrowTop + 8).toFixed(1)}" stroke="#000" stroke-width="1.2"/>` +
    `<polygon points="${arrowX},${arrowTop} ${arrowX - 5},${arrowTop + 10} ${arrowX + 5},${arrowTop + 10}"/>` +
    `<text x="${arrowX - 9}" y="${(arrowBot + 0).toFixed(1)}" font-size="4.6">TRÊN</text>`;
  const calX = cx + s / 2 + cellS + 6, calY = smallCy - 10;
  const cal = `<rect x="${calX}" y="${calY}" width="20" height="20" fill="none" stroke="#000" stroke-width="0.4"/>` +
    `<text x="${calX + 2}" y="${calY + 11}" font-size="3.5">20 mm</text>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${PAGE_W}mm" height="${PAGE_H}mm" viewBox="0 0 ${PAGE_W} ${PAGE_H}">` +
    `<rect width="${PAGE_W}" height="${PAGE_H}" fill="#fff"/><g fill="#000" font-family="sans-serif" shape-rendering="crispEdges">` +
    tagRects(small, cx - s / 2, smallTop, s) + tagRects(t.tag_id, cx - S / 2, bigCy - S / 2, S) + text + arrow + cal +
    `</g></svg>`;
  return { svg, warn };
}

/** Trang HTML nhiều trang A3, mỗi bãi (tag to đang Bật, id 0–9) một trang. */
export function buildPadSheet(tags: Tag[]): string {
  const pads = tags.filter((t) => t.enabled).sort((a, b) => a.tag_id - b.tag_id);
  const pages: string[] = [], notes: string[] = [];
  for (const t of pads) {
    if (t.tag_id > 9 || t.tag_id + PAD_SMALL.idOffset > TAG36H11_MAX_ID) {
      notes.push(`Bỏ qua tag ${t.tag_id} (${esc(t.label)}): chỉ tag 0–9 có tag nhỏ (10–19) mà Pi khai trong apriltag.yaml`);
      continue;
    }
    const p = buildPad(t);
    if ("error" in p) { notes.push(`Bỏ qua bãi ${esc(t.label)} (tag ${t.tag_id}): ${p.error}`); continue; }
    p.warn.forEach((w) => notes.push(`Bãi ${esc(t.label)} (tag ${t.tag_id}): ${w}`));
    pages.push(`<div class="page">${p.svg}</div>`);
  }
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
