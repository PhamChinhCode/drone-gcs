# Điểm cần chốt với đội firmware ESP32 và đội Pi 4

> **LỖI THỜI từ 17/09/2026.** Tài liệu này mô tả kênh ESP-NOW (dongle ESP32, khung COBS + CRC16 + HMAC,
> 27 bản tin tự định nghĩa). Kênh đó **đã bị thay** bằng MAVLink 2 / UDP theo
> [GIAO_UOC_GCS_PI.md](GIAO_UOC_GCS_PI.md). Giữ lại làm lịch sử và vì các mục không thuộc tầng liên kết
> (thiết kế khu vực, cảnh báo, phát lại, quyền người dùng, CSDL) **vẫn đúng**.
> Xem [KE_HOACH_CHUYEN_MAVLINK.md](KE_HOACH_CHUYEN_MAVLINK.md) để biết mục nào đã thay bằng gì.

**Toàn bộ tài liệu này đã lỗi thời**: mọi điểm cần chốt ở đây đều thuộc kênh ESP-NOW, và đã được giao ước GCS ↔ Pi trả lời hoặc thay thế.

*Cập nhật 14/09/2026. Tài liệu gốc: [thiet_ke_gcs_espnow_3d.md](thiet_ke_gcs_espnow_3d.md).*

Đặc tả gốc chỉ cho **byte-by-byte** 7 bản tin (TELEM_FAST, TELEM_SLOW, MISSION_WP, CMD_GOTO, EMERGENCY,
TAG_DETECT, ACK). Các bản tin còn lại chỉ có kích thước. Để GCS chạy được, phần mềm đã **đề xuất** layout
cho chúng — mọi đề xuất nằm ở một chỗ duy nhất: `backend/gcs_backend/link/messages.py` (đánh dấu `ĐỀ XUẤT`).
Các mục dưới đây phải được đội firmware + Pi 4 xác nhận **trước khi viết `gcs_link_node` và firmware ESP32**.

---

## 1. Layout bản tin đề xuất (little-endian, packed)

Kích thước khớp đúng bảng 5.3 của đặc tả.

| ID | Bản tin | Layout đề xuất | Byte |
|---|---|---|---|
| 0x10 | `HEARTBEAT_GCS` | `u32 t_ms, u32 map_crc, u16 session_id, u8 gcs_flags, u8 reserved` | 12 |
| 0x11 | `CMD_SIMPLE` | `u16 cmd_seq, u8 action, u8 reserved, i16 param` | 6 |
| 0x13 | `MISSION_BEGIN` | `u16 mission_id, u8 total, u8 flags, u32 map_crc, u16 cruise_alt_cm, u16 reserved` | 12 |
| 0x15 | `MISSION_END` | `u16 mission_id, u8 total, u8 reserved, u32 wp_crc` | 8 |
| 0x16 | `MISSION_CTRL` | `u16 mission_id, u8 action, u8 reserved, u16 cmd_seq` | 6 |
| 0x18 | `PARAM_SET` | `u16 param_id, u16 reserved, f32 value, u8[12] reserved` | 20 |
| 0x19 | `TAGMAP_BEGIN` | `u16 site_id, u8 total, u8 reserved, u32 map_crc` | 8 |
| 0x1A | `TAGMAP_ENTRY` | `u8 index, u8 total, u16 tag_id, i32 n_mm, i32 e_mm, i32 d_mm, i16 yaw_cdeg, u16 size_mm, u8 kind, u8 reserved` | 22 |
| 0x1B | `TAGMAP_END` | `u16 site_id, u8 total, u8 reserved, u32 map_crc` | 8 |
| 0x1C | `REQUEST` | `u8 what, u8 reserved, u16 arg` | 4 |
| 0x40 | `HEARTBEAT_DRONE` | `u32 t_ms, u32 map_crc, u8 fsm_state, u8 flags, u16 session_id` | 12 |
| 0x43 | `MISSION_STATE` | `u32 t_ms, u16 mission_id, u8 fsm_state, u8 prev_state, u8 wp_index, u8 wp_total, i16 expected_tag, u8 retry_count, u8 failsafe_type, u16 reserved` | 16 |
| 0x45 | `EVENT` | `u32 t_ms, u8 severity, u8 category, u16 code` + text UTF-8 ≤ 200 B | 8+n |
| 0x46 | `PARAM_VALUE` | `u16 param_id, u16 param_count, f32 value, u8[12] reserved` | 20 |
| 0x47 | `LINK_STAT` | `u16 tx_count, u16 tx_ok, u16 tx_fail, u16 rx_count, i8 rssi_dbm, u8 loss_pct, u16 rtt_ms` | 12 |

**Dongle (chan_id 0x02, không phát ra không khí)** — đặc tả chỉ nói "bản tin điều khiển dongle", chưa có mã:

| ID | Bản tin | Layout đề xuất | Byte |
|---|---|---|---|
| 0x70 | `DONGLE_STAT` (dongle → host, 1 Hz) | `u32 tx_count, u32 tx_ok, u32 tx_fail, u32 rx_count, i8 last_rssi_dbm, u8 queue_depth, u16 telem_dropped` | 20 |
| 0x71 | `DONGLE_CONFIG` (host → dongle) | `u8 channel, u8 lr_mode, u8[6] peer_mac, u16 reserved` | 10 |

Khung chan 0x02 dùng **cùng** định dạng lớp 3 (header 8 B + CRC16) để bắt lỗi dây UART; `src = 0x2` (ESP32-GCS).

