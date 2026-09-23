// Chẩn đoán liên kết (giao ước 7.4): biến "đường truyền tệ" thành con số.
//
// Tám bộ đếm của DRONE_LINK_STATS được hiển thị ở CẢ HAI PHÍA, vì ba nguyên nhân hay bị gộp làm
// một lại cần ba cách sửa khác nhau: mất gói (rx_drop), nghẽn hàng đợi (queue_depth / tx_dropped),
// sai chữ ký (rx_bad_sig — chuyện an ninh, không phải chuyện sóng).
//
// UINT32_MAX / 0xFFFF nghĩa là bên phát KHÔNG ĐO ĐƯỢC; hiện "—", không hiện con số đó.
import type { LinkCounters } from "../../lib/types";
import { known, UNKNOWN16 } from "../../lib/types";
import { useLive } from "../../store/live";
import { Sparkline } from "./Sparkline";

const num = (v: number | null | undefined, max?: number) => {
  const k = known(v, max);
  return k === null ? "—" : k.toLocaleString("vi-VN");
};

function Counters({ title, c, note }: { title: string; c: Partial<LinkCounters> | null; note: string }) {
  const rows: [string, string][] = [
    ["Gói nhận hợp lệ", num(c?.rx_ok)],
    ["Gói mất (khoảng trống seq)", num(c?.rx_drop)],
    ["Sai CRC", num(c?.rx_bad_crc)],
    ["Sai chữ ký", num(c?.rx_bad_sig)],
    ["Gói đã gửi", num(c?.tx_sent)],
    ["Telemetry bỏ do nghẽn", num(c?.tx_dropped)],
    ["Hàng đợi hiện tại", num(c?.queue_depth, UNKNOWN16)],
  ];
  return (
    <div className="panel">
      <h3>{title}</h3>
      {c ? <table className="kv"><tbody>{rows.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
        : <p className="muted">chưa nhận được số liệu</p>}
      <p className="muted small">{note}</p>
    </div>
  );
}

export function LinkDiagnostics() {
  const link = useLive((s) => s.link);
  const telem = useLive((s) => s.telem);
  const hist = useLive((s) => s.linkHistory);
  const renderDelay = useLive((s) => s.renderDelayMs);

  const kv: [string, React.ReactNode][] = link ? [
    ["Trạng thái", <b className={link.up ? "good" : "bad"}>{link.up ? "đang liên lạc" : "mất liên kết"}</b>],
    ["Đường về (địa chỉ Pi)", link.peer ?? "chưa có gói hợp lệ nào"],
    ["Gói cuối nhận được", link.rx_age_s === null ? "chưa có" : `${link.rx_age_s} s trước`],
    ["RTT (TIMESYNC)", link.rtt_ms === null ? "chưa đo được" : `${link.rtt_ms} ms`],
    ["Chữ ký gói", link.signing
      ? <b className="good">BẬT</b>
      : <b className="warn">TẮT — chỉ được phép trong mạng kín (7.6)</b>],
    ["RSSI modem 4G", telem?.rssi_dbm === null || telem?.rssi_dbm === undefined ? "—" : `${telem.rssi_dbm} dBm`],
    ["Hợp đồng drone", telem ? telem.contract_ver : "—"],
    ["RENDER_DELAY_MS (3D trễ so với thực tế)", `${renderDelay} ms`],
  ] : [];

  return (
    <div className="page">
      <div className="panel">
        <h3>Chẩn đoán liên kết</h3>
        <div className="sparks">
          <Sparkline values={hist.map((h) => h.rssi)} label="RSSI modem" unit="dBm" min={-110} max={-40} />
          <Sparkline values={hist.map((h) => h.drop)} label="Gói mất (cộng dồn)" unit="" min={0} color="#b91c1c" />
          <Sparkline values={hist.map((h) => h.rtt)} label="RTT" unit="ms" min={0} color="#d97706" />
        </div>
        <p className="muted small">Đồ thị 10 phút gần nhất (1 Hz). Lịch sử dài hơn: GET /api/link/history.</p>
        <table className="kv"><tbody>{kv.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
      </div>
      <Counters title="Phía GCS đếm được" c={link?.gcs ?? null}
        note="Gói mất ở đây là gói Pi → GCS. GCS gửi thẳng ra socket nên hàng đợi luôn 0 — đó là số thật." />
      <Counters title="Phía drone báo về (DRONE_LINK_STATS)" c={link?.drone ?? null}
        note="Gói mất ở đây là gói GCS → Pi. Hàng đợi tăng dần nghĩa là nghẽn đường lên, không phải mất sóng." />
    </div>
  );
}
