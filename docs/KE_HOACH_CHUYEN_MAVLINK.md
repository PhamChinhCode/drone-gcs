# Kế hoạch chuyển liên kết GCS sang MAVLink 2 / UDP

*Viết lại 16/09/2026 theo **giao ước GCS ↔ Pi bản 0.3** (bản gốc: `drone-ros2-jazzy/docs/GIAO_UOC_GCS_PI.md`).
Bản 15/09 (mavlink-router, mission protocol chuẩn, dải ID 53000) đã bị giao ước thay thế, bỏ.*

Giao ước là nguồn sự thật. Tài liệu này chỉ nói **phía GCS làm gì, theo thứ tự nào, kiểm bằng gì**.
Số mục trong ngoặc (ví dụ "4.2") là số mục của giao ước.

---

## 0. Những gì đổi so với backend hiện tại

| Hiện tại | Sau khi chuyển | Giao ước |
|---|---|---|
| TCP/serial tới dongle ESP32, khung COBS + CRC16 + HMAC | **UDP**, GCS nghe `:14550`, trả về địa chỉ nguồn của **gói hợp lệ gần nhất** từ `(1, 191)` | 2.1 |
| HMAC `auth_tag`, cửa sổ chống phát lại | **Chữ ký MAVLink 2**, mặc định BẬT, khoá ngoài repo | 7.6 |
| 27 bản tin tự định nghĩa | Chuẩn: `HEARTBEAT`, `COMMAND_LONG/ACK`, `LOCAL_POSITION_NED`, `ATTITUDE`, `TIMESYNC`, `STATUSTEXT`, `PARAM_*`. Dialect: 6 bản tin `420xx` | 8.1 |
| `TELEM_FAST` 10 Hz | `LOCAL_POSITION_NED` + `ATTITUDE` 5 Hz, **chỉ khi `POS_VALID`** | 5.1 |
| `TELEM_SLOW`, `MISSION_STATE`, `HEARTBEAT_DRONE` | `DRONE_TELEMETRY` 2 Hz, có `valid_flags` / `status_flags` | 5.2, 8.4 |
| ARM, TAKEOFF, GOTO, HOLD, PRECISION_LAND | **Bỏ.** GCS không arm, không lái | 9.3 |
| `MISSION_CTRL` START/PAUSE/RESUME/ABORT | 300 / 193 (Pi hiện trả `UNSUPPORTED` → ẩn nút) / 42100 | 4.1 |
| `EMERGENCY` RTH/LAND/HOLD/ABORT/KILL | 20 / 21 / — / 42100 / 400 `param2=21196`; phát lại **vô hạn** mỗi 0,5 s | 4.2 |
| Nạp kế hoạch nhiều điểm toạ độ | **Một mục mỗi điểm dừng theo marker**, ≤ 16, tự thêm mục cuối là home `action=NONE` | 3.2b, 8.3 |
| Đồng bộ bản đồ tag xuống drone | **Chỉ so `tagmap_crc`**; GCS xuất `tags.yaml` để triển khai tay; khoá nạp khi lệch | 8.6 |
| `PARAM_SET` đẩy ngưỡng | **Chỉ đọc** `PARAM_REQUEST_LIST` | 9.4 |
| Pin, GPS luôn hiển thị | Ẩn khi `BATTERY_VALID` / `GLOBAL_POS_VALID` = 0 (hiện luôn = 0) | 5.2 |

Không đổi: CSDL, thiết kế khu vực, cảnh báo, phát lại, WebSocket, xác thực người dùng.

---

## 1. Các giai đoạn

Mỗi giai đoạn kết thúc bằng `pytest` xanh.

### G0 — Dialect ✅ (16/09)

- `backend/gcs_backend/link_mav/drone_gcs.xml`: **bản sao y nguyên** của
  `drone-ros2-jazzy/docs/mavlink/drone_gcs.xml` (Pi đẩy lên 16/09). Bản nháp của GCS trước đó khớp
  từng trường **trừ hai chỗ Pi thêm** — xem bảng phát hiện bên dưới. Đồng bộ lại = chép đè rồi chạy
  `gen_dialect.py`; đừng sửa tay bản trong repo này.
