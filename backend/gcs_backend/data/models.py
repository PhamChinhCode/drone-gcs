"""Mô hình SQLAlchemy — bám lược đồ mục 7.4.

Sai khác có chủ đích (ghi trong docs/KIEN_TRUC_TRIEN_KHAI.md):
- site thêm gcs_pos_n_m/gcs_pos_e_m: vị trí trạm mặt đất để vẽ vòng bán kính liên kết (8.4.3) và kiểm W_BEYOND_LINK.
- mission thêm cruise_alt_m, max_vel_mps. mission_id trên dây = mission.id & 0xFFFF.
- t_utc của telemetry_sample/mission_event/link_stat lưu epoch giây (REAL) thay vì TIMESTAMP: gọn, tua nhanh.
- Chưa dùng alembic: bảng tạo bằng create_all ở giai đoạn nghiên cứu.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (CheckConstraint, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "site"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    origin_lat: Mapped[float | None] = mapped_column(Float)
    origin_lon: Mapped[float | None] = mapped_column(Float)
    origin_alt_m: Mapped[float | None] = mapped_column(Float)
    yaw_offset_deg: Mapped[float] = mapped_column(Float, default=0.0)
    design_radius_m: Mapped[float | None] = mapped_column(Float)
    link_radius_meas_m: Mapped[float | None] = mapped_column(Float)
    cruise_alt_default_m: Mapped[float] = mapped_column(Float, default=5.0)
    gcs_pos_n_m: Mapped[float] = mapped_column(Float, default=0.0)
    gcs_pos_e_m: Mapped[float] = mapped_column(Float, default=0.0)
    bg_image_path: Mapped[str | None] = mapped_column(Text)
    bg_anchor_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SiteArea(Base):
    __tablename__ = "site_area"
    __table_args__ = (CheckConstraint("kind IN ('operating','no_fly')"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    min_alt_m: Mapped[float] = mapped_column(Float, default=0.0)
    max_alt_m: Mapped[float] = mapped_column(Float, default=20.0)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(Text)
    vertices: Mapped[list["SiteAreaVertex"]] = relationship(
        order_by="SiteAreaVertex.seq", cascade="all, delete-orphan", lazy="selectin")


class SiteAreaVertex(Base):
    __tablename__ = "site_area_vertex"
    __table_args__ = (UniqueConstraint("area_id", "seq"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("site_area.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    pos_n_m: Mapped[float] = mapped_column(Float)
    pos_e_m: Mapped[float] = mapped_column(Float)


class TagPoint(Base):
    __tablename__ = "tag_point"
    __table_args__ = (UniqueConstraint("site_id", "tag_id"),
                      CheckConstraint("kind IN ('home','pickup','dropoff','waypoint')"))
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id"))
    tag_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    pos_n_m: Mapped[float] = mapped_column(Float)
    pos_e_m: Mapped[float] = mapped_column(Float)
    pos_d_m: Mapped[float] = mapped_column(Float, default=0.0)
    yaw_deg: Mapped[float] = mapped_column(Float, default=0.0)
    # 1 = yaw_deg là hướng ĐÃ ĐO của tag (giao ước 0.8, 8.7): Pi quay mũi theo nó khi hạ cánh và suy vị
    # trí tag nhỏ từ nó. 0 = chỉ để vẽ, không gửi — mặc định 0 vì yaw_deg = 0 cũ chưa ai đo.
    yaw_valid: Mapped[int] = mapped_column(Integer, default=0)
    tag_size_m: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(Text)
    landing_tol_m: Mapped[float] = mapped_column(Float, default=0.3)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(Text)


class Drone(Base):
    __tablename__ = "drone"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    esp_peer_mac: Mapped[str] = mapped_column(Text, unique=True)
    model: Mapped[str | None] = mapped_column(Text)
    max_payload_g: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (CheckConstraint("role IN ('operator','admin')"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Mission(Base):
    __tablename__ = "mission"
    id: Mapped[int] = mapped_column(primary_key=True)
    drone_id: Mapped[int] = mapped_column(ForeignKey("drone.id"))
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    plan_name: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)  # queued|uploading|ready|running|done|failed|aborted
    pickup_tag: Mapped[int | None] = mapped_column(Integer)
    dropoff_tag: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    map_crc: Mapped[int] = mapped_column(Integer)
    cruise_alt_m: Mapped[float] = mapped_column(Float, default=5.0)
    max_vel_mps: Mapped[float] = mapped_column(Float, default=3.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)
    fail_reason: Mapped[str | None] = mapped_column(Text)
    waypoints: Mapped[list["MissionWaypoint"]] = relationship(
        order_by="MissionWaypoint.seq", cascade="all, delete-orphan", lazy="selectin")


class MissionWaypoint(Base):
    __tablename__ = "mission_waypoint"
    __table_args__ = (UniqueConstraint("mission_id", "seq"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("mission.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    tag_id: Mapped[int | None] = mapped_column(Integer)
    pos_n_m: Mapped[float] = mapped_column(Float)
    pos_e_m: Mapped[float] = mapped_column(Float)
    pos_d_m: Mapped[float] = mapped_column(Float)
    yaw_deg: Mapped[float | None] = mapped_column(Float)
    action: Mapped[str] = mapped_column(Text, default="none")
    accept_radius_m: Mapped[float | None] = mapped_column(Float)
    max_vel_mps: Mapped[float | None] = mapped_column(Float)
    loiter_s: Mapped[float | None] = mapped_column(Float)
    require_tag_lock: Mapped[int] = mapped_column(Integer, default=0)
    precision_land: Mapped[int] = mapped_column(Integer, default=0)
    reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelemetrySample(Base):
    __tablename__ = "telemetry_sample"
    __table_args__ = (Index("idx_telem_mission_t", "mission_id", "t_utc"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int | None] = mapped_column(ForeignKey("mission.id", ondelete="CASCADE"))
    drone_id: Mapped[int] = mapped_column(Integer)
    t_utc: Mapped[float] = mapped_column(Float)  # epoch giây (float) — gọn và nhanh cho phát lại
    t_drone_ms: Mapped[int] = mapped_column(Integer)
    pos_n: Mapped[float] = mapped_column(Float)
    pos_e: Mapped[float] = mapped_column(Float)
    pos_d: Mapped[float] = mapped_column(Float)
    vel_n: Mapped[float] = mapped_column(Float)
    vel_e: Mapped[float] = mapped_column(Float)
    vel_d: Mapped[float] = mapped_column(Float)
    roll: Mapped[float] = mapped_column(Float)
    pitch: Mapped[float] = mapped_column(Float)
    yaw: Mapped[float] = mapped_column(Float)
    fsm_state: Mapped[int] = mapped_column(Integer)
    flags: Mapped[int] = mapped_column(Integer)
    wp_index: Mapped[int] = mapped_column(Integer)
    battery_pct: Mapped[int] = mapped_column(Integer)


class MissionEvent(Base):
    __tablename__ = "mission_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int | None] = mapped_column(ForeignKey("mission.id", ondelete="CASCADE"))
    t_utc: Mapped[float] = mapped_column(Float)
    t_drone_ms: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[int] = mapped_column(Integer)  # 0 info, 1 warn, 2 error, 3 critical
    category: Mapped[str] = mapped_column(Text)     # failsafe|command|link|gripper|marker|user|mission|safety
    code: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text)


class MissionPhoto(Base):
    __tablename__ = "mission_photo"
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int | None] = mapped_column(ForeignKey("mission.id", ondelete="CASCADE"))
    waypoint_seq: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str | None] = mapped_column(Text)
    t_utc: Mapped[float] = mapped_column(Float)
    file_path: Mapped[str] = mapped_column(Text)
    thumb_path: Mapped[str | None] = mapped_column(Text)


class LinkStatRow(Base):
    __tablename__ = "link_stat"
    id: Mapped[int] = mapped_column(primary_key=True)
    drone_id: Mapped[int] = mapped_column(Integer)
    t_utc: Mapped[float] = mapped_column(Float)
    window_s: Mapped[int] = mapped_column(Integer)
    tx_count: Mapped[int | None] = mapped_column(Integer)
    rx_count: Mapped[int | None] = mapped_column(Integer)
    lost_count: Mapped[int | None] = mapped_column(Integer)
    rtt_ms_avg: Mapped[float | None] = mapped_column(Float)
    rssi_dbm_avg: Mapped[float | None] = mapped_column(Float)


class SystemConfig(Base):
    __tablename__ = "system_config"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    drone_value: Mapped[str | None] = mapped_column(Text)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_sync: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    t_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    detail_json: Mapped[str | None] = mapped_column(Text)
