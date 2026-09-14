"""Kịch bản đầu-cuối qua REST với drone giả lập — tiêu chí xong của 7.5 (T0, không phần cứng).

Khu vực thu nhỏ (tag cách nhau 4 m, cao hành trình 1,5 m) để nhiệm vụ A→B→Home chạy trong ~30 s.
"""
import asyncio
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from gcs_backend.sim.fake_drone import FakeDrone, Faults, start_link_server

KEY = "00112233445566778899aabbccddeeff"


class SimThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.drone: FakeDrone | None = None
        self.port = 0
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)

            async def boot() -> None:
                self.drone = FakeDrone(bytes.fromhex(KEY), Faults())
                server = await start_link_server(self.drone, "127.0.0.1", 0)
                self.port = server.sockets[0].getsockname()[1]
                ready.set()
            self.loop.run_until_complete(boot())
            self.loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)

    def call(self, fn):
        self.loop.call_soon_threadsafe(fn)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    sim = SimThread()
    db = tmp_path_factory.mktemp("db") / "gcs.db"
    os.environ.update({"GCS_LINK_URL": f"tcp://127.0.0.1:{sim.port}", "GCS_DB_URL": f"sqlite:///{db}",
                       "GCS_SESSION_KEY": KEY, "GCS_TELEMETRY_STORE_HZ": "10"})
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


def test_design_save_requires_ack_and_blocks_errors(env):
    c, _ = env
    bad = {**SMALL, "tags": SMALL["tags"] + [dict(SMALL["tags"][1], label="A2")]}
    assert c.put("/api/sites/1/design", json=bad).status_code == 422
    assert c.put("/api/sites/1/design", json=SMALL).status_code == 409          # W_AREA_SMALL cần xác nhận
    r = c.put("/api/sites/1/design", json={**SMALL, "ack_warnings": True})
    assert r.status_code == 200, r.text
    assert len(r.json()["tags"]) == 3


def test_full_mission_a_to_b_home(env):
    c, sim = env
    assert wait_for(lambda: c.get("/api/state").json()["link"]["state"] == "ok", 10)
    # bản đồ drone rỗng → lệch → nhiệm vụ bị chặn
    assert wait_for(lambda: c.get("/api/state").json()["map"]["drone_crc"] is not None, 5)
    assert not c.get("/api/state").json()["map"]["in_sync"]
    mission = c.post("/api/missions", json={"pickup_tag": 1, "dropoff_tag": 2, "cruise_alt_m": 1.5}).json()
    assert c.post(f"/api/missions/{mission['id']}/upload").status_code == 409

    r = c.post("/api/tags/sync")
    assert r.status_code == 200, r.text
    assert c.get("/api/state").json()["map"]["in_sync"]  # ngay sau ACK TAGMAP_END, không chờ heartbeat

    r = c.post(f"/api/missions/{mission['id']}/upload")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "ready"
    assert sim.drone.plan_ready and len(sim.drone.plan) == 9
    assert sim.drone.fsm.name == "IDLE"  # tải lên KHÔNG tự kích hoạt bay (5.6)

    r = c.post(f"/api/missions/{mission['id']}/start")
    assert r.status_code == 200, r.text
    cmd_id = r.json()["id"]
    assert wait_for(lambda: any(x["id"] == cmd_id and x["stage"] == "effective"
                                for x in c.get("/api/commands").json()), 5)
    done = wait_for(lambda: c.get(f"/api/missions/{mission['id']}").json()["state"] in ("done", "failed"), 90, 1)
    mi = c.get(f"/api/missions/{mission['id']}").json()
    assert done and mi["state"] == "done", mi
    assert all(w["reached_at"] for w in mi["waypoints"])
    tel = c.get(f"/api/missions/{mission['id']}/telemetry").json()
    assert len(tel["samples"]) > 50
    rep = c.get(f"/api/missions/{mission['id']}/report").json()
    assert rep["distance_m"] > 10


def test_a11_ack_without_effect_raises_alert(env):
    c, sim = env
    sim.call(lambda: setattr(sim.drone.faults, "no_effect", True))
    try:
        r = c.post("/api/commands/simple", json={"action": "ARM"})
        cmd_id = r.json()["id"]
        stage = wait_for(lambda: next(x["stage"] for x in c.get("/api/commands").json() if x["id"] == cmd_id)
                         == "not_effective", 5)
        assert stage
        alerts = c.get("/api/alerts").json()["active"]
        assert any(a["code"].startswith("CMD_NOT_EFFECTIVE") for a in alerts)
    finally:
        sim.call(lambda: setattr(sim.drone.faults, "no_effect", False))


def test_a10_config_drift_names_key(env):
    c, sim = env
    sim.call(lambda: sim.drone.params.__setitem__("low_battery_pct", 30.0))
    c.post("/api/config/check")
    drift = wait_for(lambda: c.get("/api/config/drift").json()["drift"], 5)
    assert [d["key"] for d in drift] == ["low_battery_pct"]
    assert wait_for(lambda: any(a["code"] == "CONFIG_DRIFT" and "low_battery_pct" in a["message"]
                                for a in c.get("/api/alerts").json()["active"]), 3)
    sim.call(lambda: sim.drone.params.__setitem__("low_battery_pct", 25.0))


def test_a9_upload_survives_packet_loss(env):
    c, sim = env
    sim.call(lambda: setattr(sim.drone.faults, "drop", 0.25))
    try:
        mission = c.post("/api/missions", json={"pickup_tag": 2, "dropoff_tag": 1, "cruise_alt_m": 1.5}).json()
        r = c.post(f"/api/missions/{mission['id']}/upload")
        assert r.status_code == 200, r.text
        assert sim.drone.plan_ready and len(sim.drone.plan) == 9
    finally:
        sim.call(lambda: setattr(sim.drone.faults, "drop", 0.0))


def test_emergency_three_stage_and_kill_guard(env):
    c, _ = env
    op_tok = c.post("/api/auth/login", json={"username": "operator", "password": "operator"}).json()["token"]
    r = c.post("/api/commands/emergency", json={"action": "KILL", "confirm_token": "KILL"},
               headers={"Authorization": f"Bearer {op_tok}"})
    assert r.status_code == 403
    assert c.post("/api/commands/emergency", json={"action": "KILL"}).status_code == 400
    r = c.post("/api/commands/emergency", json={"action": "HOLD"})
    cmd_id = r.json()["id"]
    assert r.json()["stage"] == "sending"
    assert wait_for(lambda: next(x["stage"] for x in c.get("/api/commands").json() if x["id"] == cmd_id)
                    in ("acked", "effective"), 3)


def test_operator_cannot_edit_tags(env):
    c, _ = env
    op_tok = c.post("/api/auth/login", json={"username": "operator", "password": "operator"}).json()["token"]
    r = c.post("/api/tags", json={"tag_id": 9, "label": "X", "pos_n_m": 1, "pos_e_m": 1, "tag_size_m": 0.3,
                                  "kind": "waypoint"}, headers={"Authorization": f"Bearer {op_tok}"})
    assert r.status_code == 403
