// Kiểu dữ liệu API/WS — đơn vị SI (m, m/s, độ) như backend (mục 3.1)

export type Role = "operator" | "admin";
export interface User { id: number; username: string; role: Role }

/** LOCAL_POSITION_NED + ATTITUDE, 5 Hz — CHỈ có khi POS_VALID. Vắng nghĩa là KHÔNG BIẾT vị trí (5.1). */
export interface TelemFast {
  t_ms: number; t_gcs: number;
  pos: [number, number, number]; vel: [number, number, number]; att: [number, number, number];
  alt_m: number;
  /** odom vừa neo lại: vị trí NHẢY, không được nội suy qua (5.2b ý 3). */
  jump: boolean;
}

/** Cờ hiệu lực của DRONE_TELEMETRY (5.2). Bit = 0 nghĩa là KHÔNG BIẾT, không phải 0. */
export interface ValidFlags {
  pos: boolean; global_pos: boolean; battery: boolean; fc_link: boolean; marker: boolean;
  gripper: boolean; ekf_healthy: boolean; rssi: boolean; home: boolean;
}

/** DRONE_TELEMETRY 2 Hz. Trường nào cờ hạ thì backend trả null — ĐỪNG vẽ số 0 thay vào. */
export interface Telem {
  t_ms: number; t_gcs: number; mission_id: number; contract_ver: number;
  fsm_state: number; wp_index: number; wp_total: number; retry_count: number; failsafe_type: number;
  expected_tag: number | null; marker_id_tracking: number | null;
  alt_m: number | null; vel: [number, number, number] | null;
  battery_pct: number | null; battery_v: number | null; lat: number | null; lon: number | null;
  rssi_dbm: number | null; gripper_state: number | null; carrying: boolean | null;
  armed: boolean | null; pi_has_authority: boolean | null;
  /** Nhà của RTH, [n, e] mét. null = chưa chốt được — KHÔNG vẽ thay bằng pad_home (11.P18). */
  home_ne: [number, number] | null;
  /** Vì sao chuyến GẦN NHẤT kết thúc (8.5b). null = chưa biết — đừng hiển thị gì.
   *  mission_state = 9 KHÔNG nói lên điều này: nó là đích chung của cả huỷ, RTH, LAND và hết kế hoạch. */
  flight_result: number | null;
  /** GPS THÔ từ FC (0.7). null = không có GPS hoặc Pi cũ. fix_type theo GPS_FIX_TYPE: 1 chưa fix, 3 = 3D.
   *  lat/lon ở trên là vị trí HỢP NHẤT của EKF, KHÔNG phải GPS thô. */
  gps: { fix_type: number; sats: number | null; hacc_m: number | null } | null;
  tagmap_crc: number; valid: ValidFlags;
}

export interface Shadow {
  drone_id: number; fsm_state: number | null; fsm_name: string | null; mission_id: number; wp_index: number;
  wp_total: number; expected_tag: number | null; retry_count: number; failsafe_type: number; failsafe_name: string | null;
  /** Trạng thái 8 là hạ cánh CHUNG; chỉ cờ này mới nói là khẩn cấp (8.5). */
  emergency: boolean;
  contract_ver: number | null; tagmap_crc: number | null;
  age_s: number | null; stale: boolean; state_age_s: number | null;
  fast: TelemFast | null; telem: Telem | null;
}

/** Tám bộ đếm của DRONE_LINK_STATS (7.4). 0xFFFF / 0xFFFFFFFF = bên phát không đo được. */
export interface LinkCounters {
  rx_ok: number; rx_drop: number; rx_bad_crc: number; rx_bad_sig: number;
  tx_sent: number; tx_dropped: number; queue_depth?: number; rtt_ms?: number;
}

export interface LinkStatus {
  up: boolean; peer: string | null; rx_age_s: number | null; rtt_ms: number | null; signing: boolean;
  gcs: LinkCounters; drone: Partial<LinkCounters>;
}

export interface ContractStatus { gcs: number; drone: number | null; drone_major: number | null; major_mismatch: boolean }

export interface MapStatus { gcs_crc: number; drone_crc: number | null; in_sync: boolean }

export type TagKind = "home" | "pickup" | "dropoff" | "waypoint";
export interface Tag {
  id?: number; site_id?: number; tag_id: number; label: string; pos_n_m: number; pos_e_m: number; pos_d_m: number;
  yaw_deg: number; tag_size_m: number; kind: TagKind; landing_tol_m: number; enabled: boolean; notes?: string | null;
}

export interface Area {
  id?: number; kind: "operating" | "no_fly"; name: string; min_alt_m: number; max_alt_m: number; enabled: boolean;
  notes?: string | null; vertices: [number, number][];
}

export interface BgAnchor { px: number; py: number; n: number; e: number }

