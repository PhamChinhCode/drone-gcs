"""Kịch bản đầu-cuối qua REST với Pi giả lập nói MAVLink — phía GCS của phép kiểm 10.A.

Khu vực thu nhỏ (tag cách nhau 4 m, bay ở 1,5 m) để nhiệm vụ A→B→Home chạy trong ~30 s.
"""
import asyncio
import os
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from gcs_backend.link_mav.tagmap import tagmap_crc
from gcs_backend.sim.fake_pi import FakePi


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SimThread:
    """FakePi chạy ở event loop riêng: GCS và Pi là hai tiến trình khác nhau ngoài đời thật."""

    def __init__(self, gcs_port: int) -> None:
        self.loop = asyncio.new_event_loop()
        self.pi: FakePi | None = None
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)

            async def boot() -> None:
                self.pi = FakePi(("127.0.0.1", gcs_port))
                self.pi.tags = {0: (0.0, 0.0, 0.0), 1: (4.0, 0.0, 0.0), 2: (4.0, 4.0, 0.0)}
                await self.pi.start()
                ready.set()
            self.loop.run_until_complete(boot())
            self.loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)

    def call(self, fn):
        self.loop.call_soon_threadsafe(fn)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    port = _free_port()
    sim = SimThread(port)
    db = tmp_path_factory.mktemp("db") / "gcs.db"
    os.environ.update({"GCS_MAV_HOST": "127.0.0.1", "GCS_MAV_PORT": str(port),
                       "GCS_SIGNING": "false",  # mạng kín trong test; mặc định của sản phẩm là BẬT (7.6)
                       "GCS_DB_URL": f"sqlite:///{db}", "GCS_TELEMETRY_STORE_HZ": "10"})
    from gcs_backend.config import get_settings
    get_settings.cache_clear()
    from gcs_backend.main import app
    with TestClient(app) as client:
        tok = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        client.headers["Authorization"] = f"Bearer {tok}"
        yield client, sim


