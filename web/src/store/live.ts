// Trạng thái sống từ WebSocket. CẤM đặt telemetry vào React state mỗi gói:
// vị trí 5 Hz đi thẳng vào liveBuffer; store chỉ nhận bản sao hạ tần cho HUD.
import { create } from "zustand";
import type { Alert, Command, ConfigRow, ContractStatus, EventRow, LinkStatus, MapStatus, Mission, Shadow, Telem, TelemFast } from "../lib/types";

export interface LinkPoint { t: number; rssi: number | null; drop: number | null; rtt: number | null }

interface LiveState {
  connected: boolean;
  link: LinkStatus | null;
  shadow: Shadow | null;
  map: MapStatus | null;
  alerts: Alert[];
  commands: Record<number, Command>;
  fast: TelemFast | null;        // null = chưa/không còn biết vị trí (POS_VALID = 0)
  telem: Telem | null;
  contract: ContractStatus | null;
  statusTexts: { at: number; severity: number; text: string }[];
  /** Người vận hành chỉ định drone đang đậu ở đâu khi POS_VALID = 0.
   *
   *  CHỈ ĐỂ VẼ. Không gửi xuống drone, không ghi vào CSDL, và **không được đưa vào bất kỳ phép kiểm
   *  nào** — vùng cấm, khoảng cách tới bãi đáp, kiểm chứng lệnh đều phải bỏ qua nó. Kiểm một con số
   *  người dùng vừa gõ vào rồi báo kết quả như số đo là cách nhanh nhất để mất lòng tin vào cảnh báo. */
  assumedPos: { n: number; e: number; tagId: number | null } | null;
  drift: ConfigRow[];
  activeMission: Mission | null;
  events: EventRow[];
  missionsVersion: number;
  siteVersion: number;
  uploadProgress: { id: number; done: number; total: number } | null;
  renderDelayMs: number;
  linkHistory: LinkPoint[];
  thresholds: { low_battery_pct: number; critical_battery_pct: number; acceptance_radius_m: number; takeoff_alt_m: number } | null;
}

export const useLive = create<LiveState>(() => ({
  connected: false, link: null, shadow: null, map: null, alerts: [], commands: {}, fast: null, telem: null,
  contract: null, statusTexts: [], assumedPos: null, drift: [], activeMission: null, events: [], missionsVersion: 0, siteVersion: 0,
  uploadProgress: null, renderDelayMs: 150, linkHistory: [], thresholds: null,
}));