export interface Site {
  id: number; name: string; origin_lat: number | null; origin_lon: number | null; origin_alt_m: number | null;
  yaw_offset_deg: number; design_radius_m: number | null; link_radius_meas_m: number | null;
  cruise_alt_default_m: number; gcs_pos_n_m: number; gcs_pos_e_m: number; bg_image_path: string | null;
  bg_anchors: BgAnchor[] | null;
}

export interface Issue { code: string; level: "error" | "warning"; message: string; refs: { type: "tag" | "area"; tag_id?: number; index?: number }[]; at: [number, number] | null }

export interface Waypoint {
  seq: number; tag_id: number | null; pos_n_m: number; pos_e_m: number; pos_d_m: number; yaw_deg: number | null;
  action: string; accept_radius_m: number | null; max_vel_mps: number | null; loiter_s: number | null;
  require_tag_lock: boolean; precision_land: boolean; reached_at?: string | null;
}

export type MissionStateName = "queued" | "uploading" | "ready" | "running" | "done" | "failed" | "aborted";
export interface Mission {
  id: number; drone_id: number; plan_name: string | null; state: MissionStateName; pickup_tag: number | null;
  dropoff_tag: number | null; priority: number; map_crc: number; cruise_alt_m: number; max_vel_mps: number;
  created_at: string; started_at: string | null; finished_at: string | null; result: string | null;
  fail_reason: string | null; waypoints?: Waypoint[]; warnings?: string[];
}

export interface Alert { code: string; severity: number; category: string; message: string; raised_at: number; active: boolean; acked: boolean }

export type CmdStage = "sending" | "acked" | "effective" | "rejected" | "failed" | "not_effective" | "cancelled";
export interface Command { id: number; kind: string; action: string; stage: CmdStage; created: number; ack: { result: string; rtt_ms: number; attempts: number } | null; detail: string | null; verify_window_s: number | null }

export interface EventRow { id: number; mission_id: number | null; t_utc: number; t_drone_ms: number | null; severity: number; category: string; code: number | null; message: string }

export interface ConfigRow { key: string; value: string; drone_value: string | null; in_sync: number | null; last_checked: string | null; updated_at: string | null }

export interface TelemetryRow {
  t_utc: number; t_drone_ms: number; pos_n: number; pos_e: number; pos_d: number; vel_n: number; vel_e: number; vel_d: number;
  roll: number; pitch: number; yaw: number; fsm_state: number; flags: number; wp_index: number; battery_pct: number;
}

export const FSM_NAMES = ["IDLE", "TAKEOFF", "ENROUTE", "MARKER_SEARCH", "PRECISION_LAND", "ACTUATE_GRIPPER",
  "RETRY_LOITER", "RTH", "EMERGENCY_LAND", "MISSION_COMPLETE", "FAILSAFE"];

/** Nhãn hiển thị: giá trị 8 KHÔNG được hiện "khẩn cấp" khi failsafe_type = 0 (8.5). */
export const FSM_LABELS: Record<number, string> = {
  0: "Chờ lệnh", 1: "Cất cánh", 2: "Đang bay", 3: "Tìm marker", 4: "Hạ chính xác",
  5: "Gắp / thả", 6: "Chờ thử lại", 7: "Về nhà (RTH)", 8: "Đang hạ cánh",
  9: "Xong nhiệm vụ", 10: "Sự cố",
};

export const FSM = { IDLE: 0, TAKEOFF: 1, ENROUTE: 2, MARKER_SEARCH: 3, PRECISION_LAND: 4, ACTUATE_GRIPPER: 5, RETRY_LOITER: 6, RTH: 7, EMERGENCY_LAND: 8, MISSION_COMPLETE: 9, FAILSAFE: 10 } as const;

export const GRIPPER_NAMES = ["MỞ", "ĐANG GIỮ", "ĐANG CHẠY", "LỖI"];

/** DRONE_FLIGHT_RESULT (8.5b). Chỉ số 1 là "làm xong việc"; 6 vẫn đi qua MISSION_COMPLETE nhưng
 *  drone chưa bao giờ thấy tag và đã hạ xuống chỗ lạ. */
export const FLIGHT_RESULT: Record<number, [string, string]> = {
  1: ["good", "Hoàn thành kế hoạch"],
  2: ["", "Người vận hành huỷ"],
  3: ["", "Đã về nhà (RTH)"],
  4: ["", "Hạ cánh theo lệnh"],
  5: ["bad", "Kết thúc do sự cố"],
  6: ["bad", "Hết lượt thử tìm tag — hạ tại chỗ, CHƯA làm được việc"],
};

/** UINT32_MAX / 0xFFFF nghĩa là bên phát KHÔNG ĐO ĐƯỢC, không phải bằng đó (7.4). */
export const UNKNOWN32 = 0xffffffff;
export const UNKNOWN16 = 0xffff;
export const known = (v: number | null | undefined, max = UNKNOWN32) =>
  v === null || v === undefined || v === max ? null : v;
