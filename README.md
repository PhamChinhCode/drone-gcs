# GCS — Trạm điều khiển mặt đất (MAVLink 2 / UDP, giám sát 3D)

Phần mềm trạm mặt đất cho drone AprilTag không GPS: backend Python (FastAPI + asyncio), frontend React + Three.js.
Kênh nhiệm vụ nói **MAVLink 2 qua UDP** theo [docs/GIAO_UOC_GCS_PI.md](docs/GIAO_UOC_GCS_PI.md) — bản sao chỉ đọc
của hợp đồng, bản gốc ở repo `drone-ros2-jazzy`. Chạy hoàn toàn **không cần phần cứng** nhờ Pi giả lập.

Đây là **kênh nhiệm vụ, không phải kênh điều khiển bay**. GCS không arm, không lái, không điều khiển failsafe;
người lái dùng RC trực tiếp tới FC. Mất kênh này thì drone phải tự hoàn thành hoặc tự về.

- Kế hoạch chuyển đổi và tình trạng: [docs/KE_HOACH_CHUYEN_MAVLINK.md](docs/KE_HOACH_CHUYEN_MAVLINK.md)
- Kiến trúc: [docs/KIEN_TRUC_TRIEN_KHAI.md](docs/KIEN_TRUC_TRIEN_KHAI.md)

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

`pymavlink` được **ghim 2.4.49** ở cả hai phía: bộ sinh mã là một phần của hợp đồng y như file XML (giao ước 7.1).

## Chạy thử (3 cửa sổ)

```powershell
# 1. Pi giả lập — nói đúng giao ước, gọi ra trước như Pi thật sau NAT
cd backend; .venv\Scripts\python -m gcs_backend.sim.fake_pi

# 2. Backend (nghe UDP :14550)
cd backend; .venv\Scripts\python -m uvicorn gcs_backend.main:app --port 8000

# 3. Frontend (dev, có hot reload) → http://localhost:5173
cd web; npm run dev
```

Hoặc build frontend một lần (`cd web; npm run build`) rồi mở thẳng http://127.0.0.1:8000 — backend tự phục vụ `web/dist`.

Tài khoản mặc định: `admin/admin` và `operator/operator` (đổi bằng biến môi trường `GCS_ADMIN_PASSWORD`, …).
Lần chạy đầu tạo sẵn khu vực mẫu (Home + 4 tag + vùng bay + 1 vùng cấm).

Kịch bản thử nhanh: đăng nhập admin → banner đỏ "bản đồ tag lệch" → **Tải tags.yaml** rồi chép sang Pi
→ tab **Nhiệm vụ** chọn A → B → **Lập kế hoạch** → **Tải lên** → **Bắt đầu** → quay lại **Vận hành 3D**.

## Chữ ký gói — mặc định BẬT

UDP thuần trên 4G công cộng nghĩa là **bất cứ ai biết `IP:port` đều gửi được lệnh cho drone**, kể cả lệnh cắt
động cơ. MAVLink 2 ký gói bằng HMAC-SHA256 với khoá 32 byte chia sẻ trước (giao ước 7.6):

```powershell
# sinh khoá, chép ĐÚNG khoá này sang Pi, quyền 600, KHÔNG commit
cd backend; .venv\Scripts\python -c "import os,pathlib;pathlib.Path('gcs_signing.key').write_bytes(os.urandom(32))"
```

Chạy trong mạng kín thì đặt `GCS_SIGNING=false` — và đó phải là quyết định có chủ ý, không phải mặc định.

## Cấu hình (biến môi trường `GCS_*` hoặc `backend/.env`)

| Biến | Mặc định | |
|---|---|---|
| `GCS_MAV_PORT` | `14550` | cổng UDP GCS lắng nghe; Pi gọi ra trước tới đây |
| `GCS_MAV_HOST` | `0.0.0.0` | |
| `GCS_SIGNING` | `true` | tắt **chỉ khi** Pi và GCS cùng mạng kín |
| `GCS_SIGNING_KEY_FILE` | `./gcs_signing.key` | 32 byte nhị phân hoặc 64 ký tự hex |
| `GCS_DB_URL` | `sqlite:///./gcs.db` | |
| `GCS_JWT_SECRET` | khóa mẫu | **đổi khi triển khai** |

GCS phải có **điểm cuối ổn định** (IP tĩnh, DNS động, hoặc VPN chung): modem 4G nằm sau CGNAT nên GCS không
bao giờ chủ động mở kết nối tới Pi được (giao ước 2.1).

## Kiểm thử

```powershell
cd backend; .venv\Scripts\python -m pytest    # 62 test: dialect A13–A15, liên kết C4/C5, lệnh A4–A7/A16, đầu-cuối
cd web; npm test                              # hệ trục 3D, không ngoại suy qua khoảng trống
```

Bắt gói để gỡ lỗi (chữ ký **không** mã hoá nội dung, cố ý — gỡ lỗi được quan trọng hơn giữ kín nhiệm vụ):

```bash
sudo tcpdump -i any -n udp port 14550 or udp port 14551 -w /tmp/gcs.pcap
```
