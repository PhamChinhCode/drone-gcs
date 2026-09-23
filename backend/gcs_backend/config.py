"""Cấu hình backend — nạp từ biến môi trường GCS_* hoặc file .env (mục 7.1)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GCS_", env_file=".env", extra="ignore")

    # GCS nghe UDP ở đây; Pi gọi ra trước và GCS trả về địa chỉ nguồn của gói hợp lệ gần nhất (2.1)
    mav_host: str = "0.0.0.0"
    mav_port: int = 14550
    # Chữ ký MAVLink 2 (7.6): MẶC ĐỊNH BẬT. Khoá 32 byte nằm ở file ngoài repo, quyền 600 —
    # không bao giờ commit. Chỉ tắt khi Pi và GCS cùng một mạng kín, và phải tắt có chủ ý.
    signing: bool = True
    signing_key_file: str = "./gcs_signing.key"
    db_url: str = "sqlite:///./gcs.db"
    data_dir: str = "./data_store"
    jwt_secret: str = "doi-khoa-nay-khi-trien-khai-that-0123456789"  # ≥ 32 byte cho HS256
    jwt_ttl_min: int = 12 * 60
    admin_user: str = "admin"
    admin_password: str = "admin"
    operator_user: str = "operator"
    operator_password: str = "operator"
    drone_id: int = 1
    site_id: int = 1
    ws_batch_ms: int = 100
    telemetry_store_hz: float = 10.0
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def signing_key_bytes(self) -> bytes | None:
        """None = chạy không chữ ký. Bật mà thiếu khoá thì DỪNG — im lặng bay với kênh mở là tệ hơn."""
        if not self.signing:
            return None
        path = Path(self.signing_key_file)
        if not path.exists():
            raise RuntimeError(
                f"chữ ký gói đang BẬT nhưng không có khoá ở {path} (giao ước 7.6). Sinh 32 byte ngẫu "
                "nhiên vào file đó rồi chép ĐÚNG khoá ấy sang Pi, quyền 600, không commit. "
                "Mạng kín thì đặt GCS_SIGNING=false — và phải là quyết định có chủ ý.")
        raw = path.read_bytes()
        if len(raw) == 32:
            # Định dạng chuẩn: 32 byte nhị phân, đúng thứ `tools/tao_khoa_gcs.py` phía Pi sinh ra.
            # KHÔNG strip() ở đây: khoá ngẫu nhiên hoàn toàn có thể bắt đầu hoặc kết thúc bằng 0x0A
            # hay 0x20, và strip() sẽ lặng lẽ cắt mất byte thật rồi báo "khoá sai độ dài".
            return raw
        text = raw.strip().decode("ascii", "ignore")
        if len(text) == 64:
            return bytes.fromhex(text)
        raise RuntimeError(f"khoá chữ ký ở {path} phải là 32 byte nhị phân, hoặc 64 ký tự hex "
                           f"(đang là {len(raw)} byte)")


@lru_cache
def get_settings() -> Settings:
    return Settings()
