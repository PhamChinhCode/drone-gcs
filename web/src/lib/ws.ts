// LiveSource (mục 9.6): nhận khung gộp {"type":"batch"} từ /ws/telemetry, phân phối
// telemetry vào bộ đệm dùng chung và các bản tin trạng thái vào store.
import { useAuth } from "../store/auth";
import { useLive } from "../store/live";
import { makeSample, TelemetryBuffer } from "../features/scene3d/useTelemetryBuffer";
import type { Alert, Command, TelemFast } from "./types";

export const liveBuffer = new TelemetryBuffer();

/** Ước lượng lệch đồng hồ trình duyệt − backend: lấy min(arrival − t_gcs) trong cửa sổ trượt. */
class ClockSkew {
  private win: { at: number; d: number }[] = [];
  skew = 0;
  observe(tGcs: number) {
    const now = Date.now();
    this.win.push({ at: now, d: now - tGcs });
    while (this.win.length && now - this.win[0].at > 30000) this.win.shift();
    this.skew = Math.min(...this.win.map((w) => w.d));
  }
}
export const skew = new ClockSkew();
export const liveClock = () => Date.now() - skew.skew;

let ws: WebSocket | null = null;
let lastHud = 0;
let retryTimer: number | undefined;

type Item = { type: string; [k: string]: unknown };

function handle(item: Item) {
  const set = useLive.setState;
  switch (item.type) {
    case "telem_fast": {
      const f = item as unknown as TelemFast;
      skew.observe(f.t_gcs);
      liveBuffer.push(makeSample(f.t_gcs, f.pos, f.att, f.fsm_state, f.flags, f.wp_index, f.battery_pct));
      const now = performance.now();
      if (now - lastHud > 200) { lastHud = now; set({ fast: f }); }
      break;
    }
    case "telem_slow": set({ slow: item as never }); break;
    case "tag_detect": set({ tagDetect: item as never }); break;
    case "mission_state": set({ shadow: item as never }); break;
    case "link": {
      const l = item as never as import("./types").LinkStatus;
      const h = useLive.getState().linkHistory;
      const point = { t: Date.now(), rssi: l.dongle.last_rssi_dbm ?? null, pdr: l.pdr_pct, rtt: l.rtt_ms_avg };
      set({ link: l, linkHistory: [...h.slice(-599), point] });
      break;
    }
    case "map": set({ map: item as never }); break;
    case "alert": {
      const { action, alert } = item as unknown as { action: string; alert: Alert };
      const cur = useLive.getState().alerts.filter((a) => a.code !== alert.code);
      set({ alerts: action === "clear" ? cur : [alert, ...cur].sort((a, b) => b.severity - a.severity) });
      break;
    }
    case "command": {
      const c = item.data as Command ?? (item as unknown as Command);
      set({ commands: { ...useLive.getState().commands, [c.id]: c } });
      break;
    }
    case "config_drift": set({ drift: (item as unknown as { drift: never[] }).drift }); break;
    case "event": set({ events: [item as never, ...useLive.getState().events].slice(0, 200) }); break;
    case "mission_update": set({ missionsVersion: useLive.getState().missionsVersion + 1 }); break;
    case "upload_progress": set({ uploadProgress: item as never }); break;
    case "site_changed": {
      const m = (item as unknown as { map: import("./types").MapStatus }).map;
      set({ siteVersion: useLive.getState().siteVersion + 1, map: m });
      break;
    }
  }
}

function hello(msg: Item) {
  const s = msg as unknown as {
    shadow: never; link: never; map: never; alerts: Alert[]; active_mission: never; commands: Command[];
    render_delay_ms: number; thresholds: never;
  };
  const commands: Record<number, Command> = {};
  s.commands.forEach((c) => { commands[c.id] = c; });
  useLive.setState({
    shadow: s.shadow, link: s.link, map: s.map, alerts: s.alerts, activeMission: s.active_mission, commands,
    renderDelayMs: s.render_delay_ms, thresholds: s.thresholds,
  });
}

export function connectLive() {
  const token = useAuth.getState().token;
  if (!token || ws) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const sock = new WebSocket(`${proto}://${location.host}/ws/telemetry?token=${encodeURIComponent(token)}`);
  ws = sock;
  sock.onopen = () => useLive.setState({ connected: true });
  sock.onmessage = (ev) => {
    const msg = JSON.parse(ev.data) as Item;
    if (msg.type === "batch") (msg.items as Item[]).forEach(handle);
    else if (msg.type === "hello") hello(msg);
  };
  sock.onclose = (ev) => {
    ws = null;
    useLive.setState({ connected: false });
    if (ev.code === 4401) { useAuth.getState().logout(); return; }
    if (useAuth.getState().token) retryTimer = window.setTimeout(connectLive, 1500);
  };
}

export function disconnectLive() {
  window.clearTimeout(retryTimer);
  ws?.close();
  ws = null;
}
