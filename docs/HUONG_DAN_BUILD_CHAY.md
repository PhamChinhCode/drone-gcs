# Hướng dẫn build và chạy — GCS

Tham khảo nhanh, gộp lại từ [`README.md`](../README.md) (nơi có thêm bối cảnh/kiến trúc) và những
gì đã xác nhận chạy thật. Song sinh với [`../../drone-ros2-jazzy/docs/HUONG_DAN_BUILD_CHAY.md`](../../drone-ros2-jazzy/docs/HUONG_DAN_BUILD_CHAY.md)
phía Pi — hai tài liệu tách riêng vì build/chạy là việc nội bộ mỗi bên, không thuộc giao ước
[`GIAO_UOC_GCS_PI.md`](GIAO_UOC_GCS_PI.md).

## 0. Yêu cầu

- Python ≥ 3.11, Node ≥ 20.
- `pymavlink` **ghim đúng 2.4.49** — bộ sinh mã dialect là một phần của hợp đồng, y như file XML
  (giao ước 7.1). Bản trong `requirements.txt` đã ghim sẵn, đừng tự nâng cấp.

## 1. Cài đặt lần đầu

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd ..\web
npm install
```

## 2. Cấu hình (`backend/.env`)

Tạo `backend/.env` (đã gitignore, không commit). Tối thiểu cần quyết định hai biến này trước khi
chạy — sai một trong hai là liên kết **im lặng không lên**, không lỗi, không log rõ ràng:

```ini
GCS_MAV_HOST=0.0.0.0
GCS_MAV_PORT=14550
GCS_SIGNING=false        # true nếu Pi VÀ GCS cùng bật chữ ký (giao ước 7.6) — PHẢI khớp hai bên
```

Toàn bộ biến khác và giá trị mặc định xem bảng ở [`README.md`](../README.md#cấu-hình-biến-môi-trường-gcs-hoặc-backendenv).

**Nếu Pi ở máy khác** (không cùng LAN — ví dụ nối qua Tailscale như thiết lập hiện tại): sửa
`gcs_host` trong `comms.yaml` **phía Pi** trỏ đúng IP mà Pi thấy được của máy chạy GCS này (Pi gọi
ra trước — giao ước 2.1). Xem mục 6.

## 3. Sinh mã dialect MAVLink (chỉ khi `drone_gcs.xml` đổi)

`gcs_backend/link_mav/dialect/drone_gcs.py` là mã **sinh ra**, không sửa tay và không commit. Sinh
lại sau mỗi lần sửa [`gcs_backend/link_mav/drone_gcs.xml`](../backend/gcs_backend/link_mav/drone_gcs.xml)
(bản sao của `docs/mavlink/drone_gcs.xml` bên Pi — luôn sửa ở đó trước, chép sang đây, rồi sinh lại
**cả hai bên**):

```powershell
cd backend
.venv\Scripts\python -m gcs_backend.link_mav.gen_dialect
```

## 4. Chạy thử (3 cửa sổ)

```powershell
# 1. Pi giả lập — nói đúng giao ước, gọi ra trước như Pi thật sau NAT. Bỏ qua nếu đã có Pi thật/mô phỏng chạy sẵn.
cd backend; .venv\Scripts\python -m gcs_backend.sim.fake_pi

# 2. Backend (nghe UDP theo GCS_MAV_PORT, mặc định 14550)
cd backend; .venv\Scripts\python -m uvicorn gcs_backend.main:app --port 8000

# 3. Frontend (dev, hot reload) → http://localhost:5173
cd web; npm run dev
```

Đăng nhập mặc định `admin/admin` hoặc `operator/operator` (đổi qua `GCS_ADMIN_PASSWORD`, …). Lần
chạy đầu tự tạo khu vực mẫu (Home + 4 tag + vùng bay + 1 vùng cấm).

### Chạy một tiến trình duy nhất (gần với triển khai thật)

```powershell
cd web; npm run build        # ra web/dist
cd ..\backend; .venv\Scripts\python -m uvicorn gcs_backend.main:app --port 8000
```

Backend tự phục vụ `web/dist` ở `http://127.0.0.1:8000` khi thư mục đó tồn tại — không cần chạy
`npm run dev` song song nữa.