## 2. Mã giá trị đề xuất

- `CMD_SIMPLE.action`: 1 ARM, 2 DISARM, 3 TAKEOFF (`param` = độ cao cm), 4 LAND, 5 HOLD, 6 PRECISION_LAND (`param` = tag_id), 7 RTH.
- `MISSION_CTRL.action`: 1 START, 2 PAUSE, 3 RESUME, 4 ABORT.
- `REQUEST.what`: 1 PARAMS, 2 MISSION_STATE, 3 TAGMAP_CRC.
- `tag kind` (dùng trong `map_crc` và `TAGMAP_ENTRY`): 0 home, 1 pickup, 2 dropoff, 3 waypoint.
- `EVENT.category`: 0 failsafe, 1 command, 2 link, 3 gripper, 4 marker, 5 user, 6 mission.
- **`param_id`** (tên khóa `safety.yaml` dài hơn 16 ký tự nên không gửi tên được):
  1 `low_battery_pct`, 2 `critical_battery_pct`, 3 `link_lost_timeout_s`, 4 `marker_search_timeout_s`,
  5 `max_retries`, 6 `takeoff_alt_m`, 7 `acceptance_radius_m`.
- `wp_crc` = CRC32 trên payload 28 B của các `MISSION_WP` nối theo thứ tự `seq`.

## 3. Enum dùng chung — mục 13 quyết định số 4 (CHƯA CHỐT)

Phần mềm đang dùng nguyên bảng 14.1 của đặc tả (`MissionState` 0–10, `FailsafeType` 0–6, `GripperState`, `WpAction`).
**Khoảng trống phát hiện khi làm drone giả lập:** `mission_state_e` không có trạng thái cho *hạ cánh thường*
và *giữ vị trí khi bay tay*. Giả lập hiện dùng `PRECISION_LAND` cho hạ cánh thường và `IDLE` (kèm cờ armed) khi
giữ vị trí. Cần đội Pi 4 quyết: thêm trạng thái hay giữ cách hiểu này.

## 4. Bắt tay phiên / chống phát lại — CÓ RỦI RO, cần quyết

Đặc tả (4.3) nói `session_key` sinh mới mỗi phiên "qua bản tin bắt tay" nhưng **chưa có msg_id cho bắt tay**.
Hiện tại:

- `session_key` là khóa tĩnh cấu hình (`GCS_SESSION_KEY`, 16 byte hex), dùng cho HMAC `auth_tag` và `confirm_code` của KILL.
- Mỗi lần khởi động, mỗi bên sinh `session_id` ngẫu nhiên và gửi trong heartbeat. Bên nhận thấy `session_id`
  đổi thì **xóa cửa sổ chống trùng/phát lại** của nguồn đó.

Lý do: khi kiểm thử, GCS khởi động lại trong lúc drone vẫn chạy làm `seq` quay về 1 → drone coi lệnh mới là
gói trùng, **ACK nhưng không thực thi** (đã có test hồi quy `test_gcs_restart_new_session_commands_still_execute`).

**Rủi ro còn lại:** heartbeat là ưu tiên 1, không có `auth_tag`; kẻ phát lại một heartbeat cũ có thể reset cửa sổ
rồi phát lại lệnh cũ. CCMP của ESP-NOW chặn giả mạo gói mới nhưng không chặn phát lại. Đề xuất bắt tay thật:
`HELLO(nonce_gcs)` → `HELLO_ACK(nonce_drone)`, `session_key = HMAC(PSK, nonce_gcs ‖ nonce_drone)`, dùng hai mã
còn trống trong dải 0x20–0x3F / 0x48–0x6F.

## 5. Các điểm khác

1. **Cửa sổ chống phát lại dời theo mọi khung** (không chỉ lệnh): `seq` dùng chung cho telemetry ~20 gói/s nên
   quay vòng 16 bit sau ~55 phút; nếu chỉ lệnh mới dời cửa sổ, lệnh hợp lệ sau thời gian dài không có lệnh sẽ bị
   coi là cũ. Pi 4 phải làm giống.
2. **Hàng đợi ưu tiên đảo thứ tự seq** trên dây (ACK ưu tiên 2 vượt telemetry ưu tiên 0). Bên nhận phải chấp nhận
   gói đến "trễ"; cách đếm PDR phía GCS đã xử lý điều này.
3. **A1 (fuzz 10⁶ khung, 0 lỗi lọt):** CRC16 cho xác suất lọt lý thuyết ~1/65536 với lỗi ngẫu nhiên dạng cụm
   (COBS biến lỗi bit thành lỗi cụm). Với 10⁶ khung hỏng có thể lọt vài khung — cần chạy `GCS_FUZZ_N=1000000`
   và quyết định có nâng lên CRC32 hay chấp nhận (lệnh ưu tiên ≥ 2 còn có `auth_tag` 32 bit bảo vệ thêm).
4. **Vị trí trạm GCS**: lược đồ 7.4 không có trường này nhưng 8.4.3 cần để vẽ vòng bán kính liên kết → đã thêm
   `site.gcs_pos_n_m / gcs_pos_e_m`.
5. **Mục 13 quyết định 1–3** (bán kính mục tiêu, SQLite/PostgreSQL, cưỡng chế vùng bay phía drone) vẫn mở. Phần mềm
   đang theo khuyến nghị của đặc tả: SQLite; vùng bay chỉ cảnh báo phía GCS; 0x1D–0x1F giữ chỗ.
