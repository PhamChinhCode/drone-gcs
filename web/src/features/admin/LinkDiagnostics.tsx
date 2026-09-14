// Chẩn đoán liên kết (F7, 8.2): đồ thị RSSI/PDR/RTT, thống kê dongle, cấu hình ESP-NOW.
import { useState } from "react";
import { errMsg, post } from "../../lib/api";
import { useIsAdmin } from "../../store/auth";
import { useLive } from "../../store/live";
import { Sparkline } from "./Sparkline";

export function LinkDiagnostics() {
  const link = useLive((s) => s.link);
  const hist = useLive((s) => s.linkHistory);
  const renderDelay = useLive((s) => s.renderDelayMs);
  const admin = useIsAdmin();
  const [cfg, setCfg] = useState({ channel: 1, lr_mode: false, peer_mac: "00:00:00:00:00:00" });
  const [msg, setMsg] = useState<string | null>(null);

  const kv: [string, React.ReactNode][] = link ? [
    ["Trạng thái", <b className={link.state === "ok" ? "good" : "bad"}>{link.state}</b>],
    ["Vận chuyển", `${link.url} · ${link.transport_up ? "mở" : "đóng"}`],
    ["Heartbeat drone", link.drone_hb_age_s === null ? "chưa có" : `${link.drone_hb_age_s} s trước`],
    ["Dongle trả lời", link.dongle_age_s === null ? "chưa có" : `${link.dongle_age_s} s trước`],
    ["Dongle tx / ok / lỗi / rx", `${link.dongle.tx_count ?? "—"} / ${link.dongle.tx_ok ?? "—"} / ${link.dongle.tx_fail ?? "—"} / ${link.dongle.rx_count ?? "—"}`],
    ["Hàng đợi dongle", `${link.dongle.queue_depth ?? "—"} · telemetry bị bỏ ${link.dongle.telem_dropped ?? "—"}`],
    ["PDR drone→GCS", link.pdr_pct === null ? "—" : `${link.pdr_pct} %`],
    ["RTT ACK tb / max", `${link.rtt_ms_avg ?? "—"} / ${link.rtt_ms_max ?? "—"} ms`],
    ["Thống kê phiên", Object.entries(link.stats).map(([k, v]) => `${k}=${v}`).join(" · ")],
    ["map_crc drone", link.drone_map_crc?.toString(16).toUpperCase() ?? "—"],
    ["RENDER_DELAY_MS (3D trễ so với thực tế)", `${renderDelay} ms`],
  ] : [];

  return (
    <div className="page">
      <div className="panel">
        <h3>Chẩn đoán liên kết</h3>
        <div className="sparks">
          <Sparkline values={hist.map((h) => h.rssi)} label="RSSI dongle" unit="dBm" min={-100} max={-20} />
          <Sparkline values={hist.map((h) => h.pdr)} label="PDR" unit="%" min={0} max={100} color="#15803d" />
          <Sparkline values={hist.map((h) => h.rtt)} label="RTT ACK" unit="ms" min={0} color="#d97706" />
        </div>
        <p className="muted small">Đồ thị 10 phút gần nhất (1 Hz). Lịch sử dài hơn: GET /api/link/history.</p>
        <table className="kv"><tbody>{kv.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
      </div>
      {admin && (
        <div className="panel">
          <h3>Cấu hình ESP-NOW (dongle)</h3>
          <div className="form-row">
            <label>Kênh <input type="number" min={1} max={14} value={cfg.channel} onChange={(e) => setCfg({ ...cfg, channel: +e.target.value })} /></label>
            <label className="check"><input type="checkbox" checked={cfg.lr_mode} onChange={(e) => setCfg({ ...cfg, lr_mode: e.target.checked })} /> LR mode</label>
            <label>MAC peer <input value={cfg.peer_mac} onChange={(e) => setCfg({ ...cfg, peer_mac: e.target.value })} /></label>
            <button onClick={async () => { try { const r = await post<{ note: string }>("/api/link/config", cfg); setMsg(r.note); } catch (e) { setMsg(errMsg(e)); } }}>Gửi cấu hình</button>
          </div>
          <p className="muted small">Kênh cố định, KHÔNG để 0; khuyến nghị kênh 1 và video WiFi ở 5 GHz (1.3). LR mode phải bật CẢ HAI đầu rồi đo lại PDR.</p>
          {msg && <div className="note small">{msg}</div>}
        </div>
      )}
    </div>
  );
}
