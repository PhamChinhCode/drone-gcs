"""REST API (mục 7.6). Mọi hành động lệnh / sửa cấu hình ghi audit_log kèm user_id (10.2)."""
from __future__ import annotations

import base64
import json
import math
import os
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..mission.planner import plan_warnings
from ..runtime import Runtime
from .auth import current_user, decode_token, issue_token, require_admin

router = APIRouter(prefix="/api")


def rt(request: Request) -> Runtime:
    return request.app.state.rt


# ─────────────────────────── mô hình đầu vào ──────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str


class TagBody(BaseModel):
    tag_id: int = Field(ge=0, le=32767)
    label: str
    pos_n_m: float
    pos_e_m: float
    pos_d_m: float = 0.0
    yaw_deg: float = 0.0
    tag_size_m: float = Field(gt=0)
    kind: Literal["home", "pickup", "dropoff", "waypoint"]
    landing_tol_m: float = 0.3
    enabled: bool = True
    notes: str | None = None
    site_id: int | None = None


class TagPatch(BaseModel):
    tag_id: int | None = Field(default=None, ge=0, le=32767)
    label: str | None = None
    pos_n_m: float | None = None
    pos_e_m: float | None = None
    pos_d_m: float | None = None
    yaw_deg: float | None = None
    tag_size_m: float | None = Field(default=None, gt=0)
    kind: Literal["home", "pickup", "dropoff", "waypoint"] | None = None
    landing_tol_m: float | None = None
    enabled: bool | None = None
    notes: str | None = None


class AreaBody(BaseModel):
    kind: Literal["operating", "no_fly"]
    name: str = ""
    min_alt_m: float = 0.0
    max_alt_m: float = 20.0
    enabled: bool = True
    notes: str | None = None
    vertices: list[tuple[float, float]]


class SiteBody(BaseModel):
    name: str
    origin_lat: float | None = None
    origin_lon: float | None = None
    origin_alt_m: float | None = None
    yaw_offset_deg: float = 0.0
    design_radius_m: float | None = None
    link_radius_meas_m: float | None = None
    cruise_alt_default_m: float = 5.0
    gcs_pos_n_m: float = 0.0
    gcs_pos_e_m: float = 0.0
    bg_anchors: list[dict] | None = None


class DesignBody(BaseModel):
    site: SiteBody
    tags: list[TagBody]
    areas: list[AreaBody]
    ack_warnings: bool = False


class DraftBody(BaseModel):
    site: dict = {}
    tags: list[dict] = []
    areas: list[dict] = []


class BgImageBody(BaseModel):
    data_url: str
    anchors: list[dict] | None = None


class MissionBody(BaseModel):
    pickup_tag: int | None = None
    dropoff_tag: int | None = None
    stops: list[dict] | None = None
    cruise_alt_m: float | None = Field(default=None, gt=0, le=120)
    max_vel_mps: float | None = Field(default=None, gt=0, le=15)
    priority: int = 100
    plan_name: str | None = None


class PriorityBody(BaseModel):
    priority: int


class EmergencyBody(BaseModel):
    """Bốn lệnh khẩn của giao ước 4.2. ARM, TAKEOFF, GOTO, HOLD không thuộc kênh này (9.3)."""
    action: Literal["RTH", "LAND", "DISARM", "ABORT_MISSION"]


class ConfigBody(BaseModel):
    values: dict[str, float]


class UserBody(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=6)
    role: Literal["operator", "admin"]


# ─────────────────────────── auth / trạng thái ────────────────────────────────────────────────

@router.post("/auth/login")
async def login(body: LoginBody, r: Runtime = Depends(rt)):
    user = r.db.authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(401, "sai tên đăng nhập hoặc mật khẩu")
    r.db.audit(user["id"], "auth.login")
    return {"token": issue_token(user, r.settings.jwt_secret, r.settings.jwt_ttl_min), "user": user}


@router.get("/auth/me")
async def me(user: dict = Depends(current_user)):
    return user