- `gen_dialect.py` sinh `link_mav/dialect/drone_gcs.py` (tự chép `common.xml` cạnh XML, chạy UTF-8).
- `link_mav/tagmap.py`: `tagmap_crc` 14 byte/tag.
- `tests/test_mav_dialect.py`: **A13, A14, A15(a)** chạy xanh; vectơ `0x6BDEA0A6` khớp.

> `docs/GIAO_UOC_GCS_PI.md` và `drone_gcs.xml` trong repo này là **bản sao chỉ đọc** (mục 0.2); mọi
> sửa đổi làm ở bản gốc `drone-ros2-jazzy` rồi đồng bộ về. Pi đã chạy xong phép kiểm **10.A** phía họ
> (A1–A10, A12–A15 đạt; A11, A15b, A16, A17 chưa chạy) và làm xong việc 1–7 của mục 9.1; chỉ còn việc
> 8 (`PARAM_*`).

### G1 — Tầng liên kết `link_mav/` ✅ (16/09)

| Tệp | Nội dung | Giao ước |
|---|---|---|
| `transport.py` | UDP `asyncio.DatagramProtocol` bind `:14550`; địa chỉ đích = nguồn của gói hợp lệ gần nhất từ `(1, 191)` | 2.1 |
| `codec.py` | `pymavlink` MAVLink 2, chữ ký 32 byte (giữ `timestamp` để nạp lại), bỏ im lặng gói ngoài bảng sysid/compid | 2.2, 7.6 |
| `link.py` | `HEARTBEAT` 1 Hz; mất liên kết sau **5 s**; `rx_drop` theo khoảng trống `seq`; `TIMESYNC` 0,2 Hz → `rtt_ms`; phát/nhận `DRONE_LINK_STATS` 0,2 Hz; ghép đoạn `STATUSTEXT` | 5.1, 7.4, 7.5, 9.2 |

`tests/test_mav_link.py` — 7 test với cặp socket UDP loopback: đường về học từ gói hợp lệ đầu tiên,
**C5** (đổi cổng nguồn giữa chừng, gói cũ không còn nhận được), **C4** (ký sai và không ký đều bị bỏ,
`rx_bad_sig` tăng, **không cướp được đường về**), `rx_drop` đếm đúng khoảng trống `seq`, GCS trả lời
`TIMESYNC` của Pi, **A17** (ghép chuỗi 85 byte thành 2 đoạn, không mất phần cuối).

Ba chỗ quyết định khi viết, không suy thẳng ra được từ giao ước:

- **Buffer giải mã đặt lại mỗi datagram.** Với UDP, ranh giới datagram là ranh giới gói; giữ buffer
  qua các datagram thì một gói cụt làm hỏng gói của datagram sau.
- **`rx_bad_crc` đo được, không phải `UINT32_MAX`.** Giao ước 7.4 cho phép điền `UINT32_MAX` vì
  "pymavlink không tách được" — nhưng bật `robust_parsing` thì lỗi về dưới dạng chuỗi lý do, tách
  được CRC với chữ ký. Điền số thật đúng tinh thần R3 hơn.
- **`queue_depth` luôn 0.** GCS phát 1 gói/s lúc nghỉ nên không cần hàng đợi ưu tiên như Pi (5.3);
  gửi thẳng ra socket. Số 0 là sự thật, không phải chỗ trống chưa làm.

**Lỗi `seq` mà Pi tìm ra ở 10.A — phía GCS mắc đúng lỗi đó, đã sửa:** `msg.pack(mav)` của pymavlink
**không** tăng `seq`; việc đó nằm trong `send()` mà ta không dùng (nó ghi vào file, ta cần bytes cho
UDP). Hệ quả: mọi gói mang `seq = 0` và bên kia đếm `(0−0−1) & 0xFF = 255` gói mất **mỗi gói nhận
được** — `DRONE_LINK_STATS` nói dối ngay từ ngày đầu. `Codec.encode()` tự tăng, có test riêng.

### G2 — Dịch vụ ✅ (16/09)

