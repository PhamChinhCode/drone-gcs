// Tab Drone: chuẩn bị trước khi giao nhiệm vụ.
//
// Đây KHÔNG phải màn hình "kết nối". GCS không mở được kết nối tới drone: modem 4G nằm sau CGNAT
// nên chỉ Pi gọi ra trước, GCS chỉ ngồi nghe :14550 (giao ước 2.1). Vẽ một nút "Kết nối" ở đây thì
// người vận hành bấm xong sẽ tưởng mình vừa làm gì đó, và khi không lên thì đi tìm sai chỗ.
//
// Thay vào đó: danh sách drone đang liên lạc, và với drone đang chọn thì nói rõ còn thiếu gì để
// bay được. Danh sách dựng dạng nhiều dòng ngay từ đầu để lên đa drone không phải làm lại —
// nhưng đa drone còn cần sửa giao ước 2.2 (Pi cố định sysid = 1 nên hai drone cùng cổng sẽ giành
// đường về của nhau), hoặc mỗi drone một cổng UDP.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { download, errMsg } from "../../lib/api";
import { FLIGHT_RESULT } from "../../lib/types";
import { hex8 } from "../../lib/units";
import { useIsAdmin } from "../../store/auth";
import { useLive } from "../../store/live";
import { useSite } from "../../store/site";

type Mark = "ok" | "warn" | "bad" | "na";

const DOT: Record<Mark, string> = { ok: "●", warn: "▲", bad: "✗", na: "—" };

function Row({ mark, label, value, note, action }: {
  mark: Mark; label: string; value: React.ReactNode; note?: string; action?: React.ReactNode;
}) {
  return (
    <tr className={mark === "bad" ? "sev2" : ""}>
      <td className={`check-dot ${mark}`}>{DOT[mark]}</td>
      <th>{label}</th>
      <td>{value}{note && <div className="muted small">{note}</div>}</td>
      <td className="nowrap">{action}</td>
    </tr>
  );
}

