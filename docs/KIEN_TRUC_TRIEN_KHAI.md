# Kiến trúc triển khai GCS

*Cập nhật 17/09/2026 sau khi chuyển kênh sang MAVLink 2 / UDP.*

**Nguồn sự thật của kênh nhiệm vụ là [GIAO_UOC_GCS_PI.md](GIAO_UOC_GCS_PI.md)** (bản sao chỉ đọc; bản gốc ở
repo `drone-ros2-jazzy`) và file dialect `backend/gcs_backend/link_mav/drone_gcs.xml`. Thứ tự ưu tiên khi mâu
thuẫn: phép đo trên dây → XML → tài liệu.

`thiet_ke_gcs_espnow_3d.md` mục 4–5 và `DIEM_CAN_CHOT.md` mô tả kênh ESP-NOW cũ, **đã lỗi thời** — giữ lại làm
lịch sử. Các mục khác của đặc tả (thiết kế khu vực, cảnh báo, phát lại, quyền người dùng) vẫn đúng.

## 1. Phạm vi

Kênh nhiệm vụ chạy **MAVLink 2 trên UDP**: GCS nghe `:14550`, Pi gọi ra trước từ `:14551` và GCS trả lời về địa
chỉ nguồn của **gói hợp lệ gần nhất** — modem 4G nằm sau CGNAT nên chiều ngược lại không mở được (giao ước 2.1).

Chưa có Pi thật trên dây, nên liên kết chạy qua `sim/fake_pi.py` nói đúng hợp đồng. Đổi sang Pi thật chỉ là đổi
địa chỉ: không có mã nào rẽ nhánh theo "thật hay giả lập".

**GCS không lái.** Không ARM, không TAKEOFF rời, không GOTO, không giữ vị trí tay (giao ước 9.3). Sáu lệnh duy
nhất đi trên kênh này: `MISSION_START`, `NAV_RETURN_TO_LAUNCH`, `NAV_LAND`, `COMPONENT_ARM_DISARM` (chỉ disarm),
`DRONE_ABORT_MISSION`, `DO_PAUSE_CONTINUE` (Pi trả `UNSUPPORTED`, GCS ẩn nút).

Còn lại: chạy nghiệm thu 10.A với `gcs_link_node` thật, rồi 10.B (Gazebo) và 10.C (4G thật).

## 2. Sơ đồ mã nguồn

```
GrounControlStation/
├── backend/gcs_backend/
│   ├── link_mav/    drone_gcs.xml (dialect, bản sao của repo Pi) + gen_dialect.py → dialect/
│   │                transport.py (UDP) codec.py (MAVLink 2 + chữ ký) link.py (heartbeat, TIMESYNC,
│   │                thống kê, ghép STATUSTEXT) commands.py mission_client.py params.py
│   │                telemetry.py (bản tin → dict SI) tagmap.py (tagmap_crc)
│   ├── mission/     planner.py uploader.py (→ mục trên dây + tags.yaml) shadow.py
│   ├── safety/      thresholds.py monitor.py
│   ├── sitedesign/  geometry.py validator.py          ← bộ kiểm tra 8.4.4 (đặc tả chưa đặt chỗ)
│   ├── data/        models.py repo.py
│   ├── api/         rest.py ws.py auth.py
│   ├── sim/         fake_pi.py
│   ├── runtime.py   nối link ↔ shadow ↔ kiểm chứng lệnh ↔ cảnh báo ↔ CSDL ↔ WS
│   ├── bus.py, config.py, main.py
│   └── tests/ (ở backend/tests)
└── web/src/
    ├── app/         App.tsx (router, layout), Login.tsx, theme.css
    ├── lib/         api.ts ws.ts (LiveSource) types.ts units.ts apriltag36h11.ts (bảng mã 36h11 + dựng SVG)
    ├── store/       auth.ts live.ts site.ts (zustand)
    └── features/
        ├── scene3d/     Scene.tsx CameraRig.tsx coords.ts useTelemetryBuffer.ts objects/
        ├── monitor/     OperationPage Hud EmergencyBar Banners TelemetryPanel
        ├── mission/     MissionPage (tạo nhiệm vụ + hàng đợi)
        ├── tags/        TagsPage
        ├── sitedesign/  SiteDesignPage (sơ đồ SVG 2D)
        ├── history/     HistoryPage (danh sách + ReplaySource + trình phát lại)
        ├── alerts/      AlertsPage
        └── admin/       AdminPage LinkDiagnostics Sparkline
```

## 3. Luồng dữ liệu chính

