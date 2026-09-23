// Banner trạng thái: mất liên kết (9.2), lệch bản đồ tag (8.6), lệch MAJOR hợp đồng (6.2), lệch ngưỡng (9.4)
import { Link } from "react-router-dom";
import { download } from "../../lib/api";
import { hex8 } from "../../lib/units";
import { useIsAdmin } from "../../store/auth";
import { useLive } from "../../store/live";

export function Banners() {
  const connected = useLive((s) => s.connected);
  const link = useLive((s) => s.link);
  const map = useLive((s) => s.map);
  const alerts = useLive((s) => s.alerts);
  const contract = useLive((s) => s.contract);
  const admin = useIsAdmin();

  const drift = alerts.find((a) => a.code === "CONFIG_DRIFT");
  const out: React.ReactNode[] = [];
  if (!connected) out.push(<div key="ws" className="banner red">Mất kết nối tới backend GCS — đang thử lại…</div>);
  if (link && !link.up) {
    out.push(
      <div key="link" className="banner orange">
        Mất liên kết drone — vị trí trong 3D đang đóng băng; quá 10 s nữa drone tự kích RTH
        {link.rx_age_s !== null && ` · gói cuối cách đây ${link.rx_age_s.toFixed(0)} s`}
      </div>,
    );
  }
  if (contract?.major_mismatch) {
    out.push(
      <div key="contract" className="banner red">
        Lệch MAJOR hợp đồng: GCS {contract.gcs} ≠ drone {contract.drone} — KHÔNG soạn được nhiệm vụ.
        Lệnh khẩn vẫn gửi được (giao ước 6.2).
      </div>,
    );
  }
  if (map && map.drone_crc !== null && !map.in_sync) {
    out.push(
      <div key="map" className="banner red">
        Bản đồ tag lệch: GCS {hex8(map.gcs_crc)} ≠ drone {hex8(map.drone_crc)} — CHẶN nạp kế hoạch
        (lệnh khẩn vẫn cho phép). Bản đồ đi bằng file: tải tags.yaml rồi chép sang Pi và khởi động lại (8.6).
        {admin && <button onClick={() => { void download("/api/tags/export", "tags.yaml"); }}>Tải tags.yaml</button>}
      </div>,
    );
  }
  if (drift) out.push(<div key="drift" className="banner yellow">{drift.message} {admin && <Link to="/admin">→ Quản trị</Link>}</div>);
  return <div className="banners">{out}</div>;
}
