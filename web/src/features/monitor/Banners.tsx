// Banner trạng thái: phân biệt mất dongle / mất drone / drone im lặng (5.8), lệch bản đồ (3.3), lệch cấu hình (10.1)
import { useState } from "react";
import { Link } from "react-router-dom";
import { errMsg, post } from "../../lib/api";
import { hex8 } from "../../lib/units";
import { useIsAdmin } from "../../store/auth";
import { useLive } from "../../store/live";

const LINK_BANNER: Record<string, [string, string]> = {
  dongle_lost: ["red", "MẤT DONGLE — ESP32-GCS không trả lời qua serial (kiểm tra cáp USB)"],
  drone_lost: ["orange", "Mất liên kết drone — vị trí trong 3D đang đóng băng"],
  drone_lost_long: ["red", "Mất liên kết drone > 10 s — drone sẽ tự kích RTH theo cấu hình"],
  drone_silent: ["purple", "Drone im lặng nhưng dongle vẫn gửi thành công — nghi treo phần mềm Pi 4"],
};

export function Banners() {
  const connected = useLive((s) => s.connected);
  const link = useLive((s) => s.link);
  const map = useLive((s) => s.map);
  const alerts = useLive((s) => s.alerts);
  const admin = useIsAdmin();
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const drift = alerts.find((a) => a.code === "CONFIG_DRIFT");
  const out: React.ReactNode[] = [];
  if (!connected) out.push(<div key="ws" className="banner red">Mất kết nối tới backend GCS — đang thử lại…</div>);
  if (link && link.state !== "ok") {
    const [c, t] = LINK_BANNER[link.state];
    out.push(<div key="link" className={`banner ${c}`}>{t}{link.drone_hb_age_s !== null && ` · heartbeat cách đây ${link.drone_hb_age_s.toFixed(0)} s`}</div>);
  }
  if (map && map.drone_crc !== null && !map.in_sync) {
    out.push(
      <div key="map" className="banner red">
        Bản đồ tag lệch: GCS {hex8(map.gcs_crc)} ≠ drone {hex8(map.drone_crc)} — CHẶN lệnh nhiệm vụ (lệnh tay & khẩn cấp vẫn cho phép).
        {admin && (
          <button disabled={syncing} onClick={async () => {
            setSyncing(true); setSyncMsg(null);
            try { await post("/api/tags/sync"); } catch (e) { setSyncMsg(errMsg(e)); }
            setSyncing(false);
          }}>{syncing ? "Đang đồng bộ…" : "Đồng bộ bản đồ"}</button>
        )}
        {syncMsg && <span> · {syncMsg}</span>}
      </div>,
    );
  }
  if (drift) out.push(<div key="drift" className="banner yellow">{drift.message} {admin && <Link to="/admin">→ Quản trị</Link>}</div>);
  return <div className="banners">{out}</div>;
}