```
fake_drone / dongle ──COBS──► LinkSession.rx_loop ──► on_unit: CRC, auth, chống trùng, ACK
                                                        │
                                                   bus "rx" ──► Runtime._on_rx
                                                                 ├─ DroneShadow (6.2)
                                                                 ├─ CommandTracker.evaluate (6.3)
                                                                 ├─ SafetyMonitor (ngưỡng, vùng bay, liên kết)
                                                                 ├─ bộ đệm ghi telemetry → SQLite mỗi 1 s
                                                                 └─ WsHub.push ──gộp 100 ms──► trình duyệt
trình duyệt: ws.ts ──TELEM_FAST──► liveBuffer (KHÔNG qua React state) ──useFrame──► cảnh 3D
                  └─các bản tin khác──► zustand store (HUD cập nhật 5 Hz)
```

Lệnh: REST → `Runtime.cmd_*` → `CommandTracker.new` (giai đoạn *sending*) → `LinkSession.send` (retry giữ nguyên
khung + seq) → ACK (*acked*) → kiểm chứng phản hồi trạng thái (*effective* / *not_effective*). Lệnh khẩn cấp trả về
ngay, tiến trình ba giai đoạn đẩy qua WebSocket để thanh khẩn cấp hiển thị (8.3).

## 4. Sai khác có chủ đích so với đặc tả

| Chỗ | Đặc tả | Triển khai | Lý do |
|---|---|---|---|
| Layout 15 bản tin + dongle | chỉ có kích thước | đề xuất đầy đủ | xem DIEM_CAN_CHOT.md §1 |
| `PARAM_SET/VALUE` | (ngầm hiểu theo tên) | theo `param_id` | tên khóa > 16 ký tự không vừa 20 B |
| Bắt tay phiên | có, chưa đặc tả | khóa tĩnh + `session_id` trong heartbeat | xem DIEM_CAN_CHOT.md §4 |
| `sim` qua socat | cặp pty | TCP | Windows không có socat |
| `serial_io` | pyserial-asyncio | pyserial + luồng đọc | vòng Proactor của Windows không hỗ trợ `add_reader` |
| `site` | — | thêm `gcs_pos_n_m/e_m` | cần tâm vòng bán kính liên kết |
| `t_utc` telemetry/event | TIMESTAMP | REAL epoch giây | gọn, tua phát lại nhanh |
| alembic | có | `create_all` | giai đoạn nghiên cứu, lược đồ còn đổi |
| Vệt bay | `Line2` | `THREE.Line` + mảng cấp sẵn 3000 điểm + `setDrawRange` | Line2 phải cấp phát lại hình khi thêm điểm; giữ đúng mục tiêu hiệu năng 9.5 |
| Mô hình drone | glTF ≤ 20k tam giác | dựng thủ tục (~1k tam giác) | chưa có file mô hình; thay bằng glTF sau không đổi API |
| Nhãn trong 3D | Sprite | Sprite vẽ canvas, kích thước cố định trên màn hình | không tải font từ mạng (GCS chạy offline) và đọc được ở góc nhìn toàn khu vực |
| Hàng đợi | màn hình riêng | gộp trong màn Nhiệm vụ | thao tác chọn/tải lên/bắt đầu liền mạch |
| Báo cáo | `report.pdf` | `GET /api/missions/{id}/report` (JSON) + hiển thị ở màn phát lại | PDF làm sau |
| Tờ in tag | `tag-sheet.pdf` | Trang HTML dựng SVG ở client (`apriltag36h11.ts`), mở tab mới để in/lưu PDF từ trình duyệt | không cần thêm thư viện PDF ở backend; SVG in đúng mm ở mọi độ phân giải máy in |

## 5. Tình trạng yêu cầu chức năng

