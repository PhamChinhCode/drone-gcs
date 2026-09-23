// Bảng telemetry. Quy tắc duy nhất cần nhớ: cờ hiệu lực = 0 thì hiện "—", KHÔNG hiện số 0 (5.2).
// Pin hiện luôn rơi vào nhánh đó vì FC chưa gửi BATTERY_STATUS. Từ giao ước 0.7: "Toạ độ" là vị trí
// HỢP NHẤT (tag + GPS + flow) quy ra WGS84, "GPS" là chất lượng GPS thô — hai thứ khác nhau.
import { FLIGHT_RESULT, GRIPPER_NAMES } from "../../lib/types";
import { useLive } from "../../store/live";

const FIX_NAME: Record<number, string> = { 1: "chưa fix", 2: "2D", 3: "3D", 4: "3D + SBAS", 5: "RTK float", 6: "RTK fixed" };

export function TelemetryPanel() {
  const fast = useLive((s) => s.fast);
  const t = useLive((s) => s.telem);
  const shadow = useLive((s) => s.shadow);
  const v = t?.valid;

  const rows: [string, string][] = [
    ["Vị trí N/E/D", fast ? fast.pos.map((x) => x.toFixed(2)).join(" / ") + " m" : "chưa có vị trí"],
    ["Vận tốc N/E/D", fast ? fast.vel.map((x) => x.toFixed(2)).join(" / ") + " m/s" : "—"],
    ["Roll/Pitch/Yaw", fast ? fast.att.map((x) => x.toFixed(1)).join(" / ") + "°" : "—"],
    ["Nhiệm vụ", shadow ? `#${shadow.mission_id} · WP ${shadow.wp_index}/${shadow.wp_total}` : "—"],
    ["Tag đang TÌM", t?.expected_tag === null || t?.expected_tag === undefined ? "—" : `#${t.expected_tag}`],
    ["Tag đang BÁM", t?.marker_id_tracking === null || t?.marker_id_tracking === undefined
      ? "không bám tag nào" : `#${t.marker_id_tracking}`],
    ["Thử lại / sự cố", shadow ? `${shadow.retry_count} · ${shadow.failsafe_name ?? "—"}` : "—"],
    ["Pin", t?.battery_pct === null || t?.battery_pct === undefined
      ? "không có số đo (FC chưa gửi)" : `${t.battery_pct.toFixed(0)} % · ${t.battery_v?.toFixed(2)} V`],
    ["Toạ độ (hợp nhất)", v?.global_pos ? `${t?.lat?.toFixed(7)}, ${t?.lon?.toFixed(7)}`
      : "— (chưa neo, hoặc khu vực chưa khai gốc WGS84)"],
    ["GPS", !t?.gps ? "không có dữ liệu GPS"
      : `${FIX_NAME[t.gps.fix_type] ?? `mã ${t.gps.fix_type}`}`
        + (t.gps.sats === null ? "" : ` · ${t.gps.sats} vệ tinh`)
        + (t.gps.hacc_m === null ? "" : ` · ±${t.gps.hacc_m.toFixed(1)} m`)],
    ["Kẹp hàng", t?.gripper_state === null || t?.gripper_state === undefined
      ? "—" : GRIPPER_NAMES[t.gripper_state] ?? "?"],
    ["Quyền điều khiển của Pi", t?.pi_has_authority === null || t?.pi_has_authority === undefined
      ? "—" : t.pi_has_authority ? "đã trao (ch5/ch8)" : "CHƯA trao — MISSION_START sẽ bị từ chối"],
    ["EKF", v ? (v.ekf_healthy ? "khoẻ" : "KHÔNG khoẻ") : "—"],
    ["Chuyến gần nhất", t?.flight_result ? (FLIGHT_RESULT[t.flight_result]?.[1] ?? `mã ${t.flight_result}`)
      : "chưa có chuyến nào kết thúc"],
    ["Nhà của RTH", t?.home_ne ? `N ${t.home_ne[0].toFixed(2)} / E ${t.home_ne[1].toFixed(2)} m`
      : "chưa chốt — RTH sẽ hạ tại chỗ"],
  ];
  return (
    <div className="panel">
      <h3>Telemetry</h3>
      <table className="kv"><tbody>{rows.map(([k, x]) => <tr key={k}><th>{k}</th><td>{x}</td></tr>)}</tbody></table>
    </div>
  );
}
