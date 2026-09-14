import { fmt } from "../../lib/units";
import { useLive } from "../../store/live";

const GRIP = ["mở", "đóng", "đang chạy", "lỗi"];

export function TelemetryPanel() {
  const fast = useLive((s) => s.fast);
  const slow = useLive((s) => s.slow);
  const shadow = useLive((s) => s.shadow);
  const det = useLive((s) => s.tagDetect);
  const rows: [string, string][] = [
    ["Vị trí N/E/D", fast ? fast.pos.map((v) => v.toFixed(2)).join(" / ") + " m" : "—"],
    ["Vận tốc N/E/D", fast ? fast.vel.map((v) => v.toFixed(2)).join(" / ") + " m/s" : "—"],
    ["Roll/Pitch/Yaw", fast ? fast.att.map((v) => v.toFixed(1)).join(" / ") + "°" : "—"],
    ["Nhiệm vụ", shadow ? `#${shadow.mission_id} · WP ${shadow.wp_index}/${shadow.wp_total} · tag ${shadow.expected_tag ?? "—"}` : "—"],
    ["Thử lại / failsafe", shadow ? `${shadow.retry_count} · ${shadow.failsafe_name ?? "—"}` : "—"],
    ["Pin", slow ? `${slow.batt_v.toFixed(2)} V · ${slow.batt_a.toFixed(1)} A · ${slow.batt_mah_used} mAh` : "—"],
    ["EKF / GPS", slow ? `${slow.ekf_health} % · ${slow.gps_sats} vệ tinh` : "—"],
    ["CPU / nhiệt", slow ? `${slow.cpu_pct} % · ${slow.temp_c} °C` : "—"],
    ["Kẹp hàng", slow ? GRIP[slow.gripper_state] ?? "?" : "—"],
    ["RSSI drone / mất gói", slow ? `${slow.rssi_dbm} dBm · ${slow.link_loss_pct} %` : "—"],
    ["Tag đang bám", det && Date.now() - det.t_gcs < 1000 ? `#${det.tag_id} · q ${det.quality} · rel ${det.rel.map((v) => v.toFixed(2)).join("/")}` : "—"],
    ["Uptime", slow ? fmt(slow.uptime_s, 0, "s") : "—"],
  ];
  return (
    <div className="panel">
      <h3>Telemetry</h3>
      <table className="kv"><tbody>{rows.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
    </div>
  );
}
