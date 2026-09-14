// Điều khiển tay ở mức vị trí (F2) — lệnh vị trí RỜI RẠC, không có cần điều khiển ảo (10.3).
import { useState } from "react";
import { errMsg, post } from "../../lib/api";
import type { Command, Tag } from "../../lib/types";
import { useLive } from "../../store/live";

const STAGE_CLS: Record<string, string> = { effective: "good", rejected: "bad", failed: "bad", not_effective: "bad", cancelled: "muted" };

export function ManualControl({ tags, gotoTag }: { tags: Tag[]; gotoTag: number | null }) {
  const commands = useLive((s) => s.commands);
  const [err, setErr] = useState<string | null>(null);
  const [alt, setAlt] = useState(5);
  const [frame, setFrame] = useState<"MAP_NED" | "TAG_RELATIVE" | "BODY_RELATIVE">("TAG_RELATIVE");
  const [x, setX] = useState(0);
  const [y, setY] = useState(0);
  const [tagId, setTagId] = useState<number | "">(gotoTag ?? "");
  const [vel, setVel] = useState(2);
  const [plTag, setPlTag] = useState<number | "">("");

  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); } catch (e) { setErr(errMsg(e)); }
  };
  const simple = (action: string, param?: number) => run(() => post<Command>("/api/commands/simple", { action, param }));
  const effTag = tagId === "" ? gotoTag : tagId;

  const recent = Object.values(commands).sort((a, b) => b.id - a.id).slice(0, 6);
  return (
    <div className="panel">
      <h3>Điều khiển tay</h3>
      <div className="btn-row">
        <button onClick={() => simple("ARM")}>Arm</button>
        <button onClick={() => simple("DISARM")}>Disarm</button>
        <button onClick={() => simple("HOLD")}>Giữ vị trí</button>
        <button onClick={() => simple("LAND")}>Hạ cánh</button>
        <button onClick={() => simple("RTH")}>Về Home</button>
      </div>
      <div className="form-row">
        <label>Cất cánh tới <input type="number" value={alt} min={1} max={50} step={0.5} onChange={(e) => setAlt(+e.target.value)} /> m</label>
        <button onClick={() => simple("TAKEOFF", alt)}>Cất cánh</button>
      </div>
      <div className="form-row">
        <label>Hạ chính xác trên tag
          <select value={plTag} onChange={(e) => setPlTag(e.target.value === "" ? "" : +e.target.value)}>
            <option value="">—</option>
            {tags.map((t) => <option key={t.tag_id} value={t.tag_id}>{t.label} #{t.tag_id}</option>)}
          </select>
        </label>
        <button disabled={plTag === ""} onClick={() => simple("PRECISION_LAND", plTag as number)}>Hạ</button>
      </div>
      <fieldset>
        <legend>Bay tới</legend>
        <div className="form-row">
          <select value={frame} onChange={(e) => setFrame(e.target.value as never)}>
            <option value="TAG_RELATIVE">Tương đối tag</option>
            <option value="MAP_NED">Tọa độ bản đồ (N, E)</option>
            <option value="BODY_RELATIVE">Tương đối thân (trước, phải)</option>
          </select>
          {frame === "TAG_RELATIVE" && (
            <select value={effTag ?? ""} onChange={(e) => setTagId(e.target.value === "" ? "" : +e.target.value)}>
              <option value="">chọn tag</option>
              {tags.map((t) => <option key={t.tag_id} value={t.tag_id}>{t.label} #{t.tag_id}</option>)}
            </select>
          )}
        </div>
        <div className="form-row">
          <label>{frame === "BODY_RELATIVE" ? "Trước" : frame === "MAP_NED" ? "N" : "ΔN"} <input type="number" step={0.5} value={x} onChange={(e) => setX(+e.target.value)} /></label>
          <label>{frame === "BODY_RELATIVE" ? "Phải" : frame === "MAP_NED" ? "E" : "ΔE"} <input type="number" step={0.5} value={y} onChange={(e) => setY(+e.target.value)} /></label>
          <label>{frame === "BODY_RELATIVE" ? "Lên" : "Cao"} <input type="number" step={0.5} value={alt} onChange={(e) => setAlt(+e.target.value)} /> m</label>
          <label>v <input type="number" step={0.5} min={0.5} max={10} value={vel} onChange={(e) => setVel(+e.target.value)} /></label>
        </div>
        <button disabled={frame === "TAG_RELATIVE" && effTag === null} onClick={() => run(async () => {
          // "cao h" = h mét trên mặt tag/bản đồ → z (Down) = −h; hệ thân: "lên" → z = −h
          const res = await post<Command & { warnings: string[] }>("/api/commands/goto", {
            ref_frame: frame, ref_tag_id: frame === "TAG_RELATIVE" ? effTag : null, x, y, z: -alt, max_vel: vel, climb_first: true,
          });
          if (res.warnings?.length) setErr("Cảnh báo: " + res.warnings.join("; "));
        })}>Gửi lệnh bay tới</button>
      </fieldset>
      {err && <div className="error">{err}</div>}
      <ul className="cmd-list">
        {recent.map((c) => (
          <li key={c.id} className={STAGE_CLS[c.stage] ?? ""}>
            #{c.id} {c.kind}/{c.action} — <b>{c.stage}</b>{c.detail ? ` (${c.detail})` : ""}{c.ack ? ` · ${c.ack.rtt_ms} ms ×${c.ack.attempts}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