| Tệp | Nội dung | Giao ước |
|---|---|---|
| `commands.py` | `COMMAND_LONG`, chờ `COMMAND_ACK` khớp `command`; thường 1,0 s × 3; khẩn 0,5 s vô hạn tới ACK hoặc huỷ; tăng `confirmation` | 4.1, 4.2 |
| `mission_client.py` | `COUNT` → đáp `REQUEST(seq)` → `ACK` (chờ tới khi Pi phán quyết); `COUNT` mới huỷ lượt cũ | 3.2, 3.3 |
| `params.py` | `PARAM_REQUEST_LIST` → 8 tham số 9.4, ánh xạ tên ↔ `system_config` | 9.4 |

`tests/test_mav_services.py` — 11 test: **A7** (`confirmation` 0/1/2 rồi báo người vận hành), lệnh
khẩn phát lại quá 3 lần **không bỏ cuộc** và báo động đúng một lần, `DISARM` mang `param2 = 21196`
và **không có đường nào arm**, **A6** (`UNSUPPORTED` là kết quả chứ không phải lỗi), **A2/A4/A5**
(nạp 2 điểm theo nhịp Pi, đáp lại mỗi `REQUEST` lặp, lý do từ chối giữ nguyên văn), gói lượt cũ đến
trễ không lẫn vào lượt mới, **A16** (đọc 8 tham số + đối chiếu `system_config`).

Ba điểm đáng ghi:

- **GCS không kiểm kế hoạch hợp lệ hay không** (1.1) — kể cả giới hạn 16 điểm. Bên duy nhất được từ
  chối là `mission_manager_node`, và lý do của nó là thứ người vận hành cần nhất.
- **Không có hàm ghi tham số.** `PARAM_SET` bị Pi bỏ qua (9.4); thiếu hàm đó là cố ý.
- **`compare()` trả `None` chứ không trả `False`** cho tham số chưa đọc được — "chưa biết" không phải
  "lệch", đúng R3.

### G3 — Telemetry → shadow ✅ (16/09)

`link_mav/telemetry.py` + 6 test. `LOCAL_POSITION_NED` ghép với `ATTITUDE` thành mẫu `fast`;
`DRONE_TELEMETRY` thành dict trạng thái. **Cờ hạ → trả `None`, không trả 0** — pin và GPS hiện luôn rơi
vào nhánh đó. Bước nhảy > 1 m được đánh dấu `jump` để frontend không nội suy qua. `item_alt_to_map()` là
chỗ DUY NHẤT quy đổi hai gốc của `alt_m`.

### G4 — Nghiệp vụ ✅ (16/09)

- `planner.py` viết lại: **một mục mỗi điểm dừng** (3.2b), tự thêm mục home cuối `action = NONE`, chặn
  quá 16 mục, bắt buộc mọi điểm dừng có `tag_id`. Bản cũ sinh 9 mục cho một chuyến A→B→home — mỗi mục
  thừa là một lần hạ cánh thật.
- `uploader.py` đổi vai: không còn giao thức 3 pha, chỉ còn `to_wire()` (mục CSDL → mục trên dây, đổi gốc
  `alt_m`) và `tags_yaml()`.
- `shadow.py`: `make_verifier` theo trạng thái — `DISARM` theo bit `ARMED` (`armed is False`, vì `None`
  nghĩa là mất đường FC chứ không phải đã tắt), `RTH`/`LAND`/`START`/`ABORT` theo `mission_state`.
- `runtime.py`: bỏ `cmd_simple`, `cmd_goto`, `resolve_goto_target`, `push_config`, `sync_tags`; thêm
  `contract_status()` (khoá soạn nhiệm vụ khi lệch MAJOR, lệnh khẩn vẫn đi), `start_warnings()`,
  `export_tags_yaml()`, đọc tham số chỉ-đọc khi nối lại.
- `monitor.py`: `evaluate_state` chịu được `pos = None` và `battery = None`; thêm cảnh báo
  **`BATT_UNAVAILABLE`** — ngưỡng pin trong `safety.yaml` trông như đang bảo vệ nhưng không bao giờ
  kích hoạt được (9.2, 9.4).
