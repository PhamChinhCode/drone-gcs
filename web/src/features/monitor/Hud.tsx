// HUD — lớp DOM phủ lên cảnh 3D (không vẽ chữ trong WebGL, mục 9.1). Cập nhật 5 Hz.
import { useEffect, useState } from "react";
import { fmt } from "../../lib/units";
import { FSM_NAMES } from "../../lib/types";
import { liveClock } from "../../lib/ws";
import { useLive } from "../../store/live";

export function Hud() {
  const fast = useLive((s) => s.fast);
  const slow = useLive((s) => s.slow);
  const shadow = useLive((s) => s.shadow);
  const link = useLive((s) => s.link);
  const th = useLive((s) => s.thresholds);
  // tự vẽ lại 2 Hz: khi mất gói thì store không đổi, nhưng tuổi dữ liệu vẫn phải tăng (6.2 quy tắc 1)
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick((x) => x + 1), 500); return () => window.clearInterval(t); }, []);

  const batt = fast?.battery_pct ?? null;
  const battCls = batt === null ? "" : batt < (th?.critical_battery_pct ?? 15) ? "bad" : batt < (th?.low_battery_pct ?? 25) ? "warn" : "good";
  const speed = fast ? Math.hypot(fast.vel[0], fast.vel[1]) : null;
  const age = fast ? Math.max(0, (liveClock() - fast.t_gcs) / 1000) : null;
  const stale = age === null || age > 3;
  const fsmName = shadow?.fsm_name ?? (fast ? FSM_NAMES[fast.fsm_state] : null);
  const linkCls = !link ? "" : link.state === "ok" ? "good" : link.state === "drone_lost" ? "warn" : "bad";

  return (
    <div className={`hud${stale ? " stale" : ""}`}>
      <div className={`hud-item ${battCls}`}><label>Pin</label><b>{fmt(batt, 0, "%")}</b><small>{fmt(slow?.batt_v, 1, "V")}</small></div>
      <div className="hud-item"><label>Độ cao</label><b>{fmt(fast ? -fast.pos[2] : null, 1, "m")}</b></div>
      <div className="hud-item"><label>Tốc độ</label><b>{fmt(speed, 1, "m/s")}</b><small>vz {fmt(fast ? -fast.vel[2] : null, 1)}</small></div>
      <div className="hud-item wide">
        <label>FSM</label><b>{fsmName ?? "—"}</b>
        <small>{stale ? <span className="warn">số liệu cũ {age === null ? "?" : age.toFixed(0)} s</span> : `WP ${shadow?.wp_index ?? fast?.wp_index ?? "—"}/${shadow?.wp_total ?? "—"}`}</small>
      </div>
      <div className={`hud-item ${linkCls}`}>
        <label>Liên kết</label><b>{link?.state ?? "—"}</b>
        <small>{fmt(link?.dongle.last_rssi_dbm ?? null, 0, "dBm")} · PDR {fmt(link?.pdr_pct, 0, "%")} · RTT {fmt(link?.rtt_ms_avg, 0, "ms")}</small>
      </div>
      <div className="hud-flags">
        <span className={fast?.armed ? "on" : ""}>ARMED</span>
        <span className={fast?.tag_lock ? "on" : ""}>TAG LOCK</span>
        <span className={fast?.carrying ? "on" : ""}>MANG HÀNG</span>
        <span className={fast?.failsafe_active ? "on bad" : ""}>FAILSAFE</span>
      </div>
    </div>
  );
}
