// Trạng thái sống từ WebSocket. CẤM đặt telemetry 10 Hz vào React state (9.5):
// TELEM_FAST đi thẳng vào liveBuffer; store chỉ nhận bản sao hạ tần 5 Hz cho HUD.
import { create } from "zustand";
import type { Alert, Command, ConfigRow, EventRow, LinkStatus, MapStatus, Mission, Shadow, TagDetect, TelemFast, TelemSlow } from "../lib/types";

export interface LinkPoint { t: number; rssi: number | null; pdr: number | null; rtt: number | null }

interface LiveState {
  connected: boolean;
  link: LinkStatus | null;
  shadow: Shadow | null;
  map: MapStatus | null;
  alerts: Alert[];
  commands: Record<number, Command>;
  fast: TelemFast | null;        // hạ tần 5 Hz
  slow: TelemSlow | null;
  tagDetect: TagDetect | null;
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
  connected: false, link: null, shadow: null, map: null, alerts: [], commands: {}, fast: null, slow: null,
  tagDetect: null, drift: [], activeMission: null, events: [], missionsVersion: 0, siteVersion: 0,
  uploadProgress: null, renderDelayMs: 150, linkHistory: [], thresholds: null,
}));
