# GCS — Trạm điều khiển mặt đất (ESP-NOW, giám sát 3D)

Phần mềm trạm mặt đất theo đặc tả [docs/thiet_ke_gcs_espnow_3d.md](docs/thiet_ke_gcs_espnow_3d.md):
backend Python (FastAPI + asyncio), frontend React + Three.js (react-three-fiber), liên kết nhị phân tự định nghĩa
qua ESP-NOW. Hiện chạy hoàn toàn **không cần phần cứng** nhờ drone giả lập.

- Kiến trúc & tình trạng: [docs/KIEN_TRUC_TRIEN_KHAI.md](docs/KIEN_TRUC_TRIEN_KHAI.md)
- Điểm cần chốt với đội firmware/Pi 4: [docs/DIEM_CAN_CHOT.md](docs/DIEM_CAN_CHOT.md)

## Yêu cầu

Python ≥ 3.11, Node ≥ 20.

## Cài đặt

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd ..\web
npm install
```

## Chạy thử (3 cửa sổ)

```powershell
# 1. Drone giả lập (đóng vai dongle + ESP-NOW + Pi 4)
cd backend; .venv\Scripts\python -m gcs_backend.sim.fake_drone

# 2. Backend
cd backend; .venv\Scripts\python -m uvicorn gcs_backend.main:app --port 8000

# 3. Frontend (dev, có hot reload) → http://localhost:5173
cd web; npm run dev
```

Hoặc build frontend một lần (`cd web; npm run build`) rồi mở thẳng http://127.0.0.1:8000 — backend tự phục vụ `web/dist`.

Tài khoản mặc định: `admin/admin` và `operator/operator` (đổi bằng biến môi trường `GCS_ADMIN_PASSWORD`, …).
Lần chạy đầu tạo sẵn khu vực mẫu (Home + 4 tag + vùng bay + 1 vùng cấm).

Kịch bản thử nhanh: đăng nhập admin → banner đỏ "bản đồ tag lệch" → bấm **Đồng bộ bản đồ** → tab **Nhiệm vụ**
chọn A → B → **Lập kế hoạch** → **Tải lên** → **Bắt đầu** → quay lại **Vận hành 3D** (phím 1–4, 0 đổi góc nhìn).

### Gây lỗi trên drone giả lập

Cổng điều khiển text `127.0.0.1:5761` (dùng `ncat`, `telnet` hoặc PowerShell):

```
drop 0.3        rớt 30 % gói         outage 8     mất liên kết 8 s
hang 10         Pi 4 treo 10 s       dongle off   mất dongle
noeffect on     ACK mà không làm     param low_battery_pct 30   lệch cấu hình
reject 0x11 3   từ chối lệnh         battery 20   đặt % pin
```

### Cấu hình (biến môi trường `GCS_*` hoặc `backend/.env`)

| Biến | Mặc định | |
|---|---|---|
| `GCS_LINK_URL` | `tcp://127.0.0.1:5760` | `serial://COM5?baud=921600` khi có dongle ESP32 |
| `GCS_SESSION_KEY` | khóa mẫu | 32 ký tự hex, phải trùng phía drone |
| `GCS_DB_URL` | `sqlite:///./gcs.db` | |
| `GCS_JWT_SECRET` | khóa mẫu | **đổi khi triển khai** |

Khóa PMK/LMK của ESP-NOW **không** đưa vào git (mục 4.3).

## Kiểm thử

```powershell
cd backend; .venv\Scripts\python -m pytest            # 48 test: giao thức, A1–A4, A9–A11, A16, đầu-cuối
cd web; npm test                                       # A6 hệ trục 3D, A8 không ngoại suy
```

`$env:GCS_FUZZ_N=1000000` để chạy A1 đủ 10⁶ khung.