- REST: bỏ `/commands/simple`, `/commands/goto`, `/link/config`, `/tags/sync`; thêm `/tags/export`.
- `config.py`: `GCS_MAV_PORT`, `GCS_SIGNING` (**mặc định BẬT**), `GCS_SIGNING_KEY_FILE`. Bật mà thiếu
  khoá thì **dừng hẳn** kèm hướng dẫn — im lặng bay với kênh mở là tệ hơn.

### G5 — Pi giả lập ✅ (16/09)

`sim/fake_pi.py` thay `fake_drone.py`: UDP, gọi ra trước, bắt tay nạp kế hoạch có phát lại `REQUEST`,
sáu lệnh, `PARAM_*`, chia đoạn `STATUSTEXT`, và **`POS_VALID` chỉ bật sau khi leo qua 0,8 m** — nên đậu
trên đất là không có vị trí, đúng nhánh thường gặp của 5.2b. Tự tính `tagmap_crc` bằng đúng hàm của GCS.

`tests/test_api_e2e.py` viết lại trên UDP — 13 test, gồm: Pi gọi ra trước, không có vị trí khi đậu, pin/GPS
không bao giờ hiện số, bản đồ lệch thì chặn nạp, chuyến A→B→home đầy đủ, lệnh khẩn, ngưỡng chỉ đọc, xuất
`tags.yaml`, và **không còn đường nào để ARM hay GOTO**.

### G6 — Web ✅ (16/09)

`LinkDiagnostics` viết lại: tám bộ đếm 7.4 **hai phía**, `UINT32_MAX` hiện "—" chứ không hiện số; trạng thái
chữ ký gói. Bỏ `ManualControl.tsx` và `TagDetectRay` (AprilTag thô không qua kênh 4G). `EmergencyBar` còn
đúng bốn lệnh khẩn, KILL/HOLD biến mất. HUD bỏ ô pin, thêm ô "vị trí: KHÔNG CÓ". `RthHome` vẽ nhà RTH từ
`home_*`, không vẽ gì khi `HOME_VALID` = 0. Banner lệch MAJOR hợp đồng. Trang tag đổi từ "đồng bộ xuống
drone" thành "tải tags.yaml".

### G7 — Dọn ✅ (16/09)

Xoá `link/` (7 tệp), `fake_drone.py`, `test_link_codec.py`, `test_link_session.py`, `config_check.py`,
`ManualControl.tsx`, `pyserial`, `GCS_SESSION_KEY`, `GCS_LINK_URL`, `drone_peer_mac`. Cập nhật README và
`KIEN_TRUC_TRIEN_KHAI.md`; `thiet_ke_gcs_espnow_3d.md` và `DIEM_CAN_CHOT.md` đã gắn nhãn **LỖI THỜI**.

Còn sót có chủ ý: cột `drone.esp_peer_mac` trong CSDL (bỏ cột cần migration; điền `mavlink:<id>`).

### G8 — Liên thông với Pi thật 🔄 (16/09: đã nối, 10.B chạy được)

**Đã chạy thật, cùng LAN, Pi ở `192.168.10.138` và GCS ở `192.168.10.120`:**

| Hạng mục | Kết quả |
|---|---|
| Liên kết (A1) | Pi gọi ra trước, GCS học đường về `…:14551`; **40.814 gói, 0 mất, 0 sai chữ ký** |
| `contract_ver` | 400/400 — Pi đã nạp bản 0.4 |
| Nạp kế hoạch (A2) | `DRONE_MISSION_ACCEPTED`, bắt tay `COUNT → REQUEST → ITEM → ACK` đủ |
| Lệnh (A6/A7) | `ABORT_MISSION` → ACCEPTED + có hiệu lực; `RTH` lúc chưa bay → Pi trả `DENIED` |
| **10.B — bay trong Gazebo** | hai chuyến A→home xong `done/ok`; chuyến có `PICKUP`: 53,8 s, 28,6 m, gripper đóng đúng lúc |
| `POS_VALID` | **= 0 khi đậu**, đúng nhánh thường gặp của 5.2b — GCS hiện "chưa có vị trí" thay vì vẽ drone ở gốc |
| `home_*` | chốt đúng `[0, 0]` sau khi neo, `HOME_VALID` bật |

