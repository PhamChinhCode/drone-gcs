"""Cấu hình backend — nạp từ biến môi trường GCS_* hoặc file .env (mục 7.1)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GCS_", env_file=".env", extra="ignore")

    # tcp://127.0.0.1:5760 = fake_drone; serial://COM5?baud=921600 = dongle ESP32-GCS thật
    link_url: str = "tcp://127.0.0.1:5760"
    # khóa phiên HMAC (hex 32 ký tự). Bắt tay sinh khóa mỗi phiên chưa có trong đặc tả — xem DIEM_CAN_CHOT.md
    session_key: str = "00112233445566778899aabbccddeeff"
    db_url: str = "sqlite:///./gcs.db"
    data_dir: str = "./data_store"
    jwt_secret: str = "doi-khoa-nay-khi-trien-khai-that-0123456789"  # ≥ 32 byte cho HS256
    jwt_ttl_min: int = 12 * 60
    admin_user: str = "admin"
    admin_password: str = "admin"
    operator_user: str = "operator"
    operator_password: str = "operator"
    drone_id: int = 1
    drone_peer_mac: str = "00:00:00:00:00:00"
    site_id: int = 1
    ws_batch_ms: int = 100
    telemetry_store_hz: float = 10.0
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def session_key_bytes(self) -> bytes:
        return bytes.fromhex(self.session_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