def wait_for(fn, timeout, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


SMALL = {
    "site": {"name": "Bàn thử", "link_radius_meas_m": 100, "cruise_alt_default_m": 1.5},
    "tags": [
        {"tag_id": 0, "label": "Home", "pos_n_m": 0, "pos_e_m": 0, "tag_size_m": 0.3, "kind": "home"},
        {"tag_id": 1, "label": "A", "pos_n_m": 4, "pos_e_m": 0, "tag_size_m": 0.3, "kind": "pickup"},
        {"tag_id": 2, "label": "B", "pos_n_m": 4, "pos_e_m": 4, "tag_size_m": 0.3, "kind": "dropoff"},
    ],
    "areas": [{"kind": "operating", "name": "bàn", "vertices": [[-6, -6], [-6, 8], [8, 8], [8, -6]]}],
}


def _sync_tagmap(c, sim):
    """Pi lấy bản đồ từ tags.yaml người triển khai chép sang, rồi báo tagmap_crc lên (8.6)."""
    crc = tagmap_crc(c.get("/api/sites/1/tags").json()["tags"])
    sim.call(lambda: setattr(sim.pi, "tagmap_crc", crc))
    return wait_for(lambda: c.get("/api/state").json()["map"]["in_sync"], 5)


def test_design_save_requires_ack_and_blocks_errors(env):
    c, _ = env
    bad = {**SMALL, "tags": SMALL["tags"] + [dict(SMALL["tags"][1], label="A2")]}
    assert c.put("/api/sites/1/design", json=bad).status_code == 422
    assert c.put("/api/sites/1/design", json=SMALL).status_code == 409          # W_AREA_SMALL cần xác nhận
    r = c.put("/api/sites/1/design", json={**SMALL, "ack_warnings": True})
    assert r.status_code == 200, r.text
    assert len(r.json()["tags"]) == 3


def test_pi_goi_ra_truoc_va_gcs_thay_lien_ket(env):
    """Giao ước 2.1: GCS không mở kết nối được vì Pi nằm sau CGNAT — Pi phát heartbeat trước."""
    c, _ = env
    assert wait_for(lambda: c.get("/api/state").json()["link"]["up"], 10)
    # heartbeat 1 Hz dựng liên kết trước; trạng thái tới ở nhịp DRONE_TELEMETRY 2 Hz ngay sau đó
    assert wait_for(lambda: c.get("/api/state").json()["shadow"]["fsm_name"] == "IDLE", 5)
    st = c.get("/api/state").json()
    assert st["link"]["peer"] is not None
    assert st["contract"]["drone"] == 500 and not st["contract"]["major_mismatch"]


def test_khong_co_vi_tri_khi_dau_tren_dat(env):
    """5.2b: drone không thấy pad của chính nó, nên POS_VALID = 0 trước khi cất cánh — nhánh THƯỜNG GẶP."""
    c, _ = env
    assert wait_for(lambda: c.get("/api/state").json()["shadow"]["telem"] is not None, 5)
    assert c.get("/api/state").json()["shadow"]["telem"]["valid"]["pos"] is False
    assert c.get("/api/state").json()["shadow"]["fast"] is None, "không phát = không biết, không phải 0"


def test_pin_va_gps_khong_bao_gio_hien_so(env):
    """FC chưa gửi BATTERY_STATUS và chưa có GPS: bit luôn 0, GCS không được vẽ đồng hồ pin (5.2)."""
    c, _ = env
    t = wait_for(lambda: c.get("/api/state").json()["shadow"]["telem"], 5)
    assert t["battery_pct"] is None and t["lat"] is None
    assert any(a["code"] == "BATT_UNAVAILABLE" for a in c.get("/api/alerts").json()["active"])


def test_ban_do_lech_thi_chan_nap_ke_hoach(env):
    c, sim = env
    sim.call(lambda: setattr(sim.pi, "tagmap_crc", 0xDEADBEEF))
    assert wait_for(lambda: not c.get("/api/state").json()["map"]["in_sync"], 5)
    mission = c.post("/api/missions", json={"pickup_tag": 1, "dropoff_tag": 2, "cruise_alt_m": 1.5}).json()
    assert c.post(f"/api/missions/{mission['id']}/upload").status_code == 409
    assert _sync_tagmap(c, sim)


def test_full_mission_a_to_b_home(env):
    c, sim = env
    assert _sync_tagmap(c, sim)
    mission = c.post("/api/missions", json={"pickup_tag": 1, "dropoff_tag": 2, "cruise_alt_m": 1.5}).json()
    assert [w["tag_id"] for w in mission["waypoints"]] == [1, 2, 0], "một mục mỗi điểm dừng (3.2b)"

    r = c.post(f"/api/missions/{mission['id']}/upload")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "ready"
    assert len(sim.pi.plan) == 3 and sim.pi.mission_id == mission["id"]
    assert sim.pi.state == 0, "nạp kế hoạch KHÔNG tự kích hoạt bay"

    r = c.post(f"/api/missions/{mission['id']}/start")
    assert r.status_code == 200, r.text
    assert r.json()["warnings"], "chưa có vị trí trên đất -> cảnh báo, nhưng không chặn (11.P18)"
    cmd_id = r.json()["id"]
    assert wait_for(lambda: any(x["id"] == cmd_id and x["stage"] == "effective"
                                for x in c.get("/api/commands").json()), 5)
    # chuyến bay giả lập mất ~30 s; hạn rộng vì mô phỏng chạy theo đồng hồ thật và máy bận thì trôi
    done = wait_for(lambda: c.get(f"/api/missions/{mission['id']}").json()["state"] in ("done", "failed"),
                    150, 1)
    mi = c.get(f"/api/missions/{mission['id']}").json()
    assert done and mi["state"] == "done", mi
    assert all(w["reached_at"] for w in mi["waypoints"])
    assert len(c.get(f"/api/missions/{mission['id']}/telemetry").json()["samples"]) > 20


def test_ket_qua_chuyen_bay_doc_tu_flight_result(env):
    """MISSION_COMPLETE là đích chung của cả huỷ, RTH, LAND và hết kế hoạch — chỉ flight_result mới
    phân biệt (8.5b). Đọc giá trị 9 là 'thành công' thì mọi lệnh huỷ đều báo thành công."""
    c, _ = env
    done = [m for m in c.get("/api/missions").json() if m["state"] == "done"]
    assert done, "chuyến COMPLETED phải kết thúc được"
    assert all(w["reached_at"] for w in c.get(f"/api/missions/{done[0]['id']}").json()["waypoints"])
    assert c.get("/api/state").json()["shadow"]["telem"]["flight_result"] == 1  # COMPLETED


def test_huy_nhiem_vu_khong_bi_bao_la_thanh_cong(env):
    """Chuyến bị huỷ cũng đi qua MISSION_COMPLETE; flight_result = ABORTED mới nói đúng sự thật."""
    c, sim = env
    assert _sync_tagmap(c, sim)
    assert wait_for(lambda: sim.pi.state == 0, 10), "drone phải rảnh trước khi nạp kế hoạch mới"
    mission = c.post("/api/missions", json={"pickup_tag": 1, "dropoff_tag": 2, "cruise_alt_m": 1.5}).json()
    r = c.post(f"/api/missions/{mission['id']}/upload")
    assert r.status_code == 200, r.text
    c.post(f"/api/missions/{mission['id']}/start")
    assert wait_for(lambda: sim.pi.state not in (0, 9), 10), "drone phải rời IDLE"
    c.post("/api/commands/emergency", json={"action": "ABORT_MISSION"})
    st = wait_for(lambda: c.get(f"/api/missions/{mission['id']}").json()["state"] in ("aborted", "failed", "done"), 60, 1)
    mi = c.get(f"/api/missions/{mission['id']}").json()
    assert st and mi["state"] == "aborted", mi
    assert c.get("/api/state").json()["shadow"]["telem"]["flight_result"] == 2  # ABORTED


def test_vi_tri_chi_co_sau_khi_odom_neo(env):
    """POS_VALID bật khi đã neo, và cờ latching nên không tắt lại sau khi hạ cánh (5.2b)."""
    c, _ = env
    st = c.get("/api/state").json()
    assert st["shadow"]["telem"]["valid"]["pos"] is True
    assert st["shadow"]["fast"] is not None
    assert st["shadow"]["telem"]["home_ne"] is not None, "nhà của RTH vẽ từ home_*, không từ pad_home"


def test_lenh_khan_land_doi_trang_thai(env):
    c, _ = env
    r = c.post("/api/commands/emergency", json={"action": "LAND"})
    assert r.status_code == 200 and r.json()["stage"] == "sending"
    cmd_id = r.json()["id"]
    assert wait_for(lambda: next(x["stage"] for x in c.get("/api/commands").json() if x["id"] == cmd_id)
                    in ("acked", "effective"), 5)


def test_khong_co_duong_nao_arm_hay_goto(env):
    """Giao ước 9.3: GCS không arm, không lái. Các endpoint đó phải KHÔNG còn tồn tại."""
    c, _ = env
    assert c.post("/api/commands/emergency", json={"action": "ARM"}).status_code == 422
    # đường đã gỡ: SPA catch-all chỉ nhận GET nên POST vào đó trả 405
    assert c.post("/api/commands/goto", json={"ref_frame": "MAP_NED", "x": 1, "y": 1, "z": -1}).status_code == 405
    assert c.post("/api/commands/simple", json={"action": "TAKEOFF"}).status_code == 405


def test_operator_khong_duoc_disarm(env):
    c, _ = env
    op = c.post("/api/auth/login", json={"username": "operator", "password": "operator"}).json()["token"]
    r = c.post("/api/commands/emergency", json={"action": "DISARM"},
               headers={"Authorization": f"Bearer {op}"})
    assert r.status_code == 403


def test_doi_chieu_nguong_chi_doc(env):
    c, sim = env
    sim.call(lambda: sim.pi.params.__setitem__("LOW_BATT_PCT", 30.0))
    c.post("/api/config/check")
    drift = wait_for(lambda: c.get("/api/config/drift").json()["drift"], 5)
    assert [d["key"] for d in drift] == ["low_battery_pct"]
    assert wait_for(lambda: any(a["code"] == "CONFIG_DRIFT" and "low_battery_pct" in a["message"]
                                for a in c.get("/api/alerts").json()["active"]), 3)
    sim.call(lambda: sim.pi.params.__setitem__("LOW_BATT_PCT", 25.0))


def test_xuat_tags_yaml_nap_duoc_bang_bo_doc_cua_pi(env):
    """File xuất là FILE THAM SỐ ROS: có header, known_tags phẳng, mọi số cùng kiểu float (8.6).

    Kiểm bằng cách đọc lại đúng như Pi đọc rồi tính CRC — nếu định dạng sai thì hoặc yaml vỡ, hoặc
    CRC lệch, và cả hai đều lộ ra ở đây chứ không phải lúc drone bay sai chỗ.
    """
    import yaml

    from gcs_backend.link_mav.tagmap import tagmap_crc

    c, _ = env
    r = c.get("/api/tags/export")
    assert r.status_code == 200
    params = yaml.safe_load(r.text)["/**"]["ros__parameters"]
    flat = params["known_tags"]
    assert len(flat) % 4 == 0, "danh sách phẳng [id, x, y, z, ...]"
    assert all(isinstance(v, float) for v in flat), "trộn int với float là ROS báo lỗi kiểu"
    assert len(params["tag_frames"]) == len(flat) // 4
    assert params["tag_frames"][0] == "pad_home"

    tags = {int(flat[i]): flat[i + 1:i + 4] for i in range(0, len(flat), 4)}
    crc = tagmap_crc([{"tag_id": k, "pos_e_m": v[0], "pos_n_m": v[1], "pos_d_m": -v[2]}
                      for k, v in tags.items()])
    assert crc == c.get("/api/state").json()["map"]["gcs_crc"], "file xuất phải cho đúng CRC của GCS"


def test_operator_cannot_edit_tags(env):
    c, _ = env
    op = c.post("/api/auth/login", json={"username": "operator", "password": "operator"}).json()["token"]
    r = c.post("/api/tags", json={"tag_id": 9, "label": "X", "pos_n_m": 1, "pos_e_m": 1,
                                  "tag_size_m": 0.3, "kind": "waypoint"},
               headers={"Authorization": f"Bearer {op}"})
    assert r.status_code == 403