**Chưa chạy được vì Pi chưa hiện thực:** chữ ký gói (phải đặt `GCS_SIGNING=false`), `TIMESYNC`
(`rtt_ms` luôn rỗng), `PARAM_REQUEST_LIST` (A16). Còn lại: A11 (nghẽn), A17, và 10.C (4G thật).

**Lỗi do chính lần chạy này tìm ra — P30**, xem bảng dưới. Cùng loại với hai lỗi mà 10.A phía Pi đã
bắt: mã chạy đúng với giả lập của chính mình, sai với bên kia thật.

---


### G9 — Theo hợp đồng 0.5 ✅ (16/09)

Pi đã **merge nhánh 0.4 của GCS** và phân giải P30 ở bản 0.5. Phán quyết của Pi chỉ ra một chỗ đề
xuất (a) của GCS còn hở, và chỗ đó quan trọng hơn lỗi ban đầu:

> `MISSION_COMPLETE` là đích chung của **cả** huỷ lệnh, RTH, `NAV_LAND`, hết lượt thử **và** hết kế
> hoạch. Nếu Pi chỉ giữ trạng thái 9 lâu hơn và GCS đọc nó là "nhiệm vụ hoàn thành", thì **mọi lệnh
> huỷ đều được báo là thành công** — và một chuyến chưa bao giờ thấy tag (`RETRIES_EXHAUSTED`) trông
> y hệt chuyến trót lọt. Hiện tại GCS ít nhất còn *biết là mình không biết*; (a) trần sẽ biến nó
> thành *tin chắc vào điều sai*.

Pi làm (a) với `MISSION_COMPLETE_HOLD_S = 1.5` **cộng** trường mới `DRONE_TELEMETRY.flight_result`
(8.5b) — `uint8`, extension cuối, `CRC_EXTRA` của 42010 vẫn là **14** (GCS đã sinh mã lại và xác nhận).

Phía GCS đổi theo:

- `CONTRACT_VER` 400 → **500**; XML và bản sao giao ước đồng bộ từ `main` của repo Pi.
- `runtime.py`: bỏ hẳn suy đoán "xong mục cuối + IDLE = hoàn thành". Kết cục đọc từ `flight_result`:
  `COMPLETED` → done; `ABORTED`/`RTH`/`LANDED_CMD` → aborted; `FAILSAFE`/`RETRIES_EXHAUSTED` → failed.
  **`flight_result = 0` thì không kết luận gì** — 0 nghĩa là không biết (R3), không phải "chưa xong".
- `fake_pi.py`: giữ `MISSION_COMPLETE` 1,5 s, đặt `flight_result` theo **lý do đầu tiên** (hạ vì sự cố
  mà GCS bấm huỷ thì vẫn là `FAILSAFE` — ghi đè sẽ giấu mất sự cố), xoá đúng lúc `IDLE → TAKEOFF`.
- Web: bảng telemetry thêm dòng "Chuyến gần nhất", phân biệt rõ `RETRIES_EXHAUSTED` là **chưa làm
  được việc** dù vẫn đi qua `MISSION_COMPLETE`.
- Hai test e2e mới: chuyến `COMPLETED` kết thúc đúng, và **chuyến bị huỷ không được báo là thành công**.

**Một lỗi khác tìm ra khi làm chỗ này:** `Settings.signing_key_bytes` gọi `.strip()` lên khoá nhị
phân. Khoá 32 byte ngẫu nhiên có ~5% khả năng bắt đầu hoặc kết thúc bằng `0x0A`/`0x20`, và khi đó
strip() lặng lẽ cắt mất byte thật rồi báo "khoá sai độ dài" — hỏng đúng lúc sinh khoá mới. Đã sửa.

### G10 — Tab Drone: vị trí giả định + đồng bộ bản đồ tag 🔄 (a, c xong 16/09; b chờ Pi)

Hai việc rời nhau, cố ý tách vì một cái nằm trong tay GCS còn cái kia phải chờ Pi.

#### G10a — Vị trí giả định, CHỈ để vẽ (không lên dây) ✅