@router.get("/state")
async def state(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.snapshot()


# ─────────────────────────── khu vực, tag, thiết kế ───────────────────────────────────────────

@router.get("/sites")
async def sites(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.db.list_sites()


@router.get("/sites/{site_id}/tags")
async def site_tags(site_id: int, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return {"tags": r.db.list_tags(site_id), "map": r.map_status() if site_id == r.site_id else None}


@router.post("/tags")
async def create_tag(body: TagBody, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    site_id = body.site_id or r.site_id
    if any(t["tag_id"] == body.tag_id for t in r.db.list_tags(site_id)):
        raise HTTPException(409, f"tag_id {body.tag_id} đã tồn tại")
    tag = r.db.create_tag(site_id, body.model_dump(exclude={"site_id"}))
    r.db.audit(user["id"], "tags.create", str(tag["id"]), body.model_dump())
    r.tags_changed()
    return {"tag": tag, "map": r.map_status()}


@router.put("/tags/{pk}")
async def update_tag(pk: int, body: TagPatch, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    data = body.model_dump(exclude_unset=True)
    cur = r.db.get_tag(pk)
    if cur is None:
        raise HTTPException(404, "không có tag")
    if "tag_id" in data and any(t["tag_id"] == data["tag_id"] and t["id"] != pk for t in r.db.list_tags(cur["site_id"])):
        raise HTTPException(409, f"tag_id {data['tag_id']} đã tồn tại")
    tag = r.db.update_tag(pk, data)
    r.db.audit(user["id"], "tags.update", str(pk), data)
    r.tags_changed()
    return {"tag": tag, "map": r.map_status()}


@router.delete("/tags/{pk}")
async def delete_tag(pk: int, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    if not r.db.delete_tag(pk):
        raise HTTPException(404, "không có tag")
    r.db.audit(user["id"], "tags.delete", str(pk))
    r.tags_changed()
    return {"map": r.map_status()}


@router.get("/tags/export")
async def export_tags(r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    """tags.yaml để chép sang Pi. Bản đồ tag KHÔNG nạp qua dây; trên dây chỉ so tagmap_crc (8.6)."""
    return Response(r.export_tags_yaml(user), media_type="application/x-yaml",
                    headers={"Content-Disposition": 'attachment; filename="tags.yaml"'})


@router.post("/tags/upload")
async def upload_tags(r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    """Nạp bản đồ tag qua dây (giao ước 8.7, P31) — thay cho `/tags/export` + chép tay."""
    return await r.upload_tagmap(user)


@router.post("/tags/teach/{pk}")
async def teach_tag(pk: int, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    return {"tag": r.teach_tag(pk, user), "map": r.map_status()}


@router.get("/sites/{site_id}/design")
async def get_design(site_id: int, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    d = r.db.get_design(site_id)
    if d is None:
        raise HTTPException(404, "không có khu vực")
    return d | {"issues": r.validate(d), "map": r.map_status() if site_id == r.site_id else None}


@router.put("/sites/{site_id}/design")
async def put_design(site_id: int, body: DesignBody, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    design = {"site": body.site.model_dump(), "tags": [t.model_dump(exclude={"site_id"}) for t in body.tags],
              "areas": [a.model_dump() for a in body.areas]}
    return r.save_design(site_id, design, body.ack_warnings, user)


@router.get("/sites/{site_id}/areas")
async def get_areas(site_id: int, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.db.list_areas(site_id)


@router.put("/sites/{site_id}/areas")
async def put_areas(site_id: int, body: list[AreaBody], r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    design = r.db.get_design(site_id)
    if design is None:
        raise HTTPException(404, "không có khu vực")
    design["areas"] = [a.model_dump() for a in body]
    return r.save_design(site_id, design, True, user)


@router.post("/sites/{site_id}/validate")
async def validate(site_id: int, body: DraftBody | None = None, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    draft = body.model_dump() if body and (body.tags or body.areas or body.site) else r.db.get_design(site_id)
    return {"issues": r.validate(draft)}


@router.post("/sites/{site_id}/bg-image")
async def upload_bg(site_id: int, body: BgImageBody, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    mt = re.match(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", body.data_url, re.S)
    if not mt:
        raise HTTPException(400, "data_url phải là ảnh png/jpeg/webp base64")
    raw = base64.b64decode(mt.group(2))
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "ảnh quá 15 MB")
    os.makedirs(r.settings.data_dir, exist_ok=True)
    path = os.path.join(r.settings.data_dir, f"site_{site_id}_bg.{mt.group(1)}")
    with open(path, "wb") as f:
        f.write(raw)
    r.db.set_site_fields(site_id, bg_image_path=path,
                         bg_anchor_json=json.dumps(body.anchors) if body.anchors else None)
    r.db.audit(user["id"], "site.bg_image", str(site_id), {"anchors": body.anchors})
    return r.db.get_site(site_id)


@router.get("/sites/{site_id}/bg-image")
async def get_bg(site_id: int, token: str = Query(...), r: Runtime = Depends(rt)):
    decode_token(token, r.settings.jwt_secret)
    site = r.db.get_site(site_id)
    if not site or not site["bg_image_path"] or not os.path.exists(site["bg_image_path"]):
        raise HTTPException(404, "chưa có ảnh nền")
    return FileResponse(site["bg_image_path"])


# ─────────────────────────── nhiệm vụ ─────────────────────────────────────────────────────────

@router.post("/missions")
async def create_mission(body: MissionBody, r: Runtime = Depends(rt), user: dict = Depends(current_user)):
    if not body.stops and (body.pickup_tag is None or body.dropoff_tag is None):
        raise HTTPException(400, "cần (pickup_tag, dropoff_tag) hoặc stops")
    return r.create_mission(body.model_dump(), user)


@router.get("/missions")
async def list_missions(state: str | None = None, tag: int | None = None, from_: str | None = Query(None, alias="from"),
                  to: str | None = None, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.db.list_missions(state=state, tag=tag, t_from=from_, t_to=to)


@router.get("/missions/queue")
async def mission_queue(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.db.queue(r.settings.drone_id)


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: int, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return _mission_or_404(r, mission_id)


def _mission_or_404(r: Runtime, mission_id: int) -> dict:
    mi = r.db.get_mission(mission_id)
    if mi is None:
        raise HTTPException(404, "không có nhiệm vụ")
    return mi


@router.get("/missions/{mission_id}/preview")
async def preview(mission_id: int, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    mi = _mission_or_404(r, mission_id)
    return {"waypoints": mi["waypoints"], "warnings": plan_warnings(mi["waypoints"], r.areas)}


@router.put("/missions/{mission_id}/priority")
async def set_priority(mission_id: int, body: PriorityBody, r: Runtime = Depends(rt), user: dict = Depends(current_user)):
    mi = r.db.update_mission(mission_id, priority=body.priority)
    if mi is None:
        raise HTTPException(404, "không có nhiệm vụ")
    r.db.audit(user["id"], "mission.priority", str(mission_id), body.model_dump())
    r.hub.push("mission_update", {"id": mission_id, "state": mi["state"]})
    return mi


@router.post("/missions/{mission_id}/upload")
async def upload(mission_id: int, r: Runtime = Depends(rt), user: dict = Depends(current_user)):
    return await r.upload(mission_id, user)


@router.post("/missions/{mission_id}/{action}")
async def mission_action(mission_id: int, action: Literal["start", "pause", "resume", "abort"],
                         r: Runtime = Depends(rt), user: dict = Depends(current_user)):
    return await r.mission_ctrl(mission_id, action, user)


@router.get("/missions/{mission_id}/telemetry")
async def mission_telemetry(mission_id: int, rate: float | None = None, r: Runtime = Depends(rt),
                      _: dict = Depends(current_user)):
    return {"samples": r.db.get_telemetry(mission_id, rate), "events": r.db.list_events(mission_id)}


@router.get("/missions/{mission_id}/events")
async def mission_events(mission_id: int, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.db.list_events(mission_id)


@router.get("/missions/{mission_id}/photos")
async def mission_photos(mission_id: int, _: dict = Depends(current_user)):
    return []  # ảnh đi kênh WiFi 5 GHz — bổ sung ở giai đoạn sau (quyết định số 2)


@router.get("/missions/{mission_id}/report")
async def mission_report(mission_id: int, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    """Báo cáo JSON. Bản PDF (report.pdf) chưa làm — xem docs/KIEN_TRUC_TRIEN_KHAI.md."""
    mi = _mission_or_404(r, mission_id)
    samples = r.db.get_telemetry(mission_id)
    events = r.db.list_events(mission_id)
    dist = sum(math.dist((a["pos_n"], a["pos_e"], a["pos_d"]), (b["pos_n"], b["pos_e"], b["pos_d"]))
               for a, b in zip(samples, samples[1:]))
    return {
        "mission": mi,
        "duration_s": round(samples[-1]["t_utc"] - samples[0]["t_utc"], 1) if len(samples) > 1 else 0,
        "distance_m": round(dist, 1),
        "max_alt_m": round(max((-s["pos_d"] for s in samples), default=0), 2),
        "battery_start_pct": samples[0]["battery_pct"] if samples else None,
        "battery_end_pct": samples[-1]["battery_pct"] if samples else None,
        "waypoints_reached": sum(1 for w in mi["waypoints"] if w["reached_at"]),
        "events_by_severity": {s: sum(1 for e in events if e["severity"] == s) for s in range(4)},
        "events": events,
    }


# ─────────────────────────── lệnh ─────────────────────────────────────────────────────────────

@router.post("/commands/emergency")
async def cmd_emergency(body: EmergencyBody, r: Runtime = Depends(rt), user: dict = Depends(current_user)):
    return await r.cmd_emergency(body.action, user)


@router.post("/commands/{cmd_id}/cancel")
async def cmd_cancel(cmd_id: int, r: Runtime = Depends(rt), user: dict = Depends(current_user)):
    if not r.cancel_command(cmd_id, user):
        raise HTTPException(404, "không có lệnh")
    return {"ok": True}


@router.get("/commands")
async def commands(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.tracker.recent()


# ─────────────────────────── liên kết / cấu hình / cảnh báo / người dùng ─────────────────────

@router.get("/link/status")
async def link_status(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.link.status() | {"render_delay_ms": 150}


@router.get("/link/history")
async def link_history(since_s: float = 3600, r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.db.link_stats(r.settings.drone_id, since_s)




@router.get("/config")
async def get_config(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.db.get_config()


@router.put("/config")
async def put_config(body: ConfigBody, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    known = {c["key"] for c in r.db.get_config()}
    unknown = set(body.values) - known
    if unknown:
        raise HTTPException(400, f"khóa không hợp lệ: {', '.join(sorted(unknown))}")
    for k, v in body.values.items():
        r.db.set_config(k, f"{v:g}", user["id"])
    r.db.audit(user["id"], "config.update", None, body.model_dump())
    r.reload_thresholds()
    # Không đẩy xuống drone: PARAM_SET bị Pi bỏ qua, ngưỡng chỉ sửa ở safety.yaml (9.4). Bảng này là
    # gương của safety.yaml để ĐỐI CHIẾU, không phải nguồn.
    r.monitor.set_drift(r.config_drift())
    return r.db.get_config()


@router.get("/config/drift")
async def config_drift(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return {"drift": r.config_drift(), "config": r.db.get_config()}


@router.post("/config/check")
async def config_check(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    """Đọc lại 8 tham số của Pi (PARAM_REQUEST_LIST) rồi so với bảng system_config."""
    await r._refresh_params()
    return {"drift": r.config_drift()}


@router.get("/alerts")
async def alerts(r: Runtime = Depends(rt), _: dict = Depends(current_user)):
    return r.monitor.snapshot() | {"events": r.db.list_events(limit=300)}


@router.post("/alerts/{code}/ack")
async def ack_alert(code: str, r: Runtime = Depends(rt), user: dict = Depends(current_user)):
    if not r.monitor.ack(code):
        raise HTTPException(404, "không có cảnh báo đang hoạt động")
    r.db.audit(user["id"], "alert.ack", code)
    return {"ok": True}


@router.get("/users")
async def users(r: Runtime = Depends(rt), _: dict = Depends(require_admin)):
    return r.db.list_users()


@router.post("/users")
async def create_user(body: UserBody, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    if any(u["username"] == body.username for u in r.db.list_users()):
        raise HTTPException(409, "tên đăng nhập đã tồn tại")
    u = r.db.create_user(body.username, body.password, body.role)
    r.db.audit(user["id"], "users.create", body.username, {"role": body.role})
    return u


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, r: Runtime = Depends(rt), user: dict = Depends(require_admin)):
    if user_id == user["id"]:
        raise HTTPException(400, "không tự xóa chính mình")
    if not r.db.delete_user(user_id):
        raise HTTPException(404, "không có người dùng")
    r.db.audit(user["id"], "users.delete", str(user_id))
    return {"ok": True}


@router.get("/audit")
async def audit(r: Runtime = Depends(rt), _: dict = Depends(require_admin)):
    return r.db.list_audit()


# ─────────────────────────── WebSocket ────────────────────────────────────────────────────────

ws_router = APIRouter()


@ws_router.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket, token: str = Query(...)):
    r: Runtime = ws.app.state.rt
    try:
        decode_token(token, r.settings.jwt_secret)
    except HTTPException:
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        await r.hub.serve(ws, r.snapshot())
    except WebSocketDisconnect:
        pass