| Nhóm | Đã có | Chưa có |
|---|---|---|
| F1 Tag | CRUD, dạy vị trí từ drone, đồng bộ ba pha + `map_crc` hai bên, chặn nhiệm vụ khi lệch, in tờ tag đúng kích thước (SVG dựng ở client từ bảng mã 36h11, trang HTML in qua trình duyệt) | — |
| F2 Điều khiển tay | arm/disarm, cất cánh, bay tới MAP/TAG/BODY, giữ, hạ, hạ chính xác, RTH; ACK + hủy + kiểm chứng | — |
| F3 Nhiệm vụ | lấy→giao, chuỗi tùy biến, xem trước 3D, tải lên, start/pause/resume/abort tách rời, hàng đợi ưu tiên | kéo-thả sắp xếp (đang dùng nút ▲▼) |
| F4 Giám sát 3D | toàn bộ cây cảnh 9.1, 5 góc nhìn, nội suy 150 ms không ngoại suy, làm mờ khi cũ, HUD DOM | glTF |
| F5 Cảnh báo | thanh khẩn cấp giữ 800 ms + 3 giai đoạn, KILL admin + gõ từ khóa, cảnh báo ngưỡng, nhật ký | — |
| F6 Lịch sử | lưu telemetry/sự kiện, phát lại cùng cảnh 3D, tua, 0,5×/1×/4×, mốc sự kiện, báo cáo JSON | báo cáo PDF, ảnh (kênh WiFi — giai đoạn sau) |
| F7 Quản trị | 2 cấp quyền JWT, ngưỡng + đối chiếu lệch tự động, chẩn đoán RSSI/PDR/RTT, cấu hình kênh/LR/MAC, audit | — |
| F8 Thiết kế khu vực | đặt/kéo tag (Shift bắt lưới), Home, vẽ vùng bay/cấm, sửa đỉnh, thước đo bắt tâm tag, ảnh nền 2 điểm mốc, vòng bán kính đo/70 %/mục tiêu, 10 quy tắc kiểm tra + bấm để lia tới, lưu một giao dịch, xác nhận cảnh báo ghi audit, xuất PNG | xuất PDF |

## 6. Tình trạng nghiệm thu (bảng 11.3)

| # | Trạng thái | Bằng chứng |
|---|---|---|
| A1 | Đạt ở 20 000 khung | `test_fuzz_a1_*`; chạy đủ bằng `GCS_FUZZ_N=1000000` — xem lưu ý DIEM_CAN_CHOT §5.3 |
| A2 | Đạt | `test_a2_duplicate_seq_executes_once_acks_every_time` |
| A3 | Đạt (không lệnh nào chạy hai lần) | `test_a3_retry_with_30pct_drop_*` |
| A4 | Đạt ở T0 | `test_a4_emergency_under_telemetry_flood` (cần đo lại ở T1) |
| A5 | Logic đạt ở T0 | `test_link_state_distinguishes_dongle_vs_drone`; cần T1 với ăng-ten thật |
| A6 | Đạt | `web/src/features/scene3d/coords.test.ts` — đủ 5 trường hợp |
| A7 | Chưa đo | cần đo fps trên máy Iris Xe với 50 tag + 3000 điểm |
| A8 | Đạt | `buffer.test.ts` + kiểm tra trên trình duyệt (drone đứng yên, mờ, "số liệu cũ N s") |
| A9 | Đạt ở T0 | `test_a9_upload_survives_packet_loss` |
| A10 | Đạt ở T0 | `test_a10_config_drift_names_key` |
| A11 | Đạt | `test_a11_ack_without_effect_raises_alert` |
| A12–A14 | Cần phần cứng (T2/T3) | logic khôi phục shadow sau khởi động lại đã có |
| A15 | Chức năng có, cần T4 | màn phát lại |
| A16 | Đạt | `test_a16_each_rule_triggers` (10 mã) + `test_design_save_requires_ack_and_blocks_errors` |
| A17 | Đạt | `web/src/lib/apriltag36h11.ts` (bảng mã + dựng SVG, đối chiếu pixel với ảnh tham chiếu AprilRobotics/apriltag-imgs) + nút "In tờ tag" ở `TagsPage.tsx` |

## 7. Việc cho giai đoạn 5 (firmware ESP32) — hợp đồng phía GCS đã sẵn

1. UART/USB CDC 921600 8N1, khung `COBS(chan_id ‖ đơn vị) ‖ 0x00`.
2. `chan 0x01`: chuyển nguyên văn đơn vị lớp 3 qua `esp_now_send` — **không** tính lại CRC, không đổi seq.
3. `chan 0x02`: gửi `DONGLE_STAT (0x70)` mỗi 1 s; nhận `DONGLE_CONFIG (0x71)`. Nếu dongle ngừng gửi 2 s, GCS báo "mất dongle".
4. Hàng đợi TX 2 mức theo bit PRIORITY của header: ưu tiên ≥ 2 chèn đầu; đầy thì bỏ telemetry cũ nhất.
5. Kiểm thử T1: chạy backend với `GCS_LINK_URL=serial://COMx`, hai ESP32 cách 1 m, Pi 4 thay bằng
   `fake_drone.py` nối UART (cần thêm transport serial cho phía giả lập — hiện chỉ có TCP server).