Khi `POS_VALID = 0` (lần khởi động đầu của Pi, trước lần neo đầu tiên), người vận hành chỉ định
drone đang đậu ở đâu để cảnh 3D không trống trơn. **Con số này không bao giờ rời khỏi GCS.**

Ba ràng buộc, và chúng là phần quan trọng nhất của tính năng:

1. **Không vào bất kỳ phép kiểm nào** — không kiểm vùng cấm, không tính khoảng cách tới `pad_home`
   trong `start_warnings()`, không dùng cho kiểm chứng lệnh. Nếu lỡ dùng, GCS sẽ kiểm chính con số
   người vận hành vừa gõ vào rồi báo kết quả như thể đó là số đo.
2. **Nhìn là biết không phải số đo** — drone vẽ mờ, nét đứt, kèm nhãn "vị trí giả định".
3. **Tự xoá khi drone neo** — `POS_VALID` bật thì bỏ ngay, nhảy sang vị trí thật (đã có cờ `jump`).

**Không gửi xuống drone**, và lý do đáng ghi lại: Pi chốt `home` của RTH ở tick đầu tiên có
`POS_VALID` trong lúc TAKEOFF (5.2b ý 4). Ép pose lúc drone còn trên đất làm `POS_VALID` bật sớm →
`home` chốt theo toạ độ gõ tay → **RTH bay về một điểm không tồn tại**. Đó đúng là lỗi P19 mà Pi vừa
sửa ở 0.3, chỉ khác nguồn gây ra.

Cờ neo là **latching**, nên vị trí giả định chỉ cần cho lần khởi động đầu của Pi, không phải trước
mỗi chuyến.

**Ràng buộc 1 được bảo đảm bằng kiến trúc, không bằng kỷ luật:** `assumedPos` sống trong store của
trình duyệt và **không tồn tại ở backend**. Mọi phép kiểm — vùng cấm, `start_warnings()`, kiểm chứng
lệnh — đều chạy ở backend, nên chúng không có cách nào chạm tới con số đó dù có muốn. Để nó ở backend
rồi "nhớ đừng dùng" là cách sẽ hỏng sau vài lần sửa.

Hiện thực: `store/live.ts` (`assumedPos`), `lib/ws.ts` (tự xoá khi `POS_VALID` bật),
`DroneObjects.tsx` (`AssumedDrone` — vòng nét đứt trên mặt đất, không thân, không cánh quay).

#### G10b — Đồng bộ bản đồ tag qua dây [ĐỀ XUẤT, chờ Pi]

Giao ước 8.6 để ngỏ: *"Nạp bản đồ qua dây để MINOR sau nếu cần"*. Đề xuất **P31** gửi Pi: bốn bản
tin `42005–42008` theo đúng hình dạng bắt tay của mục 3 (count → request → item → ack).

**Chưa viết code phía GCS cho phần này**: theo 6.4, cách điền từng trường phải chốt trong tài liệu
**trước khi** viết code, và 0.3 quy định [ĐỀ XUẤT] thì *"không"* được dựa vào để viết. Nếu Pi đổi số
hiệu hoặc đổi trường thì code viết sớm phải bỏ đi.

Phần GCS cần làm khi Pi chốt: `link_mav/tagmap_client.py` (đối xứng với `mission_client.py`), nút
"Đồng bộ xuống drone" trong tab Drone, và bỏ bước chép `tags.yaml` bằng tay khỏi hướng dẫn.

#### G10c — Tab Drone ✅

Danh sách drone đang liên lạc (dựng dạng nhiều dòng ngay từ đầu để lên đa drone không phải làm lại)
→ chọn → bảng trạng thái + bảng tag + nút đồng bộ. Chặn sang tab Nhiệm vụ khi CRC bản đồ lệch.

**Đa drone chưa làm được nếu không sửa giao ước**: Pi cố định `sysid = 1` (2.2), nên hai drone cùng
gửi tới một cổng sẽ giành đường về của nhau. Lối ra: mỗi drone một cổng UDP, hoặc sửa 2.2 cho phép
`sysid` khác nhau — việc phải bàn với Pi.

## 2. Điểm phát hiện khi viết dialect — cần đưa vào giao ước