### Dừng backend

Chạy trực tiếp ở cửa sổ trước mặt (như hai cách trên): `Ctrl+C` — `uvicorn` bắt `SIGINT`, chạy hết
`lifespan` (`rt.stop()` trong `main.py`) rồi thoát sạch, không cần làm gì thêm.

Chạy nền/ẩn (ví dụ khởi động bằng `Start-Process -WindowStyle Hidden`, không có cửa sổ để bấm
`Ctrl+C`) thì tìm đúng tiến trình rồi dừng bằng tay:

```powershell
netstat -ano | findstr 14550        # cột cuối là PID đang giữ cổng MAVLink UDP
Stop-Process -Id <PID> -Force
```

Sau khi dừng, `netstat -ano | findstr 14550` phải không còn dòng nào thì mới khởi động lại backend
được (cổng UDP còn bị giữ thì backend mới không bind được, lỗi ngay lúc mở).

## 5. Kiểm thử

```powershell
cd backend; .venv\Scripts\python -m pytest      # dialect, liên kết, lệnh, đầu-cuối
cd web; npm run typecheck                       # tsc -b — bắt lỗi kiểu trước khi build
cd web; npm test                                # vitest — hệ trục 3D, buffer telemetry
```

## 6. Kết nối tới Pi ở máy khác (không cùng LAN)

Thiết lập hiện tại: GCS chạy trên máy Windows này, Pi (thật hoặc mô phỏng Gazebo) chạy trên một máy
Ubuntu khác, hai máy nối qua **Tailscale** (không cùng subnet LAN).

1. Lấy IP Tailscale của máy GCS: `tailscale ip -4` (hoặc `& "C:\Program Files\Tailscale\tailscale.exe" ip -4`).
2. Bên Pi, sửa `gcs_host` trong `src/drone_bringup/config/comms.yaml` thành đúng IP đó (Pi gọi ra
   trước — địa chỉ này chỉ là điểm khởi động, sau đó GCS tự học địa chỉ nguồn của gói hợp lệ gần
   nhất, giao ước 2.1).
3. Đảm bảo tường lửa Windows không chặn UDP inbound trên `GCS_MAV_PORT` (14550).
4. `GCS_SIGNING` ở `.env` phải khớp `signing_required` trong `comms.yaml` bên Pi — lệch nhau thì
   liên kết **im lặng không lên**, không lỗi hiển thị.

Kiểm tra nhanh đường mạng thông chưa (không cần chạy cả hai stack): gửi thử một gói UDP tay bằng
`nc -u <ip-tailscale-cua-may-nay> 14550` từ phía Pi, hoặc xem log `gcs_link_node` — dòng
`"GCS co ket noi"` nghĩa là hai bên đã thấy nhau.

## 7. Nạp bản đồ tag qua dây (giao ước 8.7)

Từ bản 0.6: sửa tag ở trang **Tags** → bấm **"Nạp qua dây"** → Pi trả `ACCEPTED` → **người vận
hành khởi động lại stack bên Pi bằng tay** (chưa tự động) → ô **KHỚP/LỆCH** trên trang Tags tự
chuyển sang KHỚP khi đã có hiệu lực thật. Giới hạn: không thêm được tag ID mới (phải sửa
`apriltag.yaml` bên Pi bằng tay), tối đa 32 tag. Chi tiết: [`GIAO_UOC_GCS_PI.md` mục 8.7](GIAO_UOC_GCS_PI.md#87-nạp-bản-đồ-tag-qua-dây--thoả-thuận-hiện-thực-xong-cả-hai-bên-đã-chạy-thật-trên-dây-87).

## 8. Gỡ lỗi trên dây

```bash
sudo tcpdump -i any -n udp port 14550 or udp port 14551 -w /tmp/gcs.pcap
```

Chữ ký gói (nếu bật) **không** mã hoá nội dung — cố ý, để bắt gói gỡ lỗi được (giao ước 7.6).
