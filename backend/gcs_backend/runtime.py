"""Bộ khung chạy nền của backend: nối link ↔ shadow ↔ kiểm chứng lệnh ↔ cảnh báo ↔ CSDL ↔ WebSocket.

API REST gọi vào các phương thức nghiệp vụ ở đây; không module nào khác chạm tầng liên kết trực tiếp.

Ranh giới trách nhiệm theo giao ước GCS ↔ Pi mục 1.1, và nó quyết định module này KHÔNG có gì:

- **Không lái.** Không ARM, không TAKEOFF rời, không GOTO, không giữ vị trí bằng tay (9.3). Người lái
  dùng RC trực tiếp tới FC.
- **Không tự kiểm kế hoạch hợp lệ.** `mission_manager_node` là bên duy nhất được từ chối; GCS chỉ
  chặn những gì không đóng gói lên dây được.
- **Không điều khiển failsafe.** Pi leo thang; GCS chỉ hiển thị.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from .api.ws import WsHub
from .bus import EventBus
from .config import Settings
from .data.repo import Database
from .link_mav import commands as cmd
from .link_mav.commands import CommandClient, CommandTimeout
from .link_mav.dialect import drone_gcs as mav
from .link_mav.link import MavLink
from .link_mav.mission_client import CONTRACT_VER, MissionAck, MissionCancelled, MissionClient
from .link_mav.params import ParamClient
from .link_mav.tagmap import site_origin, tagmap_crc
from .link_mav.tagmap_client import TagmapAck, TagmapCancelled, TagmapClient
from .link_mav.telemetry import TelemetryDecoder
from .mission import planner
from .mission.shadow import (COMPLETE, FAILSAFE, IDLE, LANDING, RTH, CommandTracker,
                             DroneShadow, TrackedCommand, make_verifier)
from .mission.uploader import tags_yaml, to_wire
from .safety.monitor import SafetyMonitor
from .safety.thresholds import Thresholds
from .sitedesign.validator import has_errors, has_warnings, validate_design

log = logging.getLogger(__name__)
RENDER_DELAY_MS = 150  # hằng số hiển thị ở frontend — trả qua API để màn chẩn đoán hiện được
CONTRACT_MAJOR = 0     # MAJOR của giao ước GCS đang theo; lệch MAJOR thì khoá soạn nhiệm vụ (6.2)
PAD_HOME_TOL_M = 1.0   # lệch quá mức này so với pad_home thì cảnh báo trước MISSION_START (11.P18)


class ApiError(Exception):
    def __init__(self, status: int, message: str, detail=None):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.db_url)
        self.bus = EventBus()
        self.link = MavLink(self.bus, settings.mav_host, settings.mav_port,
                            signing_key=settings.signing_key_bytes)
        self.commands = CommandClient(self.link)
        self.missions = MissionClient(self.link)
        self.tagmap = TagmapClient(self.link)
        self.params = ParamClient(self.link)
        self.telemetry = TelemetryDecoder()
        self.shadow = DroneShadow(settings.drone_id)
        self.hub = WsHub(settings.ws_batch_ms)
        self.tracker = CommandTracker(self.shadow, lambda c: self.hub.push("command", c.as_dict()))
        self.monitor = SafetyMonitor(self._on_alert)
        self.site_id = settings.site_id
        self.tags: list[dict] = []
        self.areas: list[dict] = []
        self.origin: dict | None = None     # gốc WGS84 của bản đồ tag (0.7), None = Site chưa khai
        self.gcs_map_crc = 0
        self.th = Thresholds()
        self.active_mission_id: int | None = None
        self._interrupted: str | None = None
        self._last_wp_index = -1
        self._telem_buf: list[dict] = []
        self._last_store = 0.0
        self._upload_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._cancelable: dict[int, asyncio.Task] = {}
        self.link.handlers.append(self._on_msg)
        self.bus.subscribe("link.", self._on_link)
        self.bus.subscribe("mav.statustext", self._on_statustext)
        self.bus.subscribe("command.unacked", self._on_command_unacked)

    # ── vòng đời ─────────────────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.db.init(self.settings)
        self.reload_site()
        self.reload_thresholds()
        self._tasks = [asyncio.create_task(t) for t in (self.link.run(), self.hub.run(), self._periodic(),
                                                        self._flush_loop())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.to_thread(self.db.insert_samples, self._telem_buf)

    def reload_site(self) -> None:
        self.tags = self.db.list_tags(self.site_id)
        self.areas = self.db.list_areas(self.site_id)
        self.origin = site_origin(self.db.get_site(self.site_id))
        self.gcs_map_crc = tagmap_crc(self.tags, self.origin)
        self._check_map()

    def reload_thresholds(self) -> None:
        self.th = Thresholds.from_map(self.db.config_map())

    # ── trạng thái tổng hợp ─────────────────────────────────────────────────────────────────

    def map_status(self) -> dict:
        """So `tagmap_crc` hai bên. Lệch thì khoá nạp kế hoạch — drone sẽ bay tới chỗ khác chỗ GCS vẽ (8.6)."""
        drone = self.shadow.tagmap_crc
        return {"gcs_crc": self.gcs_map_crc, "drone_crc": drone,
                "in_sync": drone is not None and drone == self.gcs_map_crc}

    def contract_status(self) -> dict:
        """Lệch MAJOR: khoá soạn nhiệm vụ, nhưng lệnh khẩn vẫn gửi được (6.2)."""
        ver = self.shadow.contract_ver
        drone_major = None if ver is None else ver // 10000
        return {"gcs": CONTRACT_VER, "drone": ver, "drone_major": drone_major,
                "major_mismatch": drone_major is not None and drone_major != CONTRACT_MAJOR}

    def snapshot(self) -> dict:
        return {
            "shadow": self.shadow.snapshot(),
            "link": self.link.status(),
            "map": self.map_status(),
            "contract": self.contract_status(),
            "alerts": self.monitor.snapshot()["active"],
            "active_mission": self.db.get_mission(self.active_mission_id) if self.active_mission_id else None,
            "commands": self.tracker.recent(20),
            "thresholds": self.th.__dict__,
            "render_delay_ms": RENDER_DELAY_MS,
            "site_id": self.site_id,
        }

    def _check_map(self) -> None:
        st = self.map_status()
        if st["drone_crc"] is None:
            return
        self.monitor.set_map_mismatch(not st["in_sync"], st["gcs_crc"], st["drone_crc"])
        self.hub.push("map", st)

    def _require_link(self) -> None:
        if not self.link.state.up:
            raise ApiError(503, "chưa có liên kết với drone")

    def _require_map_sync(self) -> None:
        if not self.map_status()["in_sync"]:
            raise ApiError(409, "bản đồ tag GCS và drone lệch nhau — xuất lại tags.yaml và triển khai (8.6)",
                           self.map_status())

    def _require_contract(self) -> None:
        st = self.contract_status()
        if st["major_mismatch"]:
            raise ApiError(409, f"lệch MAJOR hợp đồng: GCS {CONTRACT_MAJOR}, drone {st['drone_major']} — "
                                "không soạn nhiệm vụ được; lệnh khẩn vẫn gửi được (6.2)", st)

    def _on_alert(self, alert: dict, action: str) -> None:
        self.hub.push("alert", {"action": action, "alert": alert})
        if action == "raise":
            self.db.add_event(mission_id=self.active_mission_id, severity=alert["severity"],
                              category=alert["category"], message=alert["message"],
                              payload={"code": alert["code"]})

    # ── nhận từ liên kết ────────────────────────────────────────────────────────────────────

    def _on_msg(self, msg) -> None:
        got = self.telemetry.feed(msg)
        if got is None:
            return
        kind, si = got
        now = time.monotonic()
        if kind == "fast":
            self.shadow.apply_fast(si, now)
            self.hub.push("telem_fast", si)
            self.monitor.evaluate_state({**(self.shadow.telem or {}), "pos": si["pos"]},
                                        self.th, self.areas)
            self._evaluate_commands()
            self._record(si)
        else:
            changed = self.shadow.apply_telem(si, now)
            fast = self.shadow.fast if not self.shadow.stale_fast() else None
            self.monitor.evaluate_state({**si, "pos": fast["pos"] if fast else None}, self.th, self.areas)
            self._mission_lifecycle(si)
            self._check_map()
            snap = self.shadow.snapshot()
            self.hub.push("telem", {k: v for k, v in snap.items() if k != "fast"} | {"changed": changed})
            self._evaluate_commands()

    def _on_statustext(self, topic: str, data: dict) -> None:
        """Log người đọc được của Pi (7.5). WARN trở lên mới được gửi, nên mọi dòng đều đáng lưu."""
        sev = data["severity"]
        self.db.add_event(mission_id=self.active_mission_id, severity=min(3, max(0, 6 - sev)),
                          category="drone", message=data["text"])
        self.hub.push("statustext", data)

    def _on_command_unacked(self, topic: str, data: dict) -> None:
        self.monitor.raise_("EMERGENCY_UNACKED", 3, "command",
                            f"Lệnh khẩn {data['command']} chưa có ACK sau {data['attempts']} lần — "
                            "vẫn đang phát lại mỗi 0,5 s")

    def _on_link(self, topic: str, data: dict) -> None:
        if topic == "link.status":
            self.hub.push("link", data["status"])
            self.monitor.evaluate_link("ok" if data["status"]["up"] else "drone_lost")
        elif topic == "link.state_changed":
            up = data["up"]
            self.db.add_event(mission_id=self.active_mission_id, severity=0 if up else 2, category="link",
                              message="liên kết với drone " + ("lên" if up else "mất"))
            if up:
                asyncio.get_running_loop().create_task(self._refresh_params())

    async def _refresh_params(self) -> None:
        """Nối lại thì đối chiếu ngưỡng (9.4). Chỉ đọc — ngưỡng sửa ở safety.yaml phía Pi."""
        try:
            await self.params.request_all()
        except Exception:
            log.exception("đọc tham số lỗi")
            return
        for key, row in self.params.compare(self.db.config_map()).items():
            if row["drone"] is not None:
                self.db.set_drone_value(key, f"{row['drone']:.4g}")
        drift = self.config_drift()
        self.monitor.set_drift(drift)
        self.hub.push("config_drift", {"drift": drift})

    def config_drift(self) -> list[dict]:
        return [c for c in self.db.get_config() if c["in_sync"] == 0]

    def _evaluate_commands(self) -> None:
        for c in self.tracker.evaluate():
            self.monitor.raise_(f"CMD_NOT_EFFECTIVE_{c.id}", 3, "command",
                                f"Lệnh {c.kind}/{c.action} đã nhận (ACK) nhưng không có hiệu lực sau "
                                f"{c.verify_window_s:g} s", transient_s=120)

    def _record(self, si: dict) -> None:
        hz = self.settings.telemetry_store_hz
        now = time.time()
        if hz < 5 and now - self._last_store < 1 / hz * 0.95:
            return
        self._last_store = now
        t = self.shadow.telem or {}
        self._telem_buf.append({
            "mission_id": self.active_mission_id, "drone_id": self.settings.drone_id,
            "t_utc": si["t_gcs"] / 1000, "t_drone_ms": si["t_ms"],
            "pos_n": si["pos"][0], "pos_e": si["pos"][1], "pos_d": si["pos"][2],
            "vel_n": si["vel"][0], "vel_e": si["vel"][1], "vel_d": si["vel"][2],
            "roll": si["att"][0], "pitch": si["att"][1], "yaw": si["att"][2],
            "fsm_state": self.shadow.fsm_state or 0, "flags": t.get("valid_flags_raw", 0),
            "wp_index": self.shadow.wp_index, "battery_pct": t.get("battery_pct") or 0})

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            buf, self._telem_buf = self._telem_buf, []
            if buf:
                try:
                    await asyncio.to_thread(self.db.insert_samples, buf)
                except Exception:
                    log.exception("ghi telemetry lỗi")

    async def _periodic(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(1.0)
            n += 1
            self._evaluate_commands()
            self.monitor.tick()
            if n % 10 == 0:
                st = self.link.status()
                self.db.add_link_stat(self.settings.drone_id, window_s=10, tx_count=st["gcs"]["tx_sent"],
                                      rx_count=st["gcs"]["rx_ok"], lost_count=st["gcs"]["rx_drop"],
                                      rtt_ms_avg=st["rtt_ms"], rssi_dbm_avg=(self.shadow.telem or {}).get("rssi_dbm"))

    # ── vòng đời nhiệm vụ theo shadow ─────────────────────────────────────────────────────

    def _mission_lifecycle(self, si: dict) -> None:
        wire_id, fsm = si["mission_id"], si["fsm_state"]
        if self.active_mission_id is None:
            # khôi phục sau khi GCS khởi động lại: shadow dựng lại từ telemetry, CSDL chỉ để đối chiếu
            for cand in self.db.list_missions(state="ready,running", limit=20):
                if wire_id and cand["id"] == wire_id:
                    self.active_mission_id = cand["id"]
                    self.db.add_event(mission_id=cand["id"], severity=0, category="mission",
                                      message="khôi phục theo dõi nhiệm vụ từ telemetry sau khi GCS khởi động lại")
                elif cand["state"] == "running":
                    self._finish(cand["id"], "failed", "failed",
                                 f"sau khi nối lại, drone báo mission_id {wire_id} — không còn chạy nhiệm vụ này")
        mid = self.active_mission_id
        if mid is None:
            return
        mission = self.db.get_mission(mid)
        if mission is None:
            return
        if wire_id != mid:
            if mission["state"] == "running":
                # shadow thắng kế hoạch cục bộ: drone không còn giữ nhiệm vụ này (ví dụ Pi khởi động lại)
                self._finish(mid, "failed", "failed",
                             f"shadow báo drone đang giữ mission_id {wire_id}, không phải {mid}")
            return
        state = mission["state"]
        if state == "ready" and fsm not in (IDLE, None):
            self.db.update_mission(mid, state="running", started_at=_now_utc())
            self._interrupted, self._last_wp_index = None, -1
            state = "running"
            self.hub.push("mission_update", {"id": mid, "state": "running"})
        if state != "running":
            return
        if si["wp_index"] > self._last_wp_index:
            for seq in range(max(0, self._last_wp_index), si["wp_index"]):
                self.db.mark_waypoint_reached(mid, seq)
            self._last_wp_index = si["wp_index"]
        # Chuyến bay kết thúc thế nào thì đọc ở flight_result, KHÔNG suy từ mission_state (8.5b).
        #
        # `MISSION_COMPLETE` là đích chung của cả huỷ lệnh, RTH, NAV_LAND, hết lượt thử VÀ hết kế
        # hoạch — đọc nó là "nhiệm vụ thành công" thì mọi lệnh huỷ đều được báo là thành công, và một
        # chuyến chưa bao giờ thấy tag (RETRIES_EXHAUSTED) trông y hệt chuyến trót lọt. Pi nêu đúng
        # chỗ này khi phân giải P30: thà biết là mình không biết, còn hơn tin chắc vào điều sai.
        #
        # `flight_result` được CHỐT LẠI tới khi cất cánh chuyến mới, nên GCS khởi động lại bao lâu sau
        # vẫn hỏi lại được (R5) — khác hẳn một trạng thái thoáng qua.
        if fsm in (RTH, FAILSAFE) and self._interrupted is None:
            self._interrupted = _fs_reason(si) or "chuyển RTH"
        elif fsm == LANDING and si["failsafe_type"] and self._interrupted is None:
            self._interrupted = _fs_reason(si)
        if fsm in (COMPLETE, IDLE):
            self._finish_by_result(mid, si)

    def _finish_by_result(self, mid: int, si: dict) -> None:
        """Kết thúc nhiệm vụ theo `flight_result` của Pi (8.5b). 0 = chưa biết -> chưa kết luận gì."""
        fr = si.get("flight_result")
        if fr is None:
            # Pi cũ chưa khai trường này, hoặc chưa chuyến nào kết thúc. Không đoán: để nhiệm vụ ở
            # "running" và chờ. Đoán bằng wp_index sẽ sai ngay sau khi GCS khởi động lại.
            return
        outcome = {
            mav.DRONE_RESULT_COMPLETED: ("done", "ok", None),
            mav.DRONE_RESULT_ABORTED: ("aborted", "aborted", "người vận hành huỷ nhiệm vụ"),
            mav.DRONE_RESULT_RTH: ("aborted", "aborted", "về nhà rồi hạ cánh"),
            mav.DRONE_RESULT_LANDED_CMD: ("aborted", "aborted", "hạ cánh tại chỗ theo lệnh"),
            mav.DRONE_RESULT_FAILSAFE: ("failed", "failed", self._interrupted or "sự cố trên drone"),
            # vẫn đi qua MISSION_COMPLETE nhưng CHƯA LÀM ĐƯỢC VIỆC: drone không thấy tag, hạ chỗ lạ
            mav.DRONE_RESULT_RETRIES_EXHAUSTED: ("failed", "failed",
                                                 "hết lượt thử tìm/hạ theo tag — hạ cánh tại chỗ"),
        }.get(fr)
        if outcome is None:
            return  # giá trị lạ của Pi mới hơn: bỏ qua im lặng (R1), đừng kết luận bừa
        if fr == mav.DRONE_RESULT_COMPLETED:
            for seq in range(max(0, self._last_wp_index), si["wp_total"]):
                self.db.mark_waypoint_reached(mid, seq)
        self._finish(mid, *outcome)

    def _finish(self, mid: int, state: str, result: str, reason: str | None) -> None:
        self.db.update_mission(mid, state=state, finished_at=_now_utc(), result=result, fail_reason=reason)
        self.db.add_event(mission_id=mid, severity=0 if state == "done" else 2, category="mission",
                          message=f"nhiệm vụ {mid} kết thúc: {state}" + (f" ({reason})" if reason else ""))
        self.hub.push("mission_update", {"id": mid, "state": state})
        self.active_mission_id, self._interrupted = None, None

    # ── lệnh ────────────────────────────────────────────────────────────────────────────────

    async def _send_tracked(self, c: TrackedCommand, command: int, *params: float) -> None:
        verifier = make_verifier(c.action, self.shadow)
        try:
            res = await self.commands.send(command, *params)
        except CommandTimeout as e:
            self.tracker.set_stage(c, "failed", str(e))
            self.db.add_event(mission_id=self.active_mission_id, severity=2, category="command",
                              message=f"lệnh {c.kind}/{c.action} không có ACK: {e}")
            return
        except asyncio.CancelledError:
            self.tracker.set_stage(c, "cancelled", "người vận hành huỷ")
            return
        self.tracker.acked(c, res.as_dict(), verifier)
        if c.stage == "rejected":
            self.db.add_event(mission_id=self.active_mission_id, severity=2, category="command",
                              message=f"lệnh {c.kind}/{c.action} bị từ chối: {c.detail}")

    async def cmd_emergency(self, action: str, user: dict) -> dict:
        """Bốn lệnh khẩn của mục 4.2 — phát lại vô hạn tới khi có ACK hoặc người vận hành huỷ."""
        table = {"RTH": (cmd.RTH, ()), "LAND": (cmd.LAND, ()),
                 "DISARM": (cmd.ARM_DISARM, (0.0, cmd.DISARM_FORCE)),
                 "ABORT_MISSION": (cmd.ABORT_MISSION, ())}
        if action not in table:
            raise ApiError(400, f"action không hợp lệ: {action}")
        if action == "DISARM" and user["role"] != "admin":
            raise ApiError(403, "DISARM giữa không trung chỉ dành cho admin")
        command, params = table[action]
        c = self.tracker.new("emergency", action, user["id"])
        self.db.audit(user["id"], "command.emergency", action, {"cmd_id": c.id})
        if self.active_mission_id and self._interrupted is None:
            self._interrupted = f"operator {action.lower()}"

        async def run() -> None:
            await self._send_tracked(c, command, *params)
            self.monitor.clear("EMERGENCY_UNACKED")
        task = asyncio.get_running_loop().create_task(run())  # không chặn REST — tiến trình đẩy qua WS
        self._cancelable[c.id] = task
        return c.as_dict()

    async def cmd_restart_stack(self, user: dict) -> dict:
        """Lệnh 42101: khởi động lại stack ROS trên Pi. Pi tự từ chối khi đang arm hoặc FSM không IDLE —
        GCS không kiểm thay, chỉ chuyển lý do (STATUSTEXT) cho người vận hành."""
        c = self.tracker.new("maintenance", "RESTART_STACK", user["id"])
        self.db.audit(user["id"], "command.restart_stack", None, {"cmd_id": c.id})
        self.db.add_event(mission_id=None, severity=1, category="command",
                          message="yêu cầu khởi động lại stack Pi — liên kết sẽ mất 20–40 s")
        task = asyncio.get_running_loop().create_task(self._send_tracked(c, cmd.RESTART_STACK))
        self._cancelable[c.id] = task
        return c.as_dict()

    def cancel_command(self, cmd_id: int, user: dict) -> bool:
        task = self._cancelable.pop(cmd_id, None)
        if task is None:
            return False
        task.cancel()
        self.db.audit(user["id"], "command.cancel", str(cmd_id))
        return True

    # ── nhiệm vụ ────────────────────────────────────────────────────────────────────────────

    def create_mission(self, body: dict, user: dict) -> dict:
        self._require_contract()
        cruise = body.get("cruise_alt_m") or self.th.takeoff_alt_m
        vel = min(body.get("max_vel_mps") or 1.0, 1.9)
        try:
            if body.get("stops"):
                wps = planner.plan_stops(self.tags, body["stops"], cruise_alt_m=cruise, max_vel_mps=vel,
                                         accept_radius_m=self.th.acceptance_radius_m)
                name = body.get("plan_name") or "Tuyến tùy biến"
            else:
                wps = planner.plan_pickup_dropoff(self.tags, int(body["pickup_tag"]), int(body["dropoff_tag"]),
                                                  cruise_alt_m=cruise, max_vel_mps=vel,
                                                  accept_radius_m=self.th.acceptance_radius_m)
                name = body.get("plan_name") or f"Lấy {body['pickup_tag']} → giao {body['dropoff_tag']}"
        except (planner.PlanError, KeyError, TypeError) as e:
            raise ApiError(400, f"không lập được kế hoạch: {e}")
        mission = self.db.create_mission(drone_id=self.settings.drone_id, user_id=user["id"], plan_name=name,
                                         pickup_tag=body.get("pickup_tag"), dropoff_tag=body.get("dropoff_tag"),
                                         map_crc=self.gcs_map_crc, cruise_alt_m=cruise, max_vel_mps=vel,
                                         priority=int(body.get("priority") or 100), waypoints=wps)
        self.db.audit(user["id"], "mission.create", str(mission["id"]), body)
        self.hub.push("mission_update", {"id": mission["id"], "state": mission["state"]})
        return mission | {"warnings": planner.plan_warnings(wps, self.areas, self.tags)}

    async def upload(self, mission_id: int, user: dict) -> dict:
        mission = self.db.get_mission(mission_id)
        if mission is None:
            raise ApiError(404, "không có nhiệm vụ")
        if mission["state"] not in ("queued", "ready"):
            raise ApiError(409, f"không tải lên được nhiệm vụ ở trạng thái {mission['state']}")
        if self.active_mission_id and self.active_mission_id != mission_id:
            active = self.db.get_mission(self.active_mission_id)
            if active and active["state"] == "running":
                raise ApiError(409, f"nhiệm vụ {self.active_mission_id} đang chạy")
            if active and active["state"] == "ready":
                self.db.update_mission(active["id"], state="queued")
        if mission["map_crc"] != self.gcs_map_crc:
            raise ApiError(409, "bản đồ tag đã đổi từ lúc lập kế hoạch — hãy lập lại nhiệm vụ")
        self._require_link()
        self._require_contract()
        self._require_map_sync()
        try:
            items = to_wire(mission["waypoints"], self.tags, mission["max_vel_mps"])
        except ValueError as e:
            raise ApiError(409, str(e))
        async with self._upload_lock:
            self.db.update_mission(mission_id, state="uploading", fail_reason=None)
            self.hub.push("mission_update", {"id": mission_id, "state": "uploading"})
            try:
                ack: MissionAck = await self.missions.upload(mission_id, items, plan_name=mission["plan_name"],
                                                             max_retries=int(self.th.max_retries))
            except (MissionCancelled, asyncio.TimeoutError) as e:
                self.db.update_mission(mission_id, state="queued", fail_reason=f"tải lên lỗi: {e}")
                self.hub.push("mission_update", {"id": mission_id, "state": "queued"})
                raise ApiError(502, f"tải lên không hoàn tất: {e}")
        if not ack.ok:
            # lý do từ chối do mission_manager_node sinh ra — chuyển nguyên văn, đừng diễn giải lại (3.3)
            self.db.update_mission(mission_id, state="queued", fail_reason=ack.reason)
            self.db.add_event(mission_id=mission_id, severity=2, category="mission",
                              message=f"drone từ chối kế hoạch: {ack.as_dict()['result_name']} — {ack.reason}")
            self.hub.push("mission_update", {"id": mission_id, "state": "queued"})
            raise ApiError(409, f"drone từ chối kế hoạch: {ack.reason or ack.as_dict()['result_name']}",
                           ack.as_dict())
        self.db.update_mission(mission_id, state="ready")
        self.active_mission_id = mission_id
        self.db.audit(user["id"], "mission.upload", str(mission_id), ack.as_dict())
        self.db.add_event(mission_id=mission_id, severity=0, category="mission",
                          message=f"đã nạp {len(items)} mục, drone nhận")
        self.hub.push("mission_update", {"id": mission_id, "state": "ready"})
        return self.db.get_mission(mission_id) | {"upload": ack.as_dict()}  # type: ignore[operator]

    def start_warnings(self) -> list[str]:
        """Cảnh báo trước MISSION_START — chỉ cảnh báo, KHÔNG chặn (11.P18)."""
        pad = next((t for t in self.tags if t["kind"] == "home" and t.get("enabled", True)), None)
        fast = self.shadow.fast
        if fast is None or (self.shadow.age() or 99) > 3.0:
            # nhánh THƯỜNG GẶP: drone đậu trên pad không thấy tag của chính nó nên POS_VALID = 0 (5.2b)
            return ["chưa có vị trí drone (POS_VALID = 0) — không kiểm được drone có đứng trên bãi đáp không"]
        if pad is None:
            return []
        d = ((fast["pos"][0] - pad["pos_n_m"]) ** 2 + (fast["pos"][1] - pad["pos_e_m"]) ** 2) ** 0.5
        if d > (pad.get("landing_tol_m") or PAD_HOME_TOL_M):
            return [f"drone đang cách bãi đáp {d:.1f} m — RTH sẽ về ĐIỂM CẤT CÁNH, không về bãi đáp"]
        return []

    async def mission_ctrl(self, mission_id: int, action: str, user: dict) -> dict:
        mission = self.db.get_mission(mission_id)
        if mission is None:
            raise ApiError(404, "không có nhiệm vụ")
        action = action.upper()
        if action not in ("START", "ABORT"):
            raise ApiError(400, f"action không hợp lệ: {action}")
        allowed = {"START": ("ready",), "ABORT": ("ready", "running")}
        if mission["state"] not in allowed[action]:
            if action == "ABORT" and mission["state"] == "queued":
                self.db.update_mission(mission_id, state="aborted", finished_at=_now_utc(), result="aborted",
                                       fail_reason="hủy trước khi tải lên")
                self.hub.push("mission_update", {"id": mission_id, "state": "aborted"})
                return {"id": mission_id, "state": "aborted"}
            raise ApiError(409, f"{action} không hợp lệ ở trạng thái {mission['state']}")
        self._require_link()
        if action == "START":
            self._require_contract()
            self._require_map_sync()
            warnings = self.start_warnings()
            c = self.tracker.new("mission", "START", user["id"])
            self.db.audit(user["id"], "mission.start", str(mission_id), {"cmd_id": c.id, "warnings": warnings})
            await self._send_tracked(c, cmd.MISSION_START)
            return c.as_dict() | {"warnings": warnings}
        self._interrupted = "operator abort"
        c = self.tracker.new("mission", "ABORT_MISSION", user["id"])
        self.db.audit(user["id"], "mission.abort", str(mission_id), {"cmd_id": c.id})
        await self._send_tracked(c, cmd.ABORT_MISSION)
        if mission["state"] == "ready" and c.stage in ("acked", "effective"):
            self._finish(mission_id, "aborted", "aborted", "operator abort trước khi cất cánh")
        return c.as_dict()

    # ── bản đồ tag / thiết kế ──────────────────────────────────────────────────────────────

    def validate(self, design: dict | None = None) -> list[dict]:
        return validate_design(design or self.db.get_design(self.site_id))  # type: ignore[arg-type]

    def save_design(self, site_id: int, design: dict, ack_warnings: bool, user: dict) -> dict:
        issues = validate_design(design)
        if has_errors(issues):
            raise ApiError(422, "thiết kế còn Lỗi — không lưu được", issues)
        if has_warnings(issues) and not ack_warnings:
            raise ApiError(409, "thiết kế còn Cảnh báo — cần xác nhận 'tôi đã hiểu'", issues)
        saved = self.db.save_design(site_id, design)
        self.db.audit(user["id"], "site.save_design", str(site_id),
                      {"warnings_acknowledged": [i["code"] for i in issues if i["level"] == "warning"],
                       "tags": len(design["tags"]), "areas": len(design["areas"])})
        if site_id == self.site_id:
            self.reload_site()
        self.hub.push("site_changed", {"site_id": site_id, "map": self.map_status()})
        return saved | {"issues": issues, "map": self.map_status()}

    def tags_changed(self) -> None:
        self.reload_site()
        self.hub.push("site_changed", {"site_id": self.site_id, "map": self.map_status()})

    def export_tags_yaml(self, user: dict) -> str:
        """Bản đồ tag đi sang Pi bằng file, không qua dây (8.6)."""
        issues = self.validate()
        if has_errors(issues):
            raise ApiError(422, "thiết kế còn Lỗi — không xuất bản đồ", issues)
        self.db.audit(user["id"], "tags.export", str(self.site_id), {"map_crc": self.gcs_map_crc})
        return tags_yaml(self.tags, self.origin)

    async def upload_tagmap(self, user: dict) -> dict:
        """Nạp bản đồ tag qua dây, thay cho chép `tags.yaml` bằng tay (giao ước 8.7, P31).

        `ACCEPTED` KHÔNG nghĩa là đã có hiệu lực: Pi cần khởi động lại stack. `map_status()` chỉ
        đổi `in_sync` thành True SAU KHI đó — đây là cách duy nhất biết bản đồ đã thật sự lên (11.6).
        """
        issues = self.validate()
        if has_errors(issues):
            raise ApiError(422, "thiết kế còn Lỗi — không nạp bản đồ", issues)
        self._require_link()
        self._require_contract()
        async with self._upload_lock:
            try:
                ack: TagmapAck = await self.tagmap.upload(self.tags, self.origin)
            except ValueError as e:
                raise ApiError(422, str(e))
            except (TagmapCancelled, asyncio.TimeoutError) as e:
                raise ApiError(502, f"nạp bản đồ không hoàn tất: {e}")
        if not ack.ok:
            self.db.add_event(mission_id=None, severity=2, category="mission",
                              message=f"drone từ chối bản đồ tag: {ack.as_dict()['result_name']} — {ack.reason}")
            raise ApiError(409, f"drone từ chối bản đồ tag: {ack.reason or ack.as_dict()['result_name']}",
                           ack.as_dict())
        self.db.audit(user["id"], "tags.upload_wire", str(self.site_id), ack.as_dict())
        self.db.add_event(mission_id=None, severity=0, category="mission",
                          message="đã gửi bản đồ tag qua dây — cần khởi động lại stack bên Pi để có hiệu lực")
        self.hub.push("map", self.map_status())
        return {"upload": ack.as_dict(), "map": self.map_status()}

    def teach_tag(self, tag_pk: int, user: dict) -> dict:
        if not self.shadow.fast or (self.shadow.age() or 99) > 1.0:
            raise ApiError(409, "chưa có telemetry tươi để ghi vị trí")
        n, e, d = self.shadow.fast["pos"]
        fields = {"pos_n_m": round(n, 3), "pos_e_m": round(e, 3)}
        if abs(d) < 0.3:  # drone đang đỗ trên tag → lấy luôn cao độ bãi
            fields["pos_d_m"] = round(d, 3)
        tag = self.db.update_tag(tag_pk, fields)
        if tag is None:
            raise ApiError(404, "không có tag")
        self.db.audit(user["id"], "tags.teach", str(tag_pk), fields)
        self.tags_changed()
        return tag


def _fs_reason(si: dict) -> str | None:
    from .mission.shadow import _FS_NAME
    fs = si["failsafe_type"]
    return f"failsafe {_FS_NAME.get(fs, fs)}" if fs else None