| # | Mức | Vấn đề | Đề xuất |
|---|---|---|---|
| ~~P24~~ | — | `DRONE_MISSION_ITEM` không có `mission_id` | **Pi đã nhận và hiện thực**: XML bản gốc có `mission_id` (`uint32`) ở cả `ITEM` và `REQUEST`. Khép lại |
| **P28** | **Cao** | **XML và tài liệu giao ước lệch nhau.** XML bản gốc có hai trường mà mục 8.3/8.4 **không ghi**: `DRONE_MISSION_ITEM.mission_id` (trước `<extensions/>`) và `DRONE_MISSION_COUNT.contract_ver` (sau `<extensions/>`). Mục 0.2 cho XML thắng tài liệu nên GCS đã theo XML, nhưng ai đọc tài liệu để hiện thực sẽ viết sai | Nhập hai trường vào bảng 8.3 và 8.4, kèm ngữ nghĩa `contract_ver = 0` nghĩa là "GCS cũ chưa khai" (hiện chỉ ghi trong chú thích XML) |
| **P29** | **Cao** | **MINOR chưa tăng dù XML đã đổi.** 6.4 bước 4 buộc tăng MINOR ở cả XML lẫn bảng lịch sử khi sửa dialect; `contract_ver` vẫn là 300 ở cả hai bên. Vì `mission_id` nằm **trước** `<extensions/>`, nó đổi `CRC_EXTRA` của 42003: bên nào còn XML cũ sẽ **loại sạch mọi ITEM** mà `contract_ver` vẫn báo khớp — đúng kịch bản A14, nhưng không có gì cảnh báo | Tăng lên **0.4 → `contract_ver = 400`**, ghi một dòng vào bảng lịch sử. Đây chính là tình huống số phiên bản sinh ra để bắt |
| **P30** | **Cao** | **FSM của Pi không bao giờ đi qua `MISSION_COMPLETE`** (8.5 giá trị 9). Đo trên Gazebo 16/09 với `gcs_link_node` thật: telemetry cả chuyến chỉ mang 0..5, xong mục cuối là về thẳng `IDLE`. GCS chờ trạng thái đó nên nhiệm vụ kẹt `running` vĩnh viễn dù drone đã hạ cánh và disarm | Hoặc Pi giữ `MISSION_COMPLETE` ít nhất một chu kỳ 2 Hz để GCS không lỡ, hoặc 8.5 ghi thẳng rằng trạng thái 9 **không được dùng** và định nghĩa "xong" = về `IDLE` sau mục cuối. GCS đã sửa theo cách thứ hai để dùng được ngay |
| P25 | Thấp | Lệnh sinh mã ở 7.1 **không chạy được** với pymavlink cài bằng pip: không có `pymavlink.tools`; `<include>common.xml</include>` tìm cạnh tệp XML; Windows đọc XML bằng cp1252 | Ghi đúng cách sinh (chép `common/standard/minimal.xml` cạnh XML hoặc để sẵn trong `docs/mavlink/`), **ghim cùng một phiên bản pymavlink** hai bên (GCS đang dùng 2.4.49), chạy `python -X utf8` |
| P26 | Thấp | pymavlink giải mã `char[]` bằng **ASCII** → `plan_name` tiếng Việt thành `L���y` (không lỗi, nhưng rác) | Ghi rõ: `plan_name`, `reason`, `STATUSTEXT` là **ASCII không dấu**; GCS bỏ dấu trước khi gửi |
| P27 | Thấp | Trường sau `<extensions/>` mà bên gửi cũ không có thì bên nhận thấy **0** — nhưng `expected_marker_id` = 0 là tag 0 thật, `tagmap_crc` = 0 trông như một CRC | Quy tắc cho mọi trường extension về sau: **0 phải có nghĩa "không biết"** hoặc có bit hiệu lực đi kèm. Bốn trường của 0.3 thì hai bên đều gửi từ đầu nên chưa gặp |
| — | Ghi chú | `42000`/`42001` trùng `ICAROUS_*` trong `icarous.xml` (được `ardupilotmega.xml` include) | Không sửa. Chỉ nhớ: mở dump bằng công cụ dùng dialect ArduPilot sẽ giải mã sai 42001 |
