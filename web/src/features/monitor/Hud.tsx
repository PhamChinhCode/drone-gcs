// HUD — lớp DOM phủ lên cảnh 3D (không vẽ chữ trong WebGL). Cập nhật 5 Hz.
//
// Không có ô pin: BATTERY_VALID hiện luôn bằng 0 nên một đồng hồ pin ở đây chỉ nói dối (5.2).
// Cảnh báo "không có số đo pin" đi ở dải cảnh báo, đúng chỗ của nó.
import { useEffect, useState } from "react";
import { fmt } from "../../lib/units";
import { FSM_LABELS } from "../../lib/types";
import { liveClock } from "../../lib/ws";
import { useLive } from "../../store/live";

export function Hud() {
  const fast = useLive((s) => s.fast);
  const telem = useLive((s) => s.telem);
  const shadow = useLive((s) => s.shadow);
  const link = useLive((s) => s.link);
  // tự vẽ lại 2 Hz: khi mất gói thì store không đổi, nhưng tuổi dữ liệu vẫn phải tăng
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick((x) => x + 1), 500); return () => window.clearInterval(t); }, []);

  const speed = fast ? Math.hypot(fast.vel[0], fast.vel[1]) : null;
  const age = fast ? Math.max(0, (liveClock() - fast.t_gcs) / 1000) : null;
  // Pi NGỪNG PHÁT vị trí khi mất POS_VALID: im lặng ở đây nghĩa là không biết, không phải đứng yên
  const noPos = age === null || age > 3;
  const fsm = shadow?.fsm_state ?? null;
  // giá trị 8 là hạ cánh CHUNG — chỉ hiện "khẩn cấp" khi failsafe_type khác 0 (8.5)
  const fsmName = fsm === null ? "—" : shadow?.emergency ? `${FSM_LABELS[fsm]} (SỰ CỐ)` : FSM_LABELS[fsm];

  return (
    <div className={`hud${noPos ? " stale" : ""}`}>
      <div className="hud-item"><label>Độ cao</label><b>{fmt(fast ? fast.alt_m : null, 1, "m")}</b>
        <small>so với gốc bản đồ tag</small></div>
      <div className="hud-item"><label>Tốc độ</label><b>{fmt(speed, 1, "m/s")}</b>
        <small>vz {fmt(fast ? -fast.vel[2] : null, 1)}</small></div>
      <div className="hud-item wide">
        <label>Trạng thái</label><b>{fsmName}</b>
        <small>{`WP ${shadow?.wp_index ?? "—"}/${shadow?.wp_total ?? "—"}`}
          {telem?.expected_tag !== null && telem?.expected_tag !== undefined && ` · tìm tag ${telem.expected_tag}`}</small>
      </div>
      <div className={`hud-item ${!link ? "" : link.up ? "good" : "bad"}`}>
        <label>Liên kết</label><b>{link?.up ? "OK" : "MẤT"}</b>
        <small>RTT {fmt(link?.rtt_ms ?? null, 0, "ms")} · mất {fmt(link?.gcs.rx_drop ?? null, 0, "gói")}</small>
      </div>
      <div className="hud-item wide">
        <label>Vị trí</label>
        <b className={noPos ? "warn" : "good"}>{noPos ? "KHÔNG CÓ" : "đang theo dõi"}</b>
        <small>{noPos ? "drone chưa neo theo tag hoặc mất EKF" : `cập nhật ${age!.toFixed(1)} s trước`}</small>
      </div>
      <div className="hud-flags">
        <span className={telem?.armed ? "on" : ""}>{telem?.armed === null ? "ARMED?" : "ARMED"}</span>
        <span className={telem?.marker_id_tracking !== null && telem?.marker_id_tracking !== undefined ? "on" : ""}>BÁM TAG</span>
        <span className={telem?.carrying ? "on" : ""}>MANG HÀNG</span>
        <span className={telem?.pi_has_authority ? "on" : "bad"}>QUYỀN PI</span>
        <span className={shadow?.emergency ? "on bad" : ""}>SỰ CỐ</span>
      </div>
    </div>
  );
}
