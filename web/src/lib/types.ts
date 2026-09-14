// Kiểu dữ liệu API/WS — đơn vị SI (m, m/s, độ) như backend (mục 3.1)

export type Role = "operator" | "admin";
export interface User { id: number; username: string; role: Role }

export interface TelemFast {
  t_ms: number; t_gcs: number;
  pos: [number, number, number]; vel: [number, number, number]; att: [number, number, number];
  fsm_state: number; flags: number; armed: boolean; offboard: boolean; ekf_ok: boolean; tag_lock: boolean;
  carrying: boolean; failsafe_active: boolean; gps_ok: boolean; wp_index: number; battery_pct: number;
}

export interface TelemSlow {
  t_ms: number; t_gcs: number; batt_v: number; batt_a: number; batt_mah_used: number; mission_id: number;
  uptime_s: number; batt_pct: number; rssi_dbm: number; link_loss_pct: number; ekf_health: number;
  gps_sats: number; cpu_pct: number; temp_c: number; gripper_state: number; failsafe_type: number; err_flags: number;
}

export interface TagDetect { t_ms: number; t_gcs: number; tag_id: number; rel: [number, number, number]; yaw_rel_deg: number; quality: number; n_visible: number }

export interface Shadow {
  drone_id: number; fsm_state: number | null; fsm_name: string | null; mission_id: number; wp_index: number;
  wp_total: number; expected_tag: number | null; retry_count: number; failsafe_type: number; failsafe_name: string | null;
  age_s: number | null; stale: boolean; state_age_s: number | null;
}

export type LinkState = "ok" | "drone_lost" | "drone_lost_long" | "dongle_lost" | "drone_silent";

export interface LinkStatus {
  state: LinkState; url: string; transport_up: boolean; drone_hb_age_s: number | null; dongle_age_s: number | null;
  dongle: { tx_count?: number; tx_ok?: number; tx_fail?: number; rx_count?: number; last_rssi_dbm?: number; queue_depth?: number; telem_dropped?: number };
  air: { rssi_dbm?: number; loss_pct?: number; rtt_ms?: number };
  pdr_pct: number | null; rtt_ms_avg: number | null; rtt_ms_max: number | null;
  stats: Record<string, number>; drone_map_crc: number | null;
}

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

export const FSM = { IDLE: 0, TAKEOFF: 1, ENROUTE: 2, MARKER_SEARCH: 3, PRECISION_LAND: 4, ACTUATE_GRIPPER: 5, RETRY_LOITER: 6, RTH: 7, EMERGENCY_LAND: 8, MISSION_COMPLETE: 9, FAILSAFE: 10 } as const;

export const TF = { ARMED: 0x01, OFFBOARD: 0x02, EKF_OK: 0x04, TAG_LOCK: 0x08, CARRYING: 0x10, FAILSAFE: 0x20, GPS_OK: 0x40 } as const;