export function DronePage() {
  const link = useLive((s) => s.link);
  const telem = useLive((s) => s.telem);
  const shadow = useLive((s) => s.shadow);
  const map = useLive((s) => s.map);
  const contract = useLive((s) => s.contract);
  const assumed = useLive((s) => s.assumedPos);
  const { tags } = useSite();
  const admin = useIsAdmin();
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);

  const up = !!link?.up;
  const mapOk = !!map?.in_sync;
  const contractOk = !!contract && !contract.major_mismatch;
  const readyForMission = up && mapOk && contractOk;
  const hasFix = telem?.valid.pos ?? false;

  const setAssumed = (tagId: number | null) => {
    if (tagId === null) return useLive.setState({ assumedPos: null });
    const t = tags.find((x) => x.tag_id === tagId);
    if (t) useLive.setState({ assumedPos: { n: t.pos_n_m, e: t.pos_e_m, tagId } });
  };

  return (
    <div className="page drone-page">
      <div className="panel">
        <h3>Drone đang liên lạc</h3>
        {up ? (
          <table className="grid">
            <thead><tr><th /><th>Drone</th><th>Địa chỉ</th><th>Hợp đồng</th><th>Gói mất</th><th>RTT</th></tr></thead>
            <tbody>
              <tr className="sel">
                <td>●</td>
                <td><b>Drone {shadow?.drone_id ?? 1}</b></td>
                <td>{link!.peer}</td>
                <td>{contract?.drone ?? "—"}</td>
                <td>{link!.gcs.rx_drop}</td>
                <td>{link!.rtt_ms ?? "—"}</td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p className="muted">
            Đang nghe UDP :14550, chưa drone nào gọi tới. GCS không mở kết nối được — Pi phải gọi ra
            trước vì modem 4G nằm sau CGNAT (giao ước 2.1). Kiểm <code>gcs_host</code> trong
            <code> comms.yaml</code> của Pi có trỏ đúng máy này không.
          </p>
        )}
      </div>

      <div className="panel">
        <h3>Sẵn sàng bay</h3>
        <table className="kv check-list"><tbody>
          <Row mark={up ? "ok" : "bad"} label="Liên kết"
            value={up ? `${link!.gcs.rx_ok.toLocaleString("vi-VN")} gói · ${link!.gcs.rx_drop} mất` : "chưa có"}
            note={link?.signing ? undefined : "chữ ký gói đang TẮT — chỉ được phép trong mạng kín (7.6)"} />
          <Row mark={!contract?.drone ? "na" : contractOk ? "ok" : "bad"} label="Hợp đồng"
            value={contract?.drone ? `GCS ${contract.gcs} · drone ${contract.drone}` : "chưa biết"}
            note={contract?.major_mismatch ? "lệch MAJOR — không soạn được nhiệm vụ, lệnh khẩn vẫn đi" : undefined} />
          <Row mark={map?.drone_crc == null ? "na" : mapOk ? "ok" : "bad"} label="Bản đồ tag"
            value={map?.drone_crc == null ? "chưa biết"
              : `GCS ${hex8(map.gcs_crc)} ${mapOk ? "=" : "≠"} drone ${hex8(map.drone_crc)}`}
            note={mapOk ? undefined : "chép tags.yaml sang Pi rồi khởi động lại; chưa khớp thì không nạp được kế hoạch"}
            action={admin && (
              <button onClick={() => { setErr(null); download("/api/tags/export", "tags.yaml").catch((e) => setErr(errMsg(e))); }}>
                Tải tags.yaml
              </button>)} />
          <Row mark={telem?.pi_has_authority ? "ok" : "bad"} label="Quyền điều khiển"
            value={telem?.pi_has_authority == null ? "—" : telem.pi_has_authority ? "đã trao cho Pi" : "CHƯA trao"}
            note={telem?.pi_has_authority ? undefined : "người lái gạt ch5/ch8; chưa trao thì MISSION_START bị từ chối"} />
          <Row mark={hasFix ? "ok" : "warn"} label="Vị trí drone"
            value={hasFix ? "đang theo dõi" : "chưa có (POS_VALID = 0)"}
            note={hasFix ? undefined
              : "bình thường khi đậu: drone không thấy tag dưới bụng nó. Tự có sau khi leo qua ~0,8 m"} />
          <Row mark="na" label="Pin" value="không có số đo"
            note="FC chưa gửi BATTERY_STATUS — ngưỡng pin trong safety.yaml KHÔNG bảo vệ chuyến này" />
          <Row mark="na" label="Chuyến gần nhất"
            value={telem?.flight_result ? FLIGHT_RESULT[telem.flight_result]?.[1] ?? `mã ${telem.flight_result}`
              : "chưa có chuyến nào kết thúc"} />
        </tbody></table>
        {err && <div className="error small">{err}</div>}
        <div className="btn-row">
          <button className="primary" disabled={!readyForMission} onClick={() => nav("/missions")}>
            Sang giao nhiệm vụ →
          </button>
          {!readyForMission && <span className="muted small">
            cần liên kết, hợp đồng khớp và bản đồ tag khớp
          </span>}
        </div>
      </div>

      <div className="panel">
        <h3>Vị trí giả định</h3>
        <p className="muted small">
          Khi drone chưa neo được theo tag, GCS không biết nó ở đâu. Chỉ định một tag để vẽ tạm trong
          cảnh 3D. <b>Con số này không gửi xuống drone</b> và không dùng cho bất kỳ cảnh báo nào — nó
          chỉ để nhìn. Drone neo xong thì tự biến mất.
        </p>
        {hasFix ? (
          <p className="good">Drone đã có vị trí thật — không cần giả định.</p>
        ) : (
          <div className="form-row">
            <label>Drone đang đậu ở&nbsp;
              <select value={assumed?.tagId ?? ""} onChange={(e) => setAssumed(e.target.value === "" ? null : +e.target.value)}>
                <option value="">— không chỉ định —</option>
                {tags.filter((t) => t.enabled).map((t) => (
                  <option key={t.tag_id} value={t.tag_id}>{t.label} (#{t.tag_id})</option>
                ))}
              </select>
            </label>
            {assumed && <span className="muted">vẽ ở N {assumed.n.toFixed(2)} / E {assumed.e.toFixed(2)}</span>}
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Bản đồ tag ({tags.filter((t) => t.enabled).length} tag)</h3>
        <table className="grid small">
          <thead><tr><th>#</th><th>nhãn</th><th>loại</th><th>N</th><th>E</th><th>D</th></tr></thead>
          <tbody>{tags.filter((t) => t.enabled).map((t) => (
            <tr key={t.tag_id}>
              <td>{t.tag_id}</td><td>{t.label}</td><td>{t.kind}</td>
              <td>{t.pos_n_m.toFixed(2)}</td><td>{t.pos_e_m.toFixed(2)}</td><td>{t.pos_d_m.toFixed(2)}</td>
            </tr>
          ))}</tbody>
        </table>
        <p className="muted small">
          Sửa ở tab <b>Tag</b> hoặc <b>Thiết kế khu vực</b>. Bản đồ hiện đi sang Pi bằng file; nạp
          thẳng qua dây là đề xuất P31, đang chờ phía Pi.
        </p>
      </div>
    </div>
  );
}
