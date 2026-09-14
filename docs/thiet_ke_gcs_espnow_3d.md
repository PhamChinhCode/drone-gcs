# Đặc tả Thiết kế Phần mềm Trạm Điều khiển Mặt đất (GCS) — Liên kết ESP-NOW, Giám sát 3D

*Tài liệu đặc tả thi công — biên soạn 14/09/2026.
Đối tượng đọc: đội phát triển phần mềm GCS (backend + frontend), đội firmware ESP32, đội ROS 2 trên Pi 4.
Tài liệu này đặc tả **mới** phần GCS và tầng truyền thông ESP-NOW; nó **không thay thế**
`docs/GIAO_UOC_FC_ROS2.md` (hợp đồng Pi 4 ↔ STM32H743) mà nằm **chồng lên trên** hợp đồng đó.
Ranh giới: tài liệu này dừng lại ở topic ROS 2 của `gcs_link_node`; từ đó trở xuống là hợp đồng FC.*

---

## 0. Tóm tắt điều hành và các quyết định đã chốt

Hệ thống cần một trạm mặt đất cho phép người vận hành ra lệnh nhiệm vụ cấp cao ("lấy hàng tại tag A,
giao tại tag B, về Home"), điều khiển tay ở mức vị trí (bay tới tọa độ, cất/hạ cánh tương đối với tag),
và giám sát toàn bộ quá trình trong một khung cảnh 3D thời gian thực. Liên kết vô tuyến dùng ESP-NOW.

Bảy quyết định kiến trúc đã chốt trước khi thi công:

| # | Quyết định | Lý do |
|---|---|---|
| 1 | **Cầu nối ESP-NOW bằng hai module ESP32**: một cắm USB vào máy GCS, một gắn UART vào Pi 4 | Pi 4 dùng chip WiFi Broadcom/Cypress, **không** hỗ trợ ESP-NOW. Đây là cách duy nhất chạy được mà không đổi máy tính nhúng |
| 2 | **ESP-NOW chỉ tải lệnh + telemetry gọn nhẹ**; video và tải log đi kênh WiFi riêng, **bổ sung ở giai đoạn sau**, không thuộc phạm vi đợt triển khai này | ESP-NOW tối đa 250 byte/gói, không phân mảnh, không thể tải video |
| 3 | **Khung cảnh 3D dựng hoàn toàn từ telemetry**, không phụ thuộc video | Video là kênh best-effort có thể mất; 3D phải luôn hoạt động vì nó là công cụ giám sát chính |
| 4 | **Stack GCS: frontend React + Three.js, backend Python (FastAPI + asyncio)** | Backend Python dùng chung hệ sinh thái với ROS 2/pyserial; frontend web mở đường cho truy cập từ xa sau này |
| 5 | **`gcs_link_node` phía Pi 4 giữ nguyên vai trò và topic**, chỉ đổi tầng vận chuyển từ UDP/4G sang UART → ESP32 → ESP-NOW | Giữ nguyên kiến trúc 16 node đã dựng; thay đổi khu trú trong một node |
| 6 | **Giao thức nhị phân tự định nghĩa**, không dùng MAVLink nguyên bản trên ESP-NOW | MAVLink v2 tối thiểu 12 byte overhead + không có cơ chế phân mảnh phù hợp 250 byte; giao thức riêng gọn hơn 40 % và kiểm soát được ưu tiên |
| 7 | **Drone là nguồn sự thật duy nhất về trạng thái**; GCS chỉ giữ bản sao bóng (shadow state) | Tránh hai bên cùng tin mình đúng khi mất gói |

Ba nguyên tắc bất di bất dịch xuyên suốt tài liệu: **(a)** ESP-NOW không bao giờ là thứ duy nhất ngăn
drone rơi — failsafe tầng FC phải độc lập hoàn toàn với liên kết này; **(b)** không lệnh nguy hiểm nào
được thực thi chỉ dựa trên một gói tin — phải có ACK và phải có xác nhận trạng thái phản hồi; **(c)**
mọi ngưỡng an toàn cấu hình ở GCS phải khớp với ngưỡng phía Pi 4, và hệ thống phải **tự kiểm tra** sự
khớp đó mỗi khi nối lại liên kết.

---

## 1. Phân tích yêu cầu

### 1.1 Yêu cầu chức năng

Nhóm **F1 — Quản lý bản đồ tag**: khai báo, sửa, xóa các tag trên mặt phẳng hoạt động; mỗi tag có ID,
nhãn, tọa độ (N, E, D) trong hệ quy chiếu cục bộ, góc hướng, kích thước in thật, loại điểm (Home / điểm
lấy / điểm giao / điểm trung chuyển), bán kính dung sai hạ cánh. Hỗ trợ nhập tọa độ bằng tay hoặc
"dạy" bằng cách bay drone tới từng tag ở chế độ thủ công rồi ghi lại vị trí. Xuất file in tag kèm ID.
Đồng bộ bản đồ tag xuống drone và kiểm tra checksum hai bên khớp nhau.

Nhóm **F2 — Điều khiển tay ở mức vị trí**: arm/disarm; cất cánh tới độ cao đặt trước; bay tới một tọa
độ trong hệ bản đồ; bay tới vị trí **tương đối với một tag** (ví dụ "lên thẳng phía trên tag 5, cao
3 m"); giữ vị trí; hạ cánh thường; hạ cánh chính xác bám tag; quay về Home. Mọi lệnh đều có ACK và có
thể hủy.

Nhóm **F3 — Điều khiển nhiệm vụ**: tạo nhiệm vụ từ cặp (tag lấy, tag giao) hoặc từ chuỗi waypoint tùy
biến; xem trước tuyến bay trong 3D trước khi gửi; tải nhiệm vụ lên drone và xác nhận nhận đủ; bắt đầu /
tạm dừng / tiếp tục / hủy nhiệm vụ; hàng đợi nhiều nhiệm vụ với thứ tự ưu tiên sửa được.

Nhóm **F4 — Giám sát 3D thời gian thực**: dựng mặt phẳng hoạt động với toàn bộ tag đúng tọa độ; mô hình
drone chuyển động theo vị trí và tư thế thật; vệt quỹ đạo đã bay; tuyến bay dự kiến còn lại; hình nón
tầm nhìn camera; ô đích và hành lang tiếp cận khi hạ cánh; trạng thái gắp/thả hiển thị bằng khối hàng
gắn dưới drone; lớp phủ HUD (pin, độ cao, tốc độ, trạng thái FSM, chất lượng liên kết). Xoay/zoom/pan
tự do, có các góc nhìn đặt sẵn (nhìn từ trên, nhìn theo drone, nhìn từ tag đích).

Nhóm **F5 — Cảnh báo và can thiệp khẩn cấp**: thanh lệnh khẩn cấp luôn hiển thị với ba nút RTH / hạ
cánh ngay / hủy nhiệm vụ; cảnh báo chủ động theo ngưỡng; nhật ký cảnh báo.

Nhóm **F6 — Lịch sử và phát lại**: lưu toàn bộ telemetry và sự kiện của mỗi nhiệm vụ; màn hình phát lại
dùng **chính khung cảnh 3D** với thanh tua thời gian; xuất báo cáo nhiệm vụ.

Nhóm **F7 — Quản trị**: người dùng và phân quyền hai cấp; cấu hình ngưỡng an toàn; chẩn đoán liên kết
(RSSI, tỉ lệ mất gói, RTT); cấu hình kênh và peer ESP-NOW.

Nhóm **F8 — Thiết kế khu vực hoạt động**: một tab riêng để dựng sơ đồ mặt bằng trước khi vận hành —
đặt và kéo thả tag trực tiếp trên sơ đồ, đặt điểm Home, vẽ đa giác biên vùng bay và các vùng cấm bay,
đo khoảng cách giữa hai điểm bất kỳ, phủ ảnh nền mặt bằng để căn theo hiện trường, và phủ vòng tròn bán
kính liên kết đo được để thấy ngay tag nào nằm ngoài tầm sóng an toàn. Có bộ kiểm tra tự động báo lỗi
thiết kế (tag trùng ID, tag ngoài biên, hai tag quá gần nhau, thiếu Home) và xuất được sơ đồ cùng tờ in
tag. F8 là nơi **sinh ra** dữ liệu mà F1 quản lý ở dạng bảng — hai màn hình cùng thao tác trên một bộ
dữ liệu, khác nhau ở cách trình bày. Đặc tả chi tiết ở mục 8.4.

### 1.2 Yêu cầu phi chức năng

| Mã | Yêu cầu | Chỉ tiêu |
|---|---|---|
| N1 | Độ trễ lệnh khẩn cấp (bấm nút → drone nhận và ACK) | ≤ 300 ms ở 95 % số lần, ≤ 500 ms ở 99 % |
| N2 | Tần số cập nhật tư thế trong 3D | ≥ 10 Hz dữ liệu, ≥ 50 fps dựng hình |
| N3 | Phát hiện mất liên kết | ≤ 3 s ở cả hai phía |
| N4 | Tỉ lệ nhận gói (PDR) trong bán kính vận hành | ≥ 98 % với telemetry, 100 % với lệnh (sau retry) |
| N5 | Khôi phục sau khi GCS khởi động lại | Nạp lại trạng thái nhiệm vụ đang bay trong ≤ 5 s |
| N6 | Bảo mật liên kết | Mã hóa CCMP tầng ESP-NOW + chống phát lại (replay) tầng ứng dụng |
| N7 | Dung lượng lưu trữ | ≥ 90 ngày telemetry ở 10 Hz cho 1 drone (≈ 2,5 GB/năm với nén) |

### 1.3 Ràng buộc kỹ thuật của ESP-NOW (đã kiểm chứng từ tài liệu Espressif)

Đây là các con số **quyết định thiết kế giao thức**, phải đọc trước khi viết dòng code nào:

| Thuộc tính | Giá trị | Hệ quả thiết kế |
|---|---|---|
| Payload tối đa | **250 byte** (ESP-NOW v1). v2 hỗ trợ 1470 byte nhưng cần ESP-IDF ≥ 5.4 ở **cả hai đầu** | Chốt dùng v1/250 byte làm mẫu số chung. Mọi bản tin phải ≤ 240 byte sau header |
| Số peer tối đa | 20 | Đủ xa cho mở rộng đa drone |
| Số peer mã hóa | mặc định 7, cấu hình tối đa 17 | Không phải ràng buộc với 1–5 drone |
| Mã hóa | CCMP; PMK 16 byte (mã hóa LMK), LMK 16 byte/peer (mã hóa dữ liệu) | Phải nạp khóa lúc ghép đôi; **gói broadcast không mã hóa được** |
| ACK | Có ACK tầng MAC; callback trả `ESP_NOW_SEND_SUCCESS`. Tài liệu nói rõ **không đảm bảo tầng ứng dụng nhận được** | Bắt buộc tự làm ACK tầng ứng dụng cho mọi lệnh |
| Kênh | 0–14; 0 = kênh hiện tại, khác 0 phải **trùng kênh peer** | Chốt cứng một kênh, ghi vào cấu hình cả hai đầu |
| Tốc độ | mặc định 1 Mbps, chỉnh được qua `esp_now_set_peer_rate_config()` | Giữ 1 Mbps (bền hơn); LR mode nếu cần tầm xa |
| Thông lượng thực đo | ~214 kbps môi trường mở | Ngân sách 5,6 kbps của ta dư thừa ~38 lần |

Hai hệ quả phải nhắc riêng. **Thứ nhất**, không thể truyền video hay tải log qua ESP-NOW — đó là lý do
quyết định số 2. **Thứ hai**, ESP-NOW dùng băng 2,4 GHz; kênh video đã chốt dùng WiFi, và nếu WiFi đó
chạy ở 2,4 GHz trên Pi 4 thì hai kênh **tự nhiễu lẫn nhau**. Bắt buộc: khi bổ sung kênh video ở giai
đoạn sau, đặt nó ở WiFi **5 GHz**, và kênh ESP-NOW cố định ở kênh cách xa kênh AP đang dùng (khuyến
nghị ESP-NOW kênh 1, AP video 5 GHz). Ràng buộc này phải được tôn trọng **ngay từ khi chọn phần cứng
WiFi cho Pi 4**, không phải lúc bắt đầu làm video — đổi module WiFi muộn tốn hơn nhiều.

### 1.4 Giả định

Khu vực hoạt động là mặt phẳng đã khảo sát, mọi tag đồng phẳng (sai lệch độ cao < 5 cm), tọa độ tag đã
biết trước khi bay. Tầm hoạt động thiết kế ≤ 300 m tầm nhìn thẳng (vượt quá cần bật LR mode và kiểm
chứng lại PDR). Một drone vận hành tại một thời điểm, nhưng mọi cấu trúc dữ liệu và bản tin đều mang
`drone_id` ngay từ đầu.

---

## 2. Kiến trúc tổng thể

### 2.1 Sơ đồ khối

```
┌───────────────────────── MÁY TRẠM MẶT ĐẤT (PC) ────────────────────────┐
│                                                                        │
│  ┌─── Frontend (trình duyệt / Electron) ──────────────────────────┐    │
│  │  React + Three.js                                              │    │
│  │  ├── Khung cảnh 3D          ├── Tạo nhiệm vụ                   │    │
│  │  ├── HUD + thanh khẩn cấp   ├── Quản lý tag                    │    │
│  │  └── Lịch sử / phát lại     └── Quản trị + chẩn đoán liên kết  │    │
│  └──────────────▲──────────────────────────────┬──────────────────┘    │
│         WebSocket (đẩy telemetry)        REST (CRUD, lệnh)             │
│  ┌──────────────┴──────────────────────────────▼──────────────────┐    │
│  │  Backend Python — FastAPI + asyncio                            │    │
│  │  ┌──────────┐ ┌───────────┐ ┌────────┐ ┌────────┐ ┌─────────┐  │    │
│  │  │ link/    │ │ mission/  │ │ safety/│ │ data/  │ │ api/    │  │    │
│  │  │ khung,   │ │ lập kế    │ │ ngưỡng,│ │ CSDL,  │ │ REST +  │  │    │
│  │  │ ACK,     │ │ hoạch,    │ │ cảnh   │ │ repo   │ │ WS,     │  │    │
│  │  │ retry,   │ │ tải lên,  │ │ báo    │ │        │ │ auth    │  │    │
│  │  │ hàng đợi │ │ shadow FSM│ │        │ │        │ │         │  │    │
│  │  └────▲─────┘ └───────────┘ └────────┘ └────────┘ └─────────┘  │    │
│  └───────┼────────────────────────────────────────────────────────┘    │
│          │ USB CDC serial, 921600 8N1, khung COBS                      │
│  ┌───────▼────────────────┐                                            │
│  │ ESP32-GCS (dongle USB) │  cầu nối serial ↔ ESP-NOW, không có logic  │
│  └───────┬────────────────┘  nghiệp vụ, chỉ đệm + thống kê liên kết    │
└──────────┼─────────────────────────────────────────────────────────────┘
           │ ESP-NOW 2,4 GHz, kênh cố định, CCMP, unicast
┌──────────▼─────────────────────── DRONE ───────────────────────────────┐
│  ┌────────────────────────┐                                            │
│  │ ESP32-AIR              │  cầu nối ESP-NOW ↔ UART                    │
│  └───────┬────────────────┘                                            │
│          │ UART 921600 8N1, khung COBS (giống hệt phía GCS)            │
│  ┌───────▼──────────────────────────────────────────────────────────┐  │
│  │ Raspberry Pi 4 — ROS 2 Jazzy                                     │  │
│  │   gcs_link_node  ◄── node DUY NHẤT chạm tầng truyền thông này    │  │
│  │      ├─ ra:  /mission/plan, /gcs_link/connected, /fc/command_req │  │
│  │      └─ vào: /telemetry/outgoing, /mission/state, /failsafe_event│  │
│  │   … 15 node còn lại giữ nguyên theo ke_hoach_thiet_ke_node.md    │  │
│  └───────┬──────────────────────────────────────────────────────────┘  │
│          │ UART MAVLink — theo GIAO_UOC_FC_ROS2.md (KHÔNG đổi)         │
│  ┌───────▼────────────┐                                                │
│  │ STM32H743 — FC     │                                                │
│  └────────────────────┘                                                │
│                                                                        │
│  Kênh phụ (độc lập, BỔ SUNG SAU): WiFi 5 GHz → video, tải log          │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Bảng phân vai — tra khi có tranh cãi về "việc này ai làm"

| Việc | Làm ở đâu | Không làm ở đâu |
|---|---|---|
| Ổn định bay, giữ góc, mixing động cơ | FC (STM32H743) | Không bao giờ ở Pi 4 hay GCS |
| Vòng điều khiển vị trí, hạ cánh chính xác bám tag | Pi 4 (`position_controller_node`) | Không ở GCS — độ trễ vô tuyến giết chết vòng kín |
| Máy trạng thái nhiệm vụ **có thẩm quyền** | Pi 4 (`mission_manager_node`) | GCS chỉ giữ **bản sao bóng** để hiển thị |
| Quyết định leo thang failsafe | Pi 4 (`failsafe_monitor_node`) + FC (tầng thấp) | GCS chỉ **đề nghị**, không phải nguồn quyết định |
| Lưu bản đồ tag gốc, lập kế hoạch nhiệm vụ | GCS | Drone chỉ giữ bản sao nhiệm vụ đang chạy |
| Lịch sử, báo cáo, phát lại | GCS | — |
| Đóng/mở khung gói ESP-NOW | ESP32 hai đầu (chỉ đóng gói, không diễn giải) | ESP32 **không** được có logic nghiệp vụ |

Nguyên tắc đặt ESP32 làm "cầu câm" là có chủ đích: firmware ESP32 không biết waypoint hay nhiệm vụ là
gì, nó chỉ chuyển byte và báo cáo chất lượng liên kết. Thêm một bản tin mới vào giao thức **không cần**
nạp lại firmware ESP32.

---

## 3. Hệ quy chiếu và mô hình bản đồ tag

### 3.1 Định nghĩa hệ quy chiếu (chốt một lần, mọi tầng theo)

Hệ bản đồ `map` là **NED cục bộ**: gốc đặt tại tâm tag Home, trục X hướng Bắc, Y hướng Đông, Z hướng
**xuống**. Đây là lựa chọn bắt buộc để khớp với `GIAO_UOC_FC_ROS2.md` (trên dây là FRD/NED). Độ cao bay
5 m nghĩa là `pos_d = −5.0`.

Mặt phẳng chứa tag là mặt `D = 0` (hoặc `D = d_tag` nếu bãi đáp không cùng cao độ với Home — trường
này vẫn lưu cho mỗi tag). Góc hướng tag `yaw_deg` đo từ Bắc, chiều kim đồng hồ nhìn từ trên xuống.

Toàn bộ đơn vị **trên dây** là số nguyên: khoảng cách tính bằng **milimét**, vận tốc bằng **cm/s**, góc
bằng **centi-độ**. Toàn bộ đơn vị **trong CSDL và API REST** là số thực SI: mét, m/s, độ. Việc quy đổi
chỉ xảy ra ở đúng một chỗ — module `link/messages.py` — và phải có kiểm thử đơn vị cho từng chiều.

### 3.2 Quy đổi sang hệ tọa độ Three.js — phần dễ sai nhất của giao diện 3D

Three.js dùng hệ thuận tay phải, Y hướng lên. Ánh xạ chốt:

```
x_three =  pos_e        (Đông)
y_three = −pos_d        (Lên)
z_three = −pos_n        (ngược hướng Bắc)
```

Kiểm tra tay thuận: `(E, U, −N)` là hoán vị có một lần đổi dấu từ `(E, N, U)` → vẫn thuận tay phải. Nếu
đội nào đó dùng `z_three = +pos_n` thì cảnh 3D sẽ **lật gương** — bay sang phải hiện thành sang trái,
và lỗi này rất khó phát hiện bằng mắt vì cảnh vẫn "trông hợp lý".

Với tư thế, cách an toàn nhất là đổi cơ sở bằng ma trận thay vì mò góc Euler:

```js
// coords.ts — ma trận đổi cơ sở dùng chung cho cả vị trí lẫn tư thế
// M biến vector NED thành vector Three, và cũng biến trục thân FRD
// thành trục mô hình (phải, lên, lùi) — trùng nhau, không phải trùng hợp.
const M = new THREE.Matrix4().set(
   0, 1,  0, 0,
   0, 0, -1, 0,
  -1, 0,  0, 0,
   0, 0,  0, 1,
);
const Mt = M.clone().transpose();

/** roll/pitch/yaw (radian, quy ước hàng không ZYX, NED/FRD) → quaternion Three */
export function attitudeToThree(roll, pitch, yaw) {
  const cr = Math.cos(roll),  sr = Math.sin(roll);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const cy = Math.cos(yaw),   sy = Math.sin(yaw);
  // R_ned_from_body, ZYX
  const R = new THREE.Matrix4().set(
    cp*cy, sr*sp*cy - cr*sy, cr*sp*cy + sr*sy, 0,
    cp*sy, sr*sp*sy + cr*cy, cr*sp*sy - sr*cy, 0,
    -sp,   sr*cp,            cr*cp,            0,
    0,     0,                0,                1,
  );
  const Rthree = new THREE.Matrix4().multiplyMatrices(M, R).multiply(Mt);
  return new THREE.Quaternion().setFromRotationMatrix(Rthree);
}
```

**Bảng kiểm chứng bắt buộc viết thành unit test** — mô hình drone quy ước mũi hướng `−Z`, cánh phải
`+X`, nóc `+Y`:

| roll | pitch | yaw | Mũi drone phải chỉ về | Nóc phải chỉ về |
|---|---|---|---|---|
| 0° | 0° | 0° (Bắc) | `(0, 0, −1)` | `(0, 1, 0)` |
| 0° | 0° | 90° (Đông) | `(1, 0, 0)` | `(0, 1, 0)` |
| 0° | 0° | 180° (Nam) | `(0, 0, 1)` | `(0, 1, 0)` |
| 0° | +15° (ngóc lên) | 0° | `(0, +0.26, −0.97)` | nghiêng về sau |
| +20° (nghiêng phải) | 0° | 0° | `(0, 0, −1)` | `(0.34, 0.94, 0)` |

Test này phát hiện 90 % lỗi hệ trục trước khi ai đó nhìn thấy cảnh 3D.

### 3.3 Mô hình bản đồ tag và checksum đồng bộ

GCS là nguồn sự thật của bản đồ tag. Drone giữ một bản sao để xác thực tag khi hạ cánh. Để tránh tình
huống hai bên lệch bản đồ (cực nguy hiểm: drone hạ xuống tọa độ cũ của một tag đã bị dời), mỗi bản đồ có
một **checksum** tính như sau và so khớp mỗi lần nối lại liên kết:

```
map_crc = CRC32 over, for each tag sorted by tag_id ascending:
          (uint16 tag_id, int32 n_mm, int32 e_mm, int32 d_mm,
           int16 yaw_cdeg, uint16 size_mm, uint8 kind)
```

Nếu `map_crc` hai bên khác nhau, GCS **chặn mọi lệnh nhiệm vụ** và hiện banner đỏ yêu cầu đồng bộ lại.
Lệnh điều khiển tay và lệnh khẩn cấp vẫn cho phép (để còn gọi drone về được).

---

## 4. Tầng vật lý và cấu hình ESP-NOW

### 4.1 Phần cứng

| Vị trí | Thiết bị | Kết nối | Ghi chú |
|---|---|---|---|
| Máy GCS | ESP32-S3 DevKit (hoặc ESP32-WROOM) | USB CDC, 921600 8N1 | Dùng chip có USB gốc (S3/C3) để tránh cầu USB-UART làm nghẽn |
| Drone | ESP32-WROOM-32 hoặc ESP32-C3 mini | UART Pi 4 (GPIO14/15), 921600 8N1, chung GND | Cấp nguồn từ BEC 5 V riêng, **không** lấy từ chân 5 V của Pi khi Pi đã tải nặng |
| Ăng-ten | Loại có đầu nối IPEX + ăng-ten ngoài 2 dBi | — | Bản PCB antenna chỉ đủ cho bàn test; bay thật phải dùng ăng-ten ngoài, đặt cách xa PDB và dây động cơ ≥ 10 cm |

Lưu ý lắp đặt: ăng-ten ESP32 trên drone đặt thẳng đứng, tránh song song với ăng-ten GPS và tránh nằm
dưới thân carbon (carbon chắn sóng gần như hoàn toàn).

### 4.2 Cấu hình firmware ESP32 (giống nhau hai đầu, khác vai trò)

```c
// Cấu hình chốt — ghi vào NVS, có thể đổi qua bản tin điều khiển dongle
#define LINK_CHANNEL        1          // cố định, KHÔNG để 0
#define LINK_RATE           WIFI_PHY_RATE_1M_L   // bền hơn tốc độ cao
#define LINK_LR_MODE        0          // 1 = bật WIFI_PROTOCOL_LR (phải bật CẢ HAI đầu)
#define LINK_UART_BAUD      921600
#define LINK_TX_QUEUE_DEPTH 32

static const uint8_t PMK[16] = { /* nạp lúc sản xuất, không hardcode trong git */ };
static const uint8_t LMK[16] = { /* mỗi cặp một khóa */ };

void link_init(const uint8_t peer_mac[6]) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
#if LINK_LR_MODE
    ESP_ERROR_CHECK(esp_wifi_set_protocol(WIFI_IF_STA,
        WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR));
#endif
    ESP_ERROR_CHECK(esp_wifi_set_channel(LINK_CHANNEL, WIFI_SECOND_CHAN_NONE));
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk(PMK));
    ESP_ERROR_CHECK(esp_now_register_send_cb(on_send_done));
    ESP_ERROR_CHECK(esp_now_register_recv_cb(on_recv));

    esp_now_peer_info_t peer = { .channel = LINK_CHANNEL, .ifidx = WIFI_IF_STA, .encrypt = true };
    memcpy(peer.peer_addr, peer_mac, 6);
    memcpy(peer.lmk, LMK, 16);
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    esp_now_rate_config_t rate = { .phymode = WIFI_PHY_MODE_11G, .rate = LINK_RATE };
    esp_now_set_peer_rate_config(peer.peer_addr, &rate);
}
```

Ba điều firmware ESP32 **bắt buộc** làm ngoài việc chuyển byte:

1. **Đếm và báo cáo thống kê liên kết**: số gói gửi, số gói `ESP_NOW_SEND_SUCCESS`, số gói thất bại,
   RSSI gói nhận gần nhất (lấy từ `esp_now_recv_info_t->rx_ctrl->rssi`), gửi lên host mỗi 1 s bằng bản
   tin điều khiển dongle. Không có số này thì không chẩn đoán được sự cố tầm xa.
2. **Đệm có ưu tiên**: hàng đợi TX phân 2 mức, gói có cờ ưu tiên ≥ 2 (lệnh/khẩn cấp) chèn lên đầu. Khi
   hàng đợi đầy, **loại bỏ gói telemetry cũ nhất**, không bao giờ loại gói lệnh.
3. **Không tự ý sửa payload**: ESP32 không tính lại CRC, không đổi seq. CRC do hai đầu ứng dụng tính và
   kiểm — như vậy lỗi hỏng bit trong chính ESP32 hoặc trên dây UART cũng bị bắt.

### 4.3 Ghép đôi và bảo mật

Khóa PMK/LMK nạp một lần khi sản xuất, lưu trong NVS đã bật flash encryption, **không** đưa vào kho mã
nguồn. MAC của peer khai bằng tay trong cấu hình (không dùng broadcast discovery khi vận hành thật, vì
gói broadcast không mã hóa được).

CCMP chống nghe lén và giả mạo, nhưng **không chống phát lại** ở tầng ứng dụng. Vì vậy mọi bản tin thuộc
lớp lệnh (ưu tiên ≥ 2) mang thêm 4 byte `auth_tag` = 4 byte đầu của `HMAC-SHA256(session_key, header ||
payload)`, trong đó `session_key` sinh mới mỗi phiên qua bản tin bắt tay. Kèm theo đó `seq` phải tăng
nghiêm ngặt: bên nhận từ chối mọi gói lệnh có `seq` nhỏ hơn hoặc bằng `seq` lệnh hợp lệ gần nhất
(cửa sổ 64 gói cho phép đảo thứ tự nhẹ).

---

## 5. Giao thức truyền thông

### 5.1 Ba lớp đóng gói

```
┌─ Lớp 3: Bản tin ứng dụng ────────────────────────────────┐
│  link_hdr_t (8B) │ payload (0–230B) │ auth_tag (0/4B) │ crc16 (2B)
└──────────────────────────────────────────────────────────┘
                 ▲ đơn vị này đi nguyên vẹn qua cả UART lẫn ESP-NOW
┌─ Lớp 2: Khung UART (PC↔ESP32, ESP32↔Pi4) ────────────────┐
│  COBS( chan_id (1B) │ <đơn vị lớp 3> ) │ 0x00
└──────────────────────────────────────────────────────────┘
┌─ Lớp 1: ESP-NOW ─────────────────────────────────────────┐
│  esp_now_send(peer, <đơn vị lớp 3>, len)   — tối đa 250B
└──────────────────────────────────────────────────────────┘
```

Dùng **COBS** (Consistent Overhead Byte Stuffing) cho khung UART thay vì ký tự thoát kiểu SLIP: overhead
xác định (1 byte/254 byte), không có trường hợp biên nhập nhằng, và đồng bộ lại sau nhiễu chỉ mất đúng
một khung. Byte `0x00` là dấu kết khung và không bao giờ xuất hiện trong thân khung.

`chan_id`: `0x01` = payload đi/đến qua ESP-NOW; `0x02` = điều khiển/thống kê dongle (không phát ra
không khí); `0x03` = log gỡ lỗi dạng text từ ESP32.

### 5.2 Header chung

```c
typedef struct __attribute__((packed)) {
    uint8_t  magic;      // 0xD5
    uint8_t  flags;      // xem bảng dưới
    uint8_t  msg_id;     // mã bản tin
    uint8_t  node;       // bit7-4: node nguồn, bit3-0: node đích
    uint16_t seq;        // little-endian, tăng theo từng nguồn
    uint16_t len;        // độ dài payload, KHÔNG kể header/auth/crc
} link_hdr_t;            // 8 byte
```

Bảng bit của `flags`:

| Bit | Tên | Ý nghĩa |
|---|---|---|
| 7–6 | VERSION | Phiên bản giao thức, hiện tại `0b01` |
| 5 | NEED_ACK | Bên nhận phải trả ACK |
| 4 | IS_ACK | Bản tin này là ACK |
| 3–2 | PRIORITY | 0 = telemetry, 1 = thường, 2 = lệnh, 3 = khẩn cấp |
| 1 | HAS_AUTH | Có 4 byte `auth_tag` trước CRC |
| 0 | FRAG | Thuộc một chuỗi phân mảnh |

Mã node: `0x1` = GCS backend, `0x2` = ESP32-GCS, `0x3` = ESP32-AIR, `0x4` = Pi 4, `0xF` = broadcast.

CRC16-CCITT-FALSE (đa thức 0x1021, khởi tạo 0xFFFF) tính trên `header || payload || auth_tag`.

### 5.3 Bảng bản tin

**Chiều lên (GCS → drone), `msg_id` 0x10–0x3F:**

| ID | Tên | Ưu tiên | ACK | Tần số | Kích thước |
|---|---|---|---|---|---|
| 0x10 | `HEARTBEAT_GCS` | 1 | không | 1 Hz | 12 B |
| 0x11 | `CMD_SIMPLE` | 2 | có | sự kiện | 6 B |
| 0x12 | `CMD_GOTO` | 2 | có | sự kiện | 22 B |
| 0x13 | `MISSION_BEGIN` | 2 | có | sự kiện | 12 B |
| 0x14 | `MISSION_WP` | 2 | có | sự kiện | 28 B |
| 0x15 | `MISSION_END` | 2 | có | sự kiện | 8 B |
| 0x16 | `MISSION_CTRL` | 2 | có | sự kiện | 6 B |
| 0x17 | `EMERGENCY` | 3 | có | sự kiện | 8 B |
| 0x18 | `PARAM_SET` | 2 | có | sự kiện | 20 B |
| 0x19 | `TAGMAP_BEGIN` | 1 | có | sự kiện | 8 B |
| 0x1A | `TAGMAP_ENTRY` | 1 | có | sự kiện | 22 B |
| 0x1B | `TAGMAP_END` | 1 | có | sự kiện | 8 B |
| 0x1C | `REQUEST` | 1 | có | sự kiện | 4 B |
| 0x1D–0x1F | *(giữ chỗ)* `GEOFENCE_BEGIN/VERTEX/END` | 1 | có | sự kiện | — |

Dải `0x1D–0x1F` được **giữ chỗ** cho nhóm bản tin geofence, dùng lại đúng mẫu ba pha của `TAGMAP_*`.
Chưa đặc tả vì việc drone có tự cưỡng chế vùng bay hay không còn là điểm cần quyết (mục 13, quyết định
số 3). Không dùng ba mã này cho mục đích khác.

**Chiều xuống (drone → GCS), `msg_id` 0x40–0x6F:**

| ID | Tên | Ưu tiên | ACK | Tần số | Kích thước |
|---|---|---|---|---|---|
| 0x40 | `HEARTBEAT_DRONE` | 1 | không | 1 Hz | 12 B |
| 0x41 | `TELEM_FAST` | 0 | không | 10 Hz | 32 B |
| 0x42 | `TELEM_SLOW` | 0 | không | 1 Hz | 24 B |
| 0x43 | `MISSION_STATE` | 1 | không | 2 Hz + khi đổi | 16 B |
| 0x44 | `TAG_DETECT` | 0 | không | 5 Hz khi đang bám | 16 B |
| 0x45 | `EVENT` | 2 | có | sự kiện | 8 + text |
| 0x46 | `PARAM_VALUE` | 1 | không | trả lời | 20 B |
| 0x47 | `LINK_STAT` | 0 | không | 1 Hz | 12 B |

**Dùng chung:** `0x7F` = `ACK`.

### 5.4 Đặc tả từng byte của các bản tin cốt lõi

```c
// 0x41 TELEM_FAST — 32 byte, 10 Hz, KHÔNG ACK (mất thì bỏ, gói sau đè lên)
typedef struct __attribute__((packed)) {
    uint32_t t_ms;          // đồng hồ đơn điệu của drone
    int32_t  pos_n_mm;      // NED, mm
    int32_t  pos_e_mm;
    int32_t  pos_d_mm;      // âm = trên mặt đất
    int16_t  vel_n_cms;     // cm/s
    int16_t  vel_e_cms;
    int16_t  vel_d_cms;
    int16_t  roll_cdeg;     // centi-độ, ±18000
    int16_t  pitch_cdeg;
    uint16_t yaw_cdeg;      // 0..35999, từ Bắc theo chiều kim đồng hồ
    uint8_t  fsm_state;     // khớp mission_state_e của Pi 4
    uint8_t  flags;         // b0 armed, b1 offboard, b2 ekf_ok, b3 tag_lock,
                            // b4 carrying, b5 failsafe_active, b6 gps_ok, b7 dự phòng
    uint8_t  wp_index;
    uint8_t  battery_pct;
} telem_fast_t;

// 0x42 TELEM_SLOW — 24 byte, 1 Hz
typedef struct __attribute__((packed)) {
    uint32_t t_ms;
    uint16_t batt_mv;
    int16_t  batt_ca;          // dòng, centi-ampe
    uint16_t batt_mah_used;
    uint16_t mission_id;
    uint16_t uptime_s;
    uint8_t  batt_pct;
    uint8_t  rssi_mag;         // |RSSI| dBm, ví dụ 72 nghĩa là −72 dBm
    uint8_t  link_loss_pct;
    uint8_t  ekf_health;       // 0 xấu … 100 tốt
    uint8_t  gps_sats;
    uint8_t  cpu_pct;
    uint8_t  temp_c;
    uint8_t  gripper_state;    // khớp GRIP_STATE_* của Pi 4
    uint8_t  failsafe_type;    // khớp failsafe_type_e
    uint8_t  err_flags;
} telem_slow_t;

// 0x14 MISSION_WP — 28 byte, MỘT waypoint mỗi gói (xem 5.6)
typedef struct __attribute__((packed)) {
    uint16_t mission_id;
    uint8_t  seq;              // 0..total-1
    uint8_t  total;
    int32_t  pos_n_mm;
    int32_t  pos_e_mm;
    int32_t  pos_d_mm;
    int16_t  yaw_cdeg;
    int16_t  tag_id;           // −1 = không xác thực tag tại điểm này
    uint16_t accept_radius_cm;
    uint16_t max_vel_cms;
    uint16_t loiter_s;
    uint8_t  action;           // 0 NONE, 1 PICKUP, 2 DROPOFF, 3 WAIT
    uint8_t  flags;            // b0 require_tag_lock, b1 precision_land, b2 photo_on_arrive
} mission_wp_t;

// 0x12 CMD_GOTO — 22 byte
typedef struct __attribute__((packed)) {
    uint16_t cmd_seq;
    uint8_t  ref_frame;        // 0 MAP_NED, 1 TAG_RELATIVE, 2 BODY_RELATIVE
    int16_t  ref_tag_id;       // dùng khi ref_frame = 1
    int32_t  x_mm;             // theo hệ ref_frame
    int32_t  y_mm;
    int32_t  z_mm;
    int16_t  yaw_cdeg;
    uint16_t max_vel_cms;
    uint8_t  flags;            // b0 giữ yaw hiện tại, b1 bay thẳng đứng trước
} cmd_goto_t;

// 0x17 EMERGENCY — 8 byte
typedef struct __attribute__((packed)) {
    uint16_t cmd_seq;
    uint8_t  action;           // 1 RTH, 2 LAND_NOW, 3 HOLD, 4 ABORT_MISSION, 5 KILL
    uint8_t  reserved;
    uint32_t confirm_code;     // CRC32(action || cmd_seq || session_key); BẮT BUỘC với action=5
} emergency_t;

// 0x44 TAG_DETECT — 16 byte
typedef struct __attribute__((packed)) {
    uint32_t t_ms;
    int16_t  tag_id;
    int16_t  rel_x_mm;         // vị trí tag so với thân drone (FRD), ±32,7 m
    int16_t  rel_y_mm;
    int16_t  rel_z_mm;
    int16_t  yaw_rel_cdeg;
    uint8_t  quality;          // 0..100, suy từ reprojection error
    uint8_t  n_visible;
} tag_detect_t;

// 0x7F ACK — 5 byte
typedef struct __attribute__((packed)) {
    uint8_t  ack_msg_id;
    uint16_t ack_seq;
    uint8_t  result;           // xem bảng mã kết quả
    uint8_t  detail;
} ack_t;
```

Bảng mã `result` của ACK: `0` OK, `1` REJECT_BUSY, `2` REJECT_INVALID_ARG, `3` REJECT_WRONG_STATE,
`4` REJECT_CRC, `5` REJECT_UNSUPPORTED, `6` REJECT_AUTH, `7` REJECT_MAP_MISMATCH, `8` DEFERRED
(đã nhận, đang xử lý, sẽ có `EVENT` báo kết quả sau).

### 5.5 Cơ chế ACK, thử lại và chống trùng

Mọi bản tin có cờ `NEED_ACK` được quản lý bởi một bảng chờ ở bên gửi:

| Lớp ưu tiên | Timeout | Số lần thử | Hành vi khi hết lượt |
|---|---|---|---|
| 3 — khẩn cấp | 60 ms | 8 | Báo đỏ lên giao diện, **tiếp tục phát lại** mỗi 500 ms tới khi có ACK hoặc người vận hành hủy |
| 2 — lệnh | 150 ms | 4 | Trả lỗi cho người gọi, ghi sự kiện |
| 1 — thường | 400 ms | 3 | Trả lỗi cho người gọi |

Bên nhận giữ một bộ nhớ 64 `seq` gần nhất cho mỗi nguồn. Gói trùng `seq` **vẫn được ACK lại** (vì ACK
trước có thể đã mất) nhưng **không thực thi lần hai**. Đây là điểm bắt buộc: thiếu nó, một lệnh
`CMD_GOTO` bị thử lại có thể thực thi hai lần.

Lệnh khẩn cấp có thêm một lớp an toàn: chúng được thiết kế **idempotent** (RTH hai lần vẫn là RTH), nên
kể cả khi cơ chế chống trùng hỏng thì hậu quả vẫn vô hại.

### 5.6 Tải nhiệm vụ lên — giao thức ba pha

Không phân mảnh một gói nhiệm vụ lớn, mà gửi **một waypoint mỗi gói** và ACK từng gói. Lý do: nhiệm vụ
điển hình chỉ 4–8 waypoint (28 byte mỗi cái), và khi mất gói thì chỉ phải gửi lại đúng waypoint đó thay
vì toàn bộ kế hoạch.

```
GCS                                          Drone (Pi 4)
 │  MISSION_BEGIN(mission_id, total, map_crc)  │
 ├────────────────────────────────────────────►│  kiểm map_crc; nếu lệch → ACK result=7
 │◄───────────── ACK(0) ───────────────────────┤  xóa bộ đệm nhiệm vụ cũ
 │  MISSION_WP(seq=0) … MISSION_WP(seq=n−1)    │
 ├────────────────────────────────────────────►│  lưu vào bộ đệm, chưa kích hoạt
 │◄───────────── ACK mỗi gói ──────────────────┤
 │  MISSION_END(mission_id, wp_crc)            │
 ├────────────────────────────────────────────►│  kiểm đủ n waypoint + CRC toàn kế hoạch
 │◄───────────── ACK(0) ───────────────────────┤  publish /mission/plan (chưa chạy)
 │  MISSION_CTRL(START)                        │
 ├────────────────────────────────────────────►│  mission_manager_node bắt đầu FSM
 │◄───────────── ACK(0) + MISSION_STATE ───────┤
```

Quy tắc bắt buộc: **`MISSION_CTRL(START)` là một bản tin riêng**, tách khỏi `MISSION_END`. Không bao giờ
để việc tải kế hoạch tự động kích hoạt bay — người vận hành phải xem trước tuyến bay trong 3D rồi mới
bấm bắt đầu.

### 5.7 Ngân sách băng thông

| Luồng | Tần số | Byte/gói (kể header+CRC) | B/s |
|---|---|---|---|
| `TELEM_FAST` | 10 Hz | 42 | 420 |
| `TELEM_SLOW` | 1 Hz | 34 | 34 |
| `MISSION_STATE` | 2 Hz | 26 | 52 |
| `TAG_DETECT` (khi hạ cánh) | 5 Hz | 26 | 130 |
| `LINK_STAT` | 1 Hz | 22 | 22 |
| Heartbeat hai chiều | 1 Hz mỗi chiều | 22 | 44 |
| **Tổng thường trực** | ~20 gói/s | | **~700 B/s ≈ 5,6 kbps** |

So với thông lượng thực đo ~214 kbps của ESP-NOW trong môi trường mở, hệ số dự phòng là **38 lần**. Dư
địa này dùng để: tăng `TELEM_FAST` lên 20 Hz nếu 3D cần mượt hơn, và hấp thụ các đợt thử lại khi liên
kết xấu. Điều đáng quan tâm không phải tổng byte mà là **số gói/giây** — giữ dưới 50 gói/s để không
đụng trần xử lý ngắt của ESP32.

### 5.8 Watchdog và xử lý mất liên kết

| Bên | Điều kiện | Hành động |
|---|---|---|
| GCS | Không nhận `HEARTBEAT_DRONE` trong 3 s | Banner cam "mất liên kết", đóng băng vị trí drone trong 3D (chuyển sang mờ + nhấp nháy), **không** xóa khỏi cảnh |
| GCS | Mất liên kết > 10 s | Banner đỏ, ghi sự kiện, hiện gợi ý "drone sẽ tự kích RTH theo cấu hình" |
| GCS | Dongle không trả lời qua serial 2 s | Banner đỏ riêng "mất dongle" — phân biệt rõ với mất drone |
| Pi 4 | Không nhận `HEARTBEAT_GCS` trong `link_lost_timeout_s` (**10 s**, khớp `safety.yaml`) | `failsafe_monitor_node` phát `FS_LINK_LOST`, leo thang theo bảng đã chốt |

Ba trạng thái "mất dongle", "mất drone", "drone im nhưng dongle vẫn báo gửi thành công" phải phân biệt
được trên giao diện, vì ba nguyên nhân gốc hoàn toàn khác nhau (cáp USB, tầm sóng, treo phần mềm Pi 4).

---

## 6. Mô hình nhiệm vụ và máy trạng thái

### 6.1 Vòng đời nhiệm vụ đầy đủ

```
 [Người vận hành chọn tag lấy = A, tag giao = B]
                │
                ▼
 GCS: planner sinh waypoint
   0: Home,  cất cánh tới alt_cruise
   1: trên A, alt_cruise,  tag=A, flags=require_tag_lock
   2: trên A, alt_land,    tag=A, flags=precision_land, action=PICKUP
   3: trên A, alt_cruise
   4: trên B, alt_cruise,  tag=B, flags=require_tag_lock
   5: trên B, alt_land,    tag=B, flags=precision_land, action=DROPOFF
   6: trên B, alt_cruise
   7: Home,   alt_cruise,  tag=Home
   8: Home,   alt_land,    tag=Home, flags=precision_land
                │
                ▼
 [Xem trước trong 3D]  ──► người vận hành duyệt ──► tải lên (5.6) ──► START
                │
                ▼
 Drone chạy FSM (thẩm quyền ở Pi 4); GCS cập nhật shadow theo MISSION_STATE
```

Quy tắc sinh waypoint chốt: **luôn tách pha tiếp cận ngang và pha hạ độ cao**. Drone bay tới tọa độ
ngang của tag ở độ cao hành trình trước, khóa được tag rồi mới hạ. Không bao giờ sinh một waypoint đi
chéo thẳng xuống bãi đáp.

### 6.2 Bản sao bóng (shadow state) phía GCS

GCS **không** tự chạy FSM nhiệm vụ. Nó giữ một bản sao có cấu trúc:

```python
@dataclass
class DroneShadow:
    drone_id: int
    fsm_state: int           # từ MISSION_STATE, nguồn sự thật
    mission_id: int
    wp_index: int
    expected_tag: int
    retry_count: int
    last_state_change: float # thời điểm GCS nhận, không phải thời điểm drone đổi
    stale_since: float | None  # đặt khi mất heartbeat
```

Ba quy tắc dùng shadow:

1. Giao diện hiển thị `fsm_state` kèm **tuổi của dữ liệu**. Nếu tuổi > 3 s thì hiển thị dạng mờ với
   nhãn "số liệu cũ 7 s" — không bao giờ hiển thị trạng thái cũ như thể nó đang đúng.
2. GCS **suy diễn** tiến trình (ví dụ tô đậm chặng đang bay) từ shadow, nhưng khi shadow mâu thuẫn với
   kế hoạch cục bộ thì **shadow thắng** và ghi một cảnh báo vào log.
3. Sau khi GCS khởi động lại, shadow được dựng lại **hoàn toàn từ gói `MISSION_STATE` kế tiếp**, không
   phải từ CSDL. CSDL chỉ dùng để bù phần lịch sử.

### 6.3 Kiểm chứng lệnh bằng phản hồi trạng thái

ACK chỉ chứng minh "gói đã tới", không chứng minh "hành vi đã đổi". Với mọi lệnh làm đổi trạng thái,
backend mở một **cửa sổ kiểm chứng**:

| Lệnh | Điều kiện xác nhận thành công | Cửa sổ |
|---|---|---|
| `CMD_SIMPLE(ARM)` | `telem_fast.flags.armed == 1` | 2 s |
| `CMD_SIMPLE(TAKEOFF)` | `−pos_d` tăng ≥ 0,5 m | 5 s |
| `CMD_GOTO` | khoảng cách tới đích giảm đơn điệu trong 3 s đầu | 5 s |
| `EMERGENCY(RTH)` | `fsm_state == RTH` | 2 s |
| `MISSION_CTRL(START)` | `fsm_state` rời `IDLE` | 3 s |

Quá cửa sổ mà không xác nhận được → cảnh báo "lệnh đã nhận nhưng không có hiệu lực", cấp độ nghiêm
trọng. Đây là lớp bắt lỗi khi firmware FC tùy biến nhận lệnh nhưng không thực thi — đúng rủi ro số 1 đã
ghi nhận trong `nhat_ky_lam_viec.md`.

---

## 7. Backend GCS

### 7.1 Cấu trúc mã nguồn

```
gcs_backend/
├── main.py                   # FastAPI app, lifespan, khởi động các task nền
├── config.py                 # pydantic-settings, nạp từ .env + CSDL
├── link/
│   ├── cobs.py               # encode/decode, có fuzz test
│   ├── crc.py                # CRC16-CCITT-FALSE, CRC32
│   ├── frame.py              # link_hdr_t pack/unpack
│   ├── messages.py           # NGUỒN SỰ THẬT DUY NHẤT về layout bản tin
│   ├── serial_io.py          # pyserial-asyncio, đọc/ghi không chặn
│   ├── session.py            # seq, bảng chờ ACK, retry, hàng đợi ưu tiên, watchdog
│   ├── auth.py               # HMAC session key, chống phát lại
│   └── dongle.py             # bản tin chan_id=0x02
├── mission/
│   ├── planner.py            # (tag lấy, tag giao) → danh sách waypoint
│   ├── uploader.py           # giao thức ba pha ở 5.6
│   ├── shadow.py             # DroneShadow + kiểm chứng lệnh (6.3)
│   └── queue.py              # hàng đợi nhiệm vụ
├── safety/
│   ├── thresholds.py         # gương của safety.yaml phía Pi 4
│   ├── monitor.py            # sinh cảnh báo
│   └── config_check.py       # đối chiếu tham số hai bên khi nối lại
├── data/
│   ├── models.py             # SQLAlchemy
│   ├── repo.py
│   └── migrations/           # alembic
├── api/
│   ├── rest.py
│   ├── ws.py                 # đẩy telemetry
│   └── auth.py               # JWT, phân quyền
└── sim/
    └── fake_drone.py         # MÔ PHỎNG ĐẦY ĐỦ GIAO THỨC — xem 7.5
```

### 7.2 `messages.py` là nguồn sự thật duy nhất

Toàn bộ layout bản tin khai một lần, sinh ra cả bộ đóng gói lẫn tài liệu:

```python
# link/messages.py
import struct
from dataclasses import dataclass

MSG_TELEM_FAST = 0x41

@dataclass(slots=True)
class TelemFast:
    _FMT = "<IiiihhhhhHBBBB"        # khớp CHÍNH XÁC telem_fast_t phía C
    _SIZE = struct.calcsize(_FMT)   # phải == 32

    t_ms: int
    pos_n_mm: int; pos_e_mm: int; pos_d_mm: int
    vel_n_cms: int; vel_e_cms: int; vel_d_cms: int
    roll_cdeg: int; pitch_cdeg: int; yaw_cdeg: int
    fsm_state: int; flags: int; wp_index: int; battery_pct: int

    @classmethod
    def unpack(cls, b: bytes) -> "TelemFast":
        return cls(*struct.unpack(cls._FMT, b))

    def to_si(self) -> dict:
        """Quy đổi sang SI — điểm DUY NHẤT được phép làm việc này."""
        return {
            "t_ms": self.t_ms,
            "pos": [self.pos_n_mm / 1000, self.pos_e_mm / 1000, self.pos_d_mm / 1000],
            "vel": [self.vel_n_cms / 100, self.vel_e_cms / 100, self.vel_d_cms / 100],
            "att": [self.roll_cdeg / 100, self.pitch_cdeg / 100, self.yaw_cdeg / 100],
            "fsm_state": self.fsm_state,
            "armed": bool(self.flags & 0x01),
            "tag_lock": bool(self.flags & 0x08),
            "carrying": bool(self.flags & 0x10),
            "wp_index": self.wp_index,
            "battery_pct": self.battery_pct,
        }

assert TelemFast._SIZE == 32, "layout lệch với telem_fast_t phía C"
```

Câu `assert` cuối file chạy lúc import — nếu ai sửa struct một bên mà quên bên kia, backend **không khởi
động được**, thay vì chạy được nhưng đọc sai số liệu. Áp dụng mẫu này cho toàn bộ bản tin.

### 7.3 Vòng lặp liên kết

```python
# link/session.py — rút gọn phần cốt lõi
class LinkSession:
    def __init__(self, port: SerialIO, bus: EventBus):
        self._port, self._bus = port, bus
        self._seq = itertools.count(1)
        self._pending: dict[int, PendingCmd] = {}      # seq → chờ ACK
        self._queues = [asyncio.Queue() for _ in range(4)]  # theo mức ưu tiên
        self._seen: dict[int, deque[int]] = defaultdict(lambda: deque(maxlen=64))

    async def send(self, msg_id, payload, *, priority=1, need_ack=True,
                   timeout=None, retries=None) -> AckResult:
        seq = next(self._seq) & 0xFFFF
        frame = build_frame(msg_id, payload, seq, priority, need_ack)
        if need_ack:
            fut = asyncio.get_running_loop().create_future()
            self._pending[seq] = PendingCmd(fut, frame, priority)
        await self._queues[priority].put(frame)
        if not need_ack:
            return AckResult.ok()
        t, n = timeout or RETRY[priority].timeout, retries or RETRY[priority].count
        for attempt in range(n):
            try:
                return await asyncio.wait_for(asyncio.shield(fut), t)
            except asyncio.TimeoutError:
                await self._queues[priority].put(frame)     # gửi lại NGUYÊN VĂN, giữ seq
                self._bus.emit("link.retry", seq=seq, msg_id=msg_id, attempt=attempt + 1)
        self._pending.pop(seq, None)
        raise LinkTimeout(msg_id, seq, n)

    async def _tx_loop(self):
        """Rút hàng đợi theo ưu tiên tuyệt đối: 3 trước 2 trước 1 trước 0."""
        while True:
            for q in reversed(self._queues):             # 3 → 0
                if not q.empty():
                    await self._port.write_cobs(0x01, q.get_nowait())
                    break
            else:
                await asyncio.sleep(0.002)
```

Điểm dễ sai: khi thử lại phải gửi **nguyên văn khung cũ, giữ nguyên `seq`** — nếu sinh `seq` mới thì cơ
chế chống trùng phía drone mất tác dụng và lệnh có thể chạy hai lần.

### 7.4 Lược đồ cơ sở dữ liệu

```sql
CREATE TABLE site (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  origin_lat    REAL, origin_lon REAL, origin_alt_m REAL,  -- để quy đổi ra GPS nếu cần
  yaw_offset_deg REAL NOT NULL DEFAULT 0,                  -- lệch giữa Bắc từ và Bắc bản đồ
  -- các trường phục vụ tab thiết kế khu vực (mục 8.4)
  design_radius_m      REAL,      -- bán kính vận hành MỤC TIÊU do người thiết kế đặt
  link_radius_meas_m   REAL,      -- bán kính liên kết ĐO ĐƯỢC từ T2 (mục 11.2)
  cruise_alt_default_m REAL NOT NULL DEFAULT 5.0,
  bg_image_path        TEXT,      -- ảnh nền mặt bằng
  bg_anchor_json       TEXT,      -- 2 điểm mốc {px,py,n,e} để căn ảnh nền theo mét
  created_at    TIMESTAMP NOT NULL
);

-- Vùng bay và vùng cấm bay, vẽ trong tab thiết kế. Lưu dạng đa giác đỉnh có thứ tự.
CREATE TABLE site_area (
  id          INTEGER PRIMARY KEY,
  site_id     INTEGER NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK (kind IN ('operating','no_fly')),
  name        TEXT NOT NULL,
  min_alt_m   REAL NOT NULL DEFAULT 0,
  max_alt_m   REAL NOT NULL DEFAULT 20,
  enabled     INTEGER NOT NULL DEFAULT 1,
  notes       TEXT
);
CREATE TABLE site_area_vertex (
  id          INTEGER PRIMARY KEY,
  area_id     INTEGER NOT NULL REFERENCES site_area(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,        -- thứ tự đỉnh, khép kín tự động đỉnh cuối → đỉnh đầu
  pos_n_m     REAL NOT NULL,
  pos_e_m     REAL NOT NULL,
  UNIQUE (area_id, seq)
);

CREATE TABLE tag_point (
  id            INTEGER PRIMARY KEY,
  site_id       INTEGER NOT NULL REFERENCES site(id),
  tag_id        INTEGER NOT NULL,             -- ID AprilTag in trên bãi đáp
  label         TEXT NOT NULL,                -- "A", "B", "Home"
  pos_n_m       REAL NOT NULL,
  pos_e_m       REAL NOT NULL,
  pos_d_m       REAL NOT NULL DEFAULT 0,
  yaw_deg       REAL NOT NULL DEFAULT 0,
  tag_size_m    REAL NOT NULL,                -- kích thước in THẬT, sai → sai khoảng cách
  kind          TEXT NOT NULL CHECK (kind IN ('home','pickup','dropoff','waypoint')),
  landing_tol_m REAL NOT NULL DEFAULT 0.3,
  enabled       INTEGER NOT NULL DEFAULT 1,
  notes         TEXT,
  UNIQUE (site_id, tag_id)
);

CREATE TABLE drone (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  esp_peer_mac  TEXT NOT NULL UNIQUE,
  model         TEXT, max_payload_g INTEGER,
  created_at    TIMESTAMP NOT NULL
);

CREATE TABLE mission (
  id            INTEGER PRIMARY KEY,
  drone_id      INTEGER NOT NULL REFERENCES drone(id),
  requested_by  INTEGER REFERENCES app_user(id),
  plan_name     TEXT,
  state         TEXT NOT NULL,          -- queued|uploading|ready|running|done|failed|aborted
  pickup_tag    INTEGER, dropoff_tag INTEGER,
  priority      INTEGER NOT NULL DEFAULT 100,
  map_crc       INTEGER NOT NULL,       -- checksum bản đồ lúc lập kế hoạch
  created_at    TIMESTAMP NOT NULL,
  started_at    TIMESTAMP, finished_at TIMESTAMP,
  result        TEXT, fail_reason TEXT
);

CREATE TABLE mission_waypoint (
  id            INTEGER PRIMARY KEY,
  mission_id    INTEGER NOT NULL REFERENCES mission(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,
  tag_id        INTEGER,
  pos_n_m REAL, pos_e_m REAL, pos_d_m REAL, yaw_deg REAL,
  action        TEXT NOT NULL DEFAULT 'none',
  accept_radius_m REAL, max_vel_mps REAL, loiter_s REAL,
  require_tag_lock INTEGER NOT NULL DEFAULT 0,
  precision_land   INTEGER NOT NULL DEFAULT 0,
  reached_at    TIMESTAMP,
  UNIQUE (mission_id, seq)
);

CREATE TABLE telemetry_sample (      -- bảng lớn nhất, cần đánh chỉ mục cẩn thận
  id            INTEGER PRIMARY KEY,
  mission_id    INTEGER REFERENCES mission(id) ON DELETE CASCADE,
  drone_id      INTEGER NOT NULL,
  t_utc         TIMESTAMP NOT NULL,
  t_drone_ms    INTEGER NOT NULL,
  pos_n REAL, pos_e REAL, pos_d REAL,
  vel_n REAL, vel_e REAL, vel_d REAL,
  roll REAL, pitch REAL, yaw REAL,
  fsm_state INTEGER, flags INTEGER, wp_index INTEGER, battery_pct INTEGER
);
CREATE INDEX idx_telem_mission_t ON telemetry_sample (mission_id, t_utc);

CREATE TABLE mission_event (
  id          INTEGER PRIMARY KEY,
  mission_id  INTEGER REFERENCES mission(id) ON DELETE CASCADE,
  t_utc       TIMESTAMP NOT NULL, t_drone_ms INTEGER,
  severity    INTEGER NOT NULL,     -- 0 info, 1 warn, 2 error, 3 critical
  category    TEXT NOT NULL,        -- failsafe|command|link|gripper|marker|user
  code        INTEGER, message TEXT NOT NULL, payload_json TEXT
);

CREATE TABLE mission_photo (
  id INTEGER PRIMARY KEY, mission_id INTEGER REFERENCES mission(id) ON DELETE CASCADE,
  waypoint_seq INTEGER, action TEXT, t_utc TIMESTAMP NOT NULL,
  file_path TEXT NOT NULL, thumb_path TEXT
);

CREATE TABLE link_stat (
  id INTEGER PRIMARY KEY, drone_id INTEGER NOT NULL, t_utc TIMESTAMP NOT NULL,
  window_s INTEGER NOT NULL, tx_count INTEGER, rx_count INTEGER, lost_count INTEGER,
  rtt_ms_avg REAL, rssi_dbm_avg REAL
);

CREATE TABLE app_user (
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('operator','admin')),
  created_at TIMESTAMP NOT NULL, last_login TIMESTAMP
);

CREATE TABLE system_config (         -- PHẢI khớp safety.yaml phía Pi 4
  key TEXT PRIMARY KEY, value TEXT NOT NULL,
  drone_value TEXT,                  -- giá trị đọc về từ drone, để đối chiếu
  last_checked TIMESTAMP, in_sync INTEGER,
  updated_by INTEGER REFERENCES app_user(id), updated_at TIMESTAMP
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES app_user(id),
  t_utc TIMESTAMP NOT NULL, action TEXT NOT NULL, target TEXT, detail_json TEXT
);
```

Về dung lượng: 10 Hz × 64 byte/bản ghi ≈ 2,3 MB mỗi giờ bay. Với SQLite thì đủ cho giai đoạn nghiên cứu;
khi vượt ~50 GB nên chuyển sang PostgreSQL + TimescaleDB, hoặc hạ tần số lưu xuống 5 Hz và giữ 10 Hz chỉ
trong các pha hạ cánh.

### 7.5 `sim/fake_drone.py` — hạng mục bắt buộc, làm sớm

Một tiến trình Python nói **đúng** giao thức ở mục 5 qua một cặp cổng serial ảo (`socat -d -d
pty,raw,echo=0 pty,raw,echo=0`), mô phỏng: động học drone đơn giản (bậc nhất theo setpoint), FSM nhiệm
vụ, pin tụt dần, phát `TELEM_FAST`/`TELEM_SLOW`/`MISSION_STATE`, trả ACK, và **có chế độ gây lỗi**: rớt
gói theo tỉ lệ đặt được, trễ ngẫu nhiên, mất liên kết theo lịch, từ chối lệnh với từng mã `result`.

Đây không phải hạng mục "làm nếu còn thời gian". Không có nó thì frontend và backend bị chặn tiến độ bởi
phần cứng, và không cách nào kiểm thử được các nhánh lỗi (thử lại, timeout, mất liên kết giữa lúc hạ
cánh) một cách lặp lại được. Tiêu chí xong: chạy được toàn bộ kịch bản A→B→Home trong 3D mà **không có
bất kỳ phần cứng nào cắm vào máy**.

### 7.6 API

```
POST   /api/auth/login                  → JWT
GET    /api/sites/{id}/tags
POST   /api/tags            (admin)     → tạo tag, trả map_crc mới
PUT    /api/tags/{id}       (admin)
POST   /api/tags/sync       (admin)     → đẩy bản đồ xuống drone (0x19/0x1A/0x1B)
POST   /api/tags/teach      (admin)     → ghi vị trí hiện tại của drone thành tag

GET    /api/sites/{id}/design           → gói thiết kế đầy đủ: site + tag + vùng + bán kính
PUT    /api/sites/{id}/design (admin)   → lưu cả gói trong MỘT giao dịch (xem 8.4)
GET    /api/sites/{id}/areas
PUT    /api/sites/{id}/areas  (admin)   → thay toàn bộ đa giác vùng bay / vùng cấm
POST   /api/sites/{id}/validate         → chạy bộ kiểm tra thiết kế, trả danh sách lỗi/cảnh báo
POST   /api/sites/{id}/bg-image (admin) → tải ảnh nền + 2 điểm mốc căn theo mét
GET    /api/sites/{id}/export.png|pdf   → xuất sơ đồ khu vực
GET    /api/sites/{id}/tag-sheet.pdf    → tờ in tag đúng tag_size_m

POST   /api/missions                    {pickup_tag, dropoff_tag} → kế hoạch (chưa gửi)
GET    /api/missions/{id}/preview       → danh sách waypoint cho 3D xem trước
POST   /api/missions/{id}/upload        → chạy giao thức ba pha
POST   /api/missions/{id}/start|pause|resume|abort
GET    /api/missions?from=&to=&tag=&state=
GET    /api/missions/{id}/telemetry?rate=  → dữ liệu phát lại
GET    /api/missions/{id}/events|photos
GET    /api/missions/{id}/report.pdf

POST   /api/commands/simple             {action}
POST   /api/commands/goto               {ref_frame, ref_tag_id, x, y, z, yaw, max_vel}
POST   /api/commands/emergency          {action, confirm_token}
GET    /api/link/status
POST   /api/link/config     (admin)     {channel, lr_mode, peer_mac}
GET    /api/config          /  PUT /api/config (admin)
GET    /api/config/drift                → chênh lệch cấu hình GCS vs drone

WS     /ws/telemetry
       ← {"type":"telem_fast", ...}      10 Hz
       ← {"type":"telem_slow", ...}      1 Hz
       ← {"type":"mission_state", ...}   khi đổi
       ← {"type":"tag_detect", ...}      khi đang bám
       ← {"type":"event", ...}           khi có
       ← {"type":"link", ...}            1 Hz
```

Quy tắc cho WebSocket: **gộp gói trước khi đẩy**. Backend gom telemetry trong cửa sổ 100 ms rồi đẩy một
lần, tránh 10 lần đánh thức vòng lặp sự kiện của trình duyệt mỗi giây cho mỗi loại bản tin.

---

## 8. Frontend — cấu trúc và các màn hình

### 8.1 Cấu trúc mã nguồn

```
gcs_web/src/
├── app/            # router, layout, theme
├── features/
│   ├── scene3d/
│   │   ├── Scene.tsx            # <Canvas> react-three-fiber
│   │   ├── coords.ts            # mục 3.2 — CÓ UNIT TEST
│   │   ├── useTelemetryBuffer.ts# đệm + nội suy (mục 9.4)
│   │   └── objects/             # TagField, DroneModel, Trail, PlannedPath, …
│   ├── mission/    CreateMission, MissionQueue, MissionTimeline
│   ├── monitor/    Hud, TelemetryPanel, EmergencyBar, VideoPanel (bổ sung sau)
│   ├── tags/       TagTable, TagEditor, TagPrintSheet
│   ├── sitedesign/ SiteCanvas (2D), ToolPalette, AreaPolygonEditor,
│   │               TagPlacer, MeasureTool, BackgroundImageLayer,
│   │               RangeOverlay, ValidationPanel        # mục 8.4
│   ├── history/    MissionList, ReplayPlayer
│   └── admin/      Users, ConfigDrift, LinkDiagnostics
├── lib/            ws.ts, api.ts, units.ts
└── store/          zustand: telemetry, mission, link, ui
```

Khuyến nghị dùng **react-three-fiber** thay vì Three.js thuần: cây cảnh khai báo được, vòng đời khớp với
React, và vẫn dùng được mọi API Three.js khi cần. Nhưng **cấm** đặt state telemetry tần số cao vào React
state — xem mục 9.5.

### 8.2 Cây màn hình

| Màn hình | Nội dung chính | Quyền |
|---|---|---|
| **Vận hành 3D** (mặc định) | Khung cảnh 3D toàn màn hình, HUD phủ góc trên trái, thanh khẩn cấp cố định đáy, panel telemetry thu gọn được bên phải, danh sách tag bên trái | operator |
| **Tạo nhiệm vụ** | Chọn tag lấy/giao từ danh sách hoặc bấm trực tiếp vào tag trong 3D, đặt tham số (độ cao hành trình, tốc độ), xem trước tuyến bay được tô sáng trong chính cảnh 3D, nút Tải lên và Bắt đầu tách rời | operator |
| **Hàng đợi** | Bảng nhiệm vụ chờ/đang chạy, kéo thả đổi thứ tự, hủy | operator |
| **Thiết kế khu vực** | Sơ đồ 2D nhìn từ trên: đặt/kéo tag, đặt Home, vẽ biên vùng bay và vùng cấm, đo khoảng cách, ảnh nền mặt bằng, vòng tròn bán kính liên kết, bảng lỗi thiết kế, xuất sơ đồ + tờ in tag. Đặc tả ở 8.4 | admin |
| **Quản lý tag** | Bảng tag CRUD, nút "dạy vị trí từ drone", in tờ tag, trạng thái đồng bộ bản đồ + `map_crc` hai bên | admin |
| **Lịch sử & phát lại** | Danh sách nhiệm vụ, lọc theo thời gian/tag/kết quả; mở một nhiệm vụ → **cùng khung cảnh 3D** với thanh tua, tốc độ 0,5×/1×/4×, trục thời gian có mốc sự kiện | operator |
| **Cảnh báo** | Cảnh báo đang hoạt động, lịch sử sự cố, trạng thái failsafe | operator |
| **Chẩn đoán liên kết** | Đồ thị RSSI/PDR/RTT theo thời gian, thống kê dongle, nút kiểm tra kênh | admin |
| **Quản trị** | Người dùng, cấu hình ngưỡng + **bảng đối chiếu lệch cấu hình với drone**, cấu hình ESP-NOW | admin |

### 8.3 Thanh khẩn cấp — đặc tả chi tiết

Luôn hiển thị, không bao giờ bị che, không nằm trong tab hay menu. Ba nút chính: **RTH**, **HẠ CÁNH
NGAY**, **HỦY NHIỆM VỤ**. Mỗi nút:

- bấm một lần → nút chuyển sang trạng thái "giữ để xác nhận", người dùng phải **giữ 800 ms**;
- trong lúc giữ, hiện vòng tiến trình; nhả sớm thì hủy. Cơ chế này chống bấm nhầm mà không làm chậm
  thao tác khẩn cấp thật (khác hẳn hộp thoại xác nhận phải di chuột đi nơi khác);
- sau khi phát lệnh, nút hiện trạng thái **ba giai đoạn**: "đang gửi" → "đã nhận (ACK)" → "đã có hiệu
  lực (trạng thái đổi)". Người vận hành phải thấy được sự khác nhau giữa "drone đã nghe" và "drone đã
  làm".

Nút **KILL** (ngắt động cơ trên không) đặt riêng, sau một menu bung, đòi gõ từ khóa xác nhận, và chỉ
hiện với quyền admin. Lệnh này gây rơi drone và chỉ dùng khi mất kiểm soát hoàn toàn ở nơi có người.

### 8.4 Tab Thiết kế khu vực hoạt động — đặc tả chi tiết

Đây là màn hình dùng **trước khi vận hành**, thường chỉ một lần cho mỗi địa điểm rồi thỉnh thoảng sửa.
Mục tiêu: biến việc khai báo khu vực từ chỗ gõ số vào bảng thành chỗ nhìn thấy được — ai cũng thấy ngay
tag nào lệch, tag nào nằm ngoài tầm sóng, vùng bay có bao trọn các điểm giao nhận hay không.

#### 8.4.1 Vì sao là sơ đồ 2D chứ không phải dùng luôn cảnh 3D

Cảnh 3D ở mục 9 tối ưu cho **giám sát chuyển động**; việc đặt một điểm vào đúng tọa độ trong phối cảnh
3D bằng chuột thì vừa khó vừa dễ sai vì không có chiều sâu tham chiếu. Vì mọi tag đều đồng phẳng (giả
định ở 1.4), một sơ đồ nhìn thẳng từ trên xuống là biểu diễn **không mất mát thông tin** và thao tác
chính xác hơn hẳn. Có nút "Xem trong 3D" để mở chính khu vực vừa thiết kế trong cảnh 3D kiểm tra lại.

Hệ quả kỹ thuật: tab này dùng canvas 2D (khuyến nghị `react-konva` hoặc SVG), **không** dùng
react-three-fiber. Quy đổi đơn giản: `px_x = (pos_e − e0) × scale`, `px_y = −(pos_n − n0) × scale` —
Bắc hướng lên trên màn hình. Lưu ý dấu trừ ở trục tung, cùng loại lỗi đã cảnh báo ở mục 3.2.

#### 8.4.2 Bộ công cụ

| Công cụ | Thao tác | Ghi chú |
|---|---|---|
| Chọn / kéo | Bấm chọn tag, kéo để dời | Giữ `Shift` = bắt điểm theo lưới 0,5 m; hiện tọa độ số ngay cạnh con trỏ |
| Đặt tag | Bấm vào sơ đồ → hộp thoại nhập ID, nhãn, loại, kích thước in | ID trùng bị chặn ngay tại hộp thoại |
| Đặt Home | Đánh dấu một tag là Home | Mỗi khu vực đúng **một** Home; đặt Home mới thì Home cũ tự hạ thành điểm thường |
| Vẽ vùng bay | Bấm từng đỉnh, bấm đỉnh đầu để khép kín | Một vùng `operating` cho mỗi khu vực |
| Vẽ vùng cấm | Như trên, tô chéo đỏ | Nhiều vùng `no_fly` |
| Thước đo | Kéo giữa hai điểm → hiện khoảng cách | Bắt vào tâm tag khi lại gần |
| Ảnh nền | Tải ảnh mặt bằng, đặt **2 điểm mốc** và nhập tọa độ thật của chúng | Phần mềm tự tính tỉ lệ + góc xoay; không cho căn bằng mắt |
| Nhập tọa độ số | Bảng bên phải, sửa trực tiếp ô N/E/D | **Bàn phím luôn thắng chuột về độ chính xác** — mọi giá trị kéo bằng chuột đều sửa lại được bằng số |
| Dạy từ drone | Bay drone tới tag, bấm "ghi vị trí hiện tại" | Dùng `POST /api/tags/teach`; chỉ bật khi liên kết đang lên |

#### 8.4.3 Các lớp phủ

Vòng tròn **bán kính liên kết đo được** (`link_radius_meas_m`, lấy từ T2 ở mục 11.2) vẽ nét liền quanh
vị trí trạm mặt đất, kèm vòng **biên an toàn 30 %** nét đứt bên trong. Vòng tròn **bán kính mục tiêu**
(`design_radius_m`) vẽ màu khác. Khi chưa chạy T2 thì `link_radius_meas_m` rỗng và lớp phủ hiện nhãn
"chưa đo" — **không** được đoán một con số mặc định, vì một vòng tròn trông có vẻ chính thống mà thực ra
bịa ra thì nguy hiểm hơn là không có gì.

Các lớp còn lại: lưới mét, ảnh nền, vùng bay (tô nhạt), vùng cấm (tô chéo đỏ), tag + nhãn ID, đường nối
Home tới từng tag kèm cự ly, và tuyến bay của một nhiệm vụ mẫu nếu người dùng chọn thử một cặp lấy/giao.
Mỗi lớp bật/tắt độc lập.

#### 8.4.4 Bộ kiểm tra thiết kế

Chạy tự động sau mỗi thay đổi (debounce 300 ms) và khi bấm Lưu. Kết quả hiện ở panel bên phải, bấm vào
một dòng thì sơ đồ tự lia tới và tô sáng đối tượng liên quan.

| Mã | Điều kiện | Mức | Lý do |
|---|---|---|---|
| `E_DUP_TAG` | Hai tag cùng `tag_id` | **Lỗi** | Drone xác thực tag theo ID; trùng ID là hạ nhầm bãi |
| `E_NO_HOME` | Không có tag nào loại `home` | **Lỗi** | RTH không có đích |
| `E_SELF_INTERSECT` | Đa giác vùng tự cắt | **Lỗi** | Phép kiểm "điểm trong đa giác" cho kết quả vô nghĩa |
| `E_TAG_OUTSIDE` | Tag nằm ngoài vùng bay | **Lỗi** | Nhiệm vụ sẽ dẫn drone ra ngoài biên đã khai |
| `E_TAG_IN_NOFLY` | Tag nằm trong vùng cấm | **Lỗi** | — |
| `W_BEYOND_LINK` | Tag xa trạm hơn `link_radius_meas_m × 0,7` | **Cảnh báo** | Bay tới đó sẽ mất liên kết; xem R2 |
| `W_NO_MEASURE` | Chưa có `link_radius_meas_m` | **Cảnh báo** | Chưa chạy T2 thì chưa biết tầm thật |
| `W_TAGS_CLOSE` | Hai tag cách nhau < 3 × `tag_size_m` | **Cảnh báo** | Camera có thể bắt nhầm tag lân cận lúc hạ cánh |
| `W_TAG_SIZE` | `tag_size_m` lệch quá 20 % so với các tag khác | **Cảnh báo** | Thường là gõ nhầm; sai kích thước in → sai khoảng cách đúng bấy nhiêu % |
| `W_AREA_SMALL` | Vùng bay không bao được Home + mọi tag kèm biên 5 m | **Cảnh báo** | Thiếu chỗ xoay trở khi hạ cánh |

Quy tắc: còn **Lỗi** thì không lưu được và không đồng bộ bản đồ xuống drone; còn **Cảnh báo** thì lưu
được nhưng phải tích ô "tôi đã hiểu", và ô tích đó ghi vào `audit_log`.

#### 8.4.5 Lưu, đồng bộ và ranh giới với drone

Toàn bộ gói thiết kế lưu trong **một giao dịch** qua `PUT /api/sites/{id}/design` — không lưu từng tag
lẻ, vì trạng thái nửa vời (tag mới đã lưu nhưng biên vùng chưa) sẽ làm bộ kiểm tra báo lỗi giả.

Sau khi lưu, `map_crc` được tính lại và giao diện nhắc đồng bộ bản đồ xuống drone (mục 3.3). **Lưu ý
quan trọng về phạm vi**: `map_crc` và bản tin `TAGMAP_*` hiện **chỉ mang tag**, không mang vùng bay.
Nghĩa là vùng bay và vùng cấm hiện là **ràng buộc phía GCS** — dùng để kiểm tra thiết kế, cảnh báo người
vận hành và vẽ trong 3D — chứ drone **không tự cưỡng chế** chúng. Muốn drone tự chặn khi vượt biên thì
phải bổ sung geofence vào tầng Pi 4/FC, việc này chạm hợp đồng failsafe nên tách thành quyết định riêng
(xem mục 13). Dải `msg_id` **0x1D–0x1F đã được giữ chỗ** cho nhóm bản tin geofence, dùng lại đúng mẫu ba
pha của `TAGMAP_*`. Đội thiết kế **không được** ngầm giả định drone đang tôn trọng biên vùng bay.

#### 8.4.6 Tiêu chí xong

Dựng được một khu vực 6 tag từ con số 0 trong dưới 10 phút; sửa tọa độ một tag bằng số rồi bằng chuột
cho kết quả khớp nhau; toàn bộ 10 quy tắc kiểm tra ở 8.4.4 kích hoạt đúng khi dựng dữ liệu vi phạm;
xuất được sơ đồ PNG và tờ in tag mà đo thước trên bản in đúng `tag_size_m` (sai số < 1 mm).

---

## 9. Đặc tả khung cảnh 3D

### 9.1 Cây cảnh

```
<Canvas>
 ├── Lights: hemisphere(0.6) + directional(1.0, đổ bóng, shadow-mapSize 2048)
 ├── worldRoot                                   ← mọi thứ trong hệ Three đã quy đổi
 │    ├── GroundPlane        mặt phẳng + lưới 1 m/ô, mờ dần theo khoảng cách
 │    ├── TagField           InstancedMesh (tấm 
 │    │                      vuông theo tag_size) + nhãn ID dạng Sprite
 │    ├── HomePad            khác màu, có vòng tròn dung sai
 │    ├── PlannedPath        Line2 nét đứt, chặng đã qua chuyển xám
 │    ├── FlownTrail         Line2 liền, gradient theo độ cao, đệm vòng 3000 điểm
 │    ├── WaypointMarkers    hình trụ + số thứ tự, điểm đang tới nhấp nháy
 │    ├── DroneGroup
 │    │    ├── droneModel (glTF, ≤ 20k tam giác)
 │    │    ├── propellers ×4 (quay khi armed)
 │    │    ├── cameraFrustum (LineSegments, nón theo FOV thật)
 │    │    ├── payloadBox (hiện khi flags.carrying)
 │    │    └── altitudeLine (đường thẳng xuống mặt đất + bóng đổ)
 │    ├── LandingCone        hành lang tiếp cận tại tag đích, hiện ở pha hạ cánh
 │    ├── TagDetectRay       tia từ camera tới tag đang khóa, màu theo quality
 │    ├── OperatingArea      lăng trụ theo đa giác vùng bay (8.4), khung dây,
 │    │                      đỏ khi drone tới gần biên — CẢNH BÁO, không cưỡng chế
 │    └── NoFlyZones         lăng trụ đa giác tô chéo đỏ
 └── Controls: OrbitControls + các góc nhìn đặt sẵn
```

HUD **không** nằm trong cảnh 3D — nó là lớp DOM phủ lên trên. Vẽ chữ trong WebGL vừa tốn kém vừa khó đọc
khi xoay cảnh.

### 9.2 Bảng ánh xạ dữ liệu → hiển thị

| Trường telemetry | Đối tượng 3D | Quy tắc |
|---|---|---|
| `pos_n/e/d` | `DroneGroup.position` | qua `nedToThree()`, có nội suy (9.4) |
| `roll/pitch/yaw` | `DroneGroup.quaternion` | qua `attitudeToThree()`, slerp |
| `flags.armed` | cánh quạt | quay 30 vòng/s khi armed, đứng yên khi không |
| `flags.carrying` | `payloadBox` | hiện/ẩn, có hiệu ứng mờ dần 300 ms |
| `flags.tag_lock` | `TagDetectRay` | xanh lá khi khóa, vàng khi thấy mà chưa khóa, ẩn khi không thấy |
| `flags.failsafe_active` | viền cảnh | viền đỏ nhấp nháy quanh canvas |
| `fsm_state` | `WaypointMarkers`, `LandingCone` | `PRECISION_LAND` → hiện hành lang tiếp cận |
| `wp_index` | `PlannedPath` | chặng đã qua chuyển xám, chặng hiện tại sáng |
| `battery_pct` | HUD | < 25 % vàng, < 15 % đỏ (khớp ngưỡng drone) |
| `tag_detect.quality` | màu tia | gradient đỏ→xanh theo 0→100 |
| tuổi dữ liệu > 3 s | toàn bộ `DroneGroup` | opacity 0,4 + nhãn "số liệu cũ N s" |

### 9.3 Góc nhìn đặt sẵn

| Phím | Góc nhìn | Dùng khi |
|---|---|---|
| `1` | Nhìn từ trên xuống toàn khu vực | Giám sát tổng thể, kiểm tra tuyến bay |
| `2` | Theo sau drone (chase), giữ khoảng cách 8 m | Bay hành trình |
| `3` | Nhìn từ tag đích lên | **Pha hạ cánh** — thấy rõ drone lệch tâm bao nhiêu |
| `4` | Nhìn ngang, khóa độ cao | Kiểm tra profile độ cao |
| `0` | Tự do | — |

Góc nhìn `3` là góc quan trọng nhất về mặt vận hành: nó biến bài toán "drone có vào đúng tâm không"
thành thứ nhìn một cái là biết, thay vì phải đọc số.

### 9.4 Nội suy và độ trễ hiển thị

Telemetry về 10 Hz, màn hình vẽ 60 fps. Nếu gán thẳng vị trí mỗi khi có gói, drone sẽ giật thành 10
bước/giây. Giải pháp chốt: **đệm và dựng chậm lại 150 ms**.

```ts
// useTelemetryBuffer.ts — ý tưởng cốt lõi
const RENDER_DELAY_MS = 150;   // > 1 chu kỳ telemetry, đủ để luôn có 2 mẫu bao quanh

function sampleAt(buf: Sample[], tNow: number) {
  const t = tNow - RENDER_DELAY_MS;
  const i = buf.findLastIndex(s => s.tLocal <= t);
  if (i < 0 || i >= buf.length - 1) return buf.at(-1);      // thiếu mẫu → giữ mẫu cuối
  const a = buf[i], b = buf[i + 1];
  const k = (t - a.tLocal) / (b.tLocal - a.tLocal);
  return {
    pos: a.pos.clone().lerp(b.pos, k),
    quat: a.quat.clone().slerp(b.quat, k),
    ...
  };
}
```

Ba quy tắc bắt buộc: **(1)** không bao giờ **ngoại suy** vị trí khi thiếu dữ liệu — giữ nguyên mẫu cuối
và làm mờ, vì một drone "bay tiếp" trong hình trong khi thật ra đã mất liên lạc là thông tin sai lệch
nguy hiểm; **(2)** mốc thời gian dùng để nội suy là **thời điểm nhận tại GCS**, không phải `t_ms` của
drone (hai đồng hồ trôi khác nhau); **(3)** `RENDER_DELAY_MS` phải hiển thị được ở màn hình chẩn đoán,
để khi ai đó thắc mắc "3D trễ hơn thực tế" thì có con số để trả lời.

### 9.5 Hiệu năng

| Kỹ thuật | Lý do |
|---|---|
| Telemetry **không** đi qua React state; ghi thẳng vào một `useRef` và cập nhật object trong `useFrame` | 10 Hz × re-render cả cây component sẽ làm tụt fps ngay từ khi cảnh còn đơn giản |
| `InstancedMesh` cho toàn bộ tag | 50 tag = 1 draw call thay vì 50 |
| `Line2` với `Float32Array` cấp phát sẵn cho vệt bay, ghi theo đệm vòng | Tránh cấp phát lại mỗi khung hình |
| `setDrawRange` thay vì tạo lại geometry khi vệt bay dài ra | — |
| Giới hạn `devicePixelRatio` ≤ 2 | Màn 4K sẽ vẽ 4× số điểm ảnh không cần thiết |
| Dừng vòng lặp render khi tab ẩn (`document.hidden`) | Tiết kiệm pin máy trạm; telemetry vẫn ghi vào CSDL phía backend |
| Bóng đổ chỉ bật cho drone, tắt cho tag và vệt bay | Shadow map là chi phí lớn nhất trong cảnh này |

Chỉ tiêu nghiệm thu: ≥ 50 fps trên máy có GPU tích hợp (Intel Iris Xe hoặc tương đương) với 50 tag, vệt
bay 3000 điểm, ở độ phân giải 1920×1080.

### 9.6 Phát lại dùng chung khung cảnh

Trừu tượng hóa nguồn dữ liệu thành một interface duy nhất, để màn hình phát lại **không** phải dựng lại
cảnh 3D:

```ts
interface TelemetrySource {
  subscribe(cb: (s: Sample) => void): () => void;
  seek?(tUtc: number): void;      // chỉ ReplaySource có
  setRate?(r: number): void;
}
class LiveSource implements TelemetrySource { /* WebSocket */ }
class ReplaySource implements TelemetrySource { /* fetch từ /api/missions/{id}/telemetry */ }
```

Lợi ích kép: mọi cải tiến hiển thị áp dụng cho cả hai chế độ, và quan trọng hơn — **phát lại trở thành
công cụ gỡ lỗi chính**. Khi một nhiệm vụ thất bại, đội kỹ thuật mở lại đúng cảnh 3D đó, tua tới thời
điểm sự cố, và thấy chính xác drone ở đâu, nghiêng bao nhiêu, có khóa được tag không.

---

## 10. An toàn, phân quyền, và đối chiếu cấu hình

### 10.1 Bảng ngưỡng phải khớp hai bên

Các khóa này tồn tại ở cả `system_config` (GCS) và `safety.yaml` (Pi 4). Giá trị lấy theo cấu hình đã
chốt trong `ke_hoach_thiet_ke_node.md`:

| Khóa | Giá trị | Hệ quả nếu lệch |
|---|---|---|
| `low_battery_pct` | 25,0 | GCS báo an toàn trong khi drone đã chuyển RTH → người vận hành hoang mang |
| `critical_battery_pct` | 15,0 | GCS không kịp cảnh báo trước khi hạ khẩn cấp |
| `link_lost_timeout_s` | 10,0 | GCS và drone bất đồng về thời điểm coi là mất liên lạc |
| `marker_search_timeout_s` | 20,0 | GCS hiện "đang tìm tag" trong khi drone đã bỏ cuộc |
| `max_retries` | 3 | Đếm số lần thử hiển thị sai |
| `takeoff_alt_m` | 5,0 | Xem trước 3D không khớp đường bay thật |
| `acceptance_radius_m` | 1,5 | Vòng dung sai vẽ trong 3D sai kích thước |

Cơ chế: mỗi khi liên kết lên, backend gửi `REQUEST(PARAMS)`, drone trả một loạt `PARAM_VALUE`. Backend
ghi vào cột `drone_value`, đặt `in_sync`, và hiện **banner vàng liệt kê từng khóa lệch** nếu có. Đây là
việc tự động, không phụ thuộc ai đó nhớ kiểm tra.

### 10.2 Phân quyền

| Hành động | operator | admin |
|---|---|---|
| Xem 3D, lịch sử, cảnh báo | ✓ | ✓ |
| Tạo/chạy/hủy nhiệm vụ | ✓ | ✓ |
| Lệnh khẩn cấp RTH / hạ cánh / hủy | ✓ | ✓ |
| Điều khiển tay (goto, cất/hạ cánh) | ✓ | ✓ |
| Sửa bản đồ tag, đồng bộ bản đồ | — | ✓ |
| Sửa ngưỡng an toàn, cấu hình ESP-NOW | — | ✓ |
| Lệnh KILL | — | ✓ |
| Quản lý người dùng | — | ✓ |

Mọi hành động thuộc nhóm lệnh hoặc sửa cấu hình ghi vào `audit_log` kèm `user_id`, không ngoại lệ.

### 10.3 Những gì GCS **không** được phép làm

GCS không đóng vòng điều khiển nào. Không có "cần điều khiển ảo" gửi lệnh tốc độ liên tục qua ESP-NOW —
độ trễ vô tuyến biến thiên biến vòng điều khiển đó thành nguồn dao động. Điều khiển tay ở GCS luôn là
**lệnh vị trí rời rạc** ("tới điểm này"), do Pi 4 thực thi bằng vòng kín cục bộ.

GCS cũng không được là thứ duy nhất phát hiện sự cố: mọi failsafe GCS giám sát đều phải có bản sao phía
drone. GCS phát hiện sớm hơn thì tốt, nhưng nếu GCS tắt nguồn thì drone vẫn phải tự xoay xở đầy đủ.

---

## 11. Kiểm thử và nghiệm thu

### 11.1 Năm tầng kiểm thử

| Tầng | Cần gì | Kiểm được gì |
|---|---|---|
| **T0 — Loopback phần mềm** | Chỉ một máy tính, `socat` + `fake_drone.py` | Toàn bộ giao thức, ACK, retry, phân mảnh, FSM bóng, toàn bộ giao diện 3D, mọi nhánh lỗi |
| **T1 — Bàn test hai ESP32** | 2 ESP32, cách nhau 1 m | Khung COBS qua UART thật, thông lượng, RTT, tỉ lệ mất gói nền |
| **T2 — Kiểm tra tầm sóng** | 2 ESP32 + pin, đi bộ ngoài trời | PDR/RSSI theo khoảng cách, xác định bán kính vận hành an toàn |
| **T3 — Tích hợp Pi 4** | ESP32-AIR nối UART Pi 4, `gcs_link_node` thật | Tích hợp ROS 2, chuyển đổi topic ↔ bản tin |
| **T4 — Mô phỏng toàn hệ** | Gazebo + Pi 4 + GCS | Nhiệm vụ đầy đủ A→B→Home, các kịch bản lỗi |
| **T5 — Bay buộc dây** | Drone thật, dây an toàn hoặc lồng | Độ trễ thật, rung, nhiễu từ ESC |

Thứ tự là bắt buộc — không nhảy cóc. Đặc biệt **T2 phải xong trước T5**: bay thật ở khoảng cách chưa
từng đo PDR là cách nhanh nhất để mất drone.

### 11.2 Quy trình kiểm tra tầm sóng (T2)

Đặt một ESP32 cố định ở vị trí GCS dự kiến, ESP32 kia mang đi bộ theo đường thẳng, dừng mỗi 25 m trong
60 s. Tại mỗi mốc ghi: số gói gửi, số gói nhận, RSSI trung bình, RTT trung bình và phân vị 99. Lặp lại
**ở độ cao 2 m** (mô phỏng drone bay thấp) và ở vị trí có vật cản điển hình của khu vực vận hành.

Tiêu chí: bán kính vận hành công bố = khoảng cách lớn nhất mà PDR ≥ 98 % **trừ đi 30 % biên an toàn**.
Nếu không đạt tầm mong muốn, thứ tự xử lý: đổi ăng-ten ngoài → nâng vị trí ăng-ten GCS → bật LR mode
(phải bật cả hai đầu, đo lại từ đầu) → đổi kênh để tránh nhiễu WiFi.

### 11.3 Bảng nghiệm thu

| # | Hạng mục | Tiêu chí đạt | Tầng |
|---|---|---|---|
| A1 | Đóng/mở gói | Fuzz 10⁶ khung ngẫu nhiên qua COBS+CRC: 0 sai sót không phát hiện | T0 |
| A2 | Chống trùng lệnh | Gửi lại cùng `seq` 100 lần → thực thi đúng 1 lần, ACK đủ 100 lần | T0 |
| A3 | Thử lại | Ép rớt 30 % gói → 100 % lệnh vẫn tới, không lệnh nào chạy hai lần | T0 |
| A4 | Ưu tiên | Ngập telemetry 50 gói/s → lệnh khẩn cấp vẫn ACK trong < 300 ms | T0/T1 |
| A5 | Mất liên kết | Rút ăng-ten → GCS báo trong ≤ 3 s, phân biệt đúng "mất dongle" vs "mất drone" | T1 |
| A6 | Hệ trục 3D | Bảng kiểm chứng mục 3.2 đúng cả 5 trường hợp | T0 |
| A7 | Hiệu năng 3D | ≥ 50 fps với 50 tag + vệt 3000 điểm trên GPU tích hợp | T0 |
| A8 | Không ngoại suy | Ngắt telemetry giữa chừng → drone trong cảnh **đứng yên và mờ đi**, không bay tiếp | T0 |
| A9 | Tải nhiệm vụ | Rớt gói giữa chừng → tải lại thành công, `wp_crc` khớp | T0/T3 |
| A10 | Đối chiếu cấu hình | Cố ý đặt lệch một ngưỡng → banner cảnh báo nêu đúng tên khóa | T3 |
| A11 | Kiểm chứng lệnh | Giả lập drone ACK nhưng không đổi trạng thái → cảnh báo "lệnh không có hiệu lực" | T0 |
| A12 | Độ trễ khẩn cấp | 100 lần bấm RTH: p95 ≤ 300 ms, p99 ≤ 500 ms | T2/T3 |
| A13 | PDR | ≥ 98 % ở bán kính vận hành công bố | T2 |
| A14 | Khôi phục | Kill backend giữa nhiệm vụ → khởi động lại, shadow dựng lại trong ≤ 5 s | T3 |
| A15 | Phát lại | Nhiệm vụ đã bay phát lại khớp từng sự kiện với log | T4 |
| A16 | Kiểm tra thiết kế khu vực | Dựng dữ liệu vi phạm cho từng quy tắc ở 8.4.4 → đủ 10 mã lỗi/cảnh báo kích hoạt đúng, còn Lỗi thì chặn được lưu và chặn đồng bộ bản đồ | T0 |
| A17 | Tờ in tag | Đo thước trên bản in → cạnh tag đúng `tag_size_m`, sai số < 1 mm | T0 |

---

## 12. Lộ trình triển khai

| GĐ | Nội dung | Kết quả kiểm chứng được | Ước lượng |
|---|---|---|---|
| **1** | `link/` (COBS, CRC, frame, messages) + `fake_drone.py` | A1, A2, A3 đạt, chưa cần phần cứng | 1,5 tuần |
| **2** | Backend: CSDL, API, WS, shadow, safety monitor | Chạy được nhiệm vụ giả lập đầu-cuối qua API | 2 tuần |
| **3** | Frontend: khung cảnh 3D + HUD + thanh khẩn cấp | A6, A7, A8 đạt; xem được nhiệm vụ giả lập trong 3D | 2,5 tuần |
| **4** | Frontend: tạo nhiệm vụ, quản lý tag, **tab thiết kế khu vực (8.4)**, hàng đợi, lịch sử/phát lại | A15, A16, A17 đạt | 3 tuần |
| **5** | Firmware ESP32 hai đầu + T1, T2 | A4, A5, A13 đạt; bán kính vận hành có số liệu | 1,5 tuần |
| **6** | Tích hợp `gcs_link_node` phía Pi 4 + T3, T4 | A9, A10, A12, A14 đạt | 2 tuần |
| **7** | T5 bay buộc dây, hiệu chỉnh, nghiệm thu | Toàn bộ bảng A | 1,5 tuần |

Giai đoạn 1–4 **không cần phần cứng nào**, chạy song song được với công việc firmware và ROS 2 đang dang
dở. Đây là lý do `fake_drone.py` được đặt ở giai đoạn 1 chứ không phải cuối.

---

## 13. Rủi ro và điểm cần quyết

| # | Rủi ro | Mức | Giảm thiểu |
|---|---|---|---|
| R1 | Nhiễu 2,4 GHz giữa ESP-NOW và kênh video WiFi (rủi ro **tiềm ẩn**: chỉ bộc lộ khi bổ sung video ở giai đoạn sau, lúc kiến trúc đã cố định) | **Cao** | Video WiFi bắt buộc chạy 5 GHz; chọn module WiFi hỗ trợ 5 GHz cho Pi 4 **ngay từ đầu**; ESP-NOW cố định kênh 1; khi có video thì đo lại PDR có và không có video đang chạy |
| R2 | Tầm sóng thực tế thấp hơn kỳ vọng trong môi trường nhiều kim loại | **Cao** | T2 làm sớm, ngay giai đoạn 5; chuẩn bị sẵn phương án LR mode và ăng-ten định hướng ở GCS |
| R3 | Firmware FC tùy biến không đáp ứng đúng lệnh dù ACK | **Cao** | Cơ chế kiểm chứng lệnh ở mục 6.3; đây cũng là rủi ro số 1 trong `nhat_ky_lam_viec.md` |
| R4 | Lệch layout struct giữa C và Python | Trung bình | `assert` kích thước lúc import (7.2) + test vector dùng chung hai ngôn ngữ |
| R5 | Lệch bản đồ tag giữa GCS và drone | **Cao** | `map_crc` kiểm mỗi lần nối lại, chặn nhiệm vụ khi lệch (3.3) |
| R6 | Nhầm hệ trục trong 3D | Trung bình | Bảng kiểm chứng 5 trường hợp thành unit test (3.2) |
| R7 | Ăng-ten ESP32 đặt sai chỗ trên drone | Trung bình | Quy định lắp đặt ở 4.1; đo RSSI trước và sau khi lắp lên khung |
| R8 | ESP-NOW v1/v2 lệch nhau giữa hai module | Thấp | Chốt v1/250 byte; ghi phiên bản ESP-IDF vào tài liệu build |
| R9 | Đội vận hành hiểu nhầm vùng bay vẽ trong tab thiết kế là **hàng rào cứng** drone tự tôn trọng | **Cao** | Vùng bay hiện chỉ là ràng buộc phía GCS (8.4.5); nhãn "CẢNH BÁO — không cưỡng chế" hiện ngay trên lớp vùng bay ở cả sơ đồ 2D lẫn cảnh 3D; quyết định số 4 dưới đây |

**Bốn điểm cần quyết trước khi bắt đầu giai đoạn 1:**

1. **Bán kính vận hành mục tiêu**: con số này quyết định có cần LR mode ngay từ đầu hay không. Nó được
   nhập và hiển thị trong tab thiết kế khu vực (`site.design_radius_m`, mục 8.4.3), rồi đối chiếu với
   bán kính **đo được** từ T2 (`link_radius_meas_m`). Chừng nào T2 chưa chạy thì tab chỉ hiện "chưa đo"
   chứ không bịa số — nhưng con số **mục tiêu** vẫn cần chốt trước, vì nó là đầu vào để quyết định mua
   ăng-ten và có bật LR mode hay không.
2. **CSDL**: SQLite (đơn giản, đủ cho giai đoạn nghiên cứu) hay PostgreSQL ngay từ đầu (đỡ phải chuyển
   đổi sau)? Khuyến nghị SQLite cho giai đoạn 1–5, đánh giá lại ở giai đoạn 6.
3. **Cưỡng chế vùng bay phía drone**: vùng bay và vùng cấm vẽ ở tab thiết kế hiện **chỉ** là ràng buộc
   phía GCS. Có đưa chúng xuống drone để `failsafe_monitor_node` (hoặc FC) tự chặn khi vượt biên không?
   Việc này chạm hợp đồng failsafe nên cần quyết riêng; dải `msg_id` 0x1D–0x1F đã giữ chỗ sẵn. Khuyến
   nghị: giai đoạn đầu giữ nguyên cảnh báo phía GCS, bổ sung cưỡng chế sau khi T5 xong.
4. **Đối chiếu `mission_state_e` và `failsafe_type_e`**: giá trị số của enum phải giống hệt ba tầng. Hiện
   `drone_interfaces` phía Pi 4 đặt tên theo suy đoán (ghi nhận trong nhật ký). **Phải chốt bảng enum
   dùng chung trước khi viết `messages.py`**, nếu không toàn bộ mục 5.4 sẽ phải sửa lại.

---

## 14. Phụ lục

### 14.1 Bảng enum dùng chung (cần chốt với đội Pi 4 — mục cần quyết số 4)

```c
typedef enum {  // mission_state_e — PHẢI khớp drone_interfaces/MissionState.msg
    MS_IDLE = 0, MS_TAKEOFF = 1, MS_ENROUTE = 2, MS_MARKER_SEARCH = 3,
    MS_PRECISION_LAND = 4, MS_ACTUATE_GRIPPER = 5, MS_RETRY_LOITER = 6,
    MS_RTH = 7, MS_EMERGENCY_LAND = 8, MS_MISSION_COMPLETE = 9, MS_FAILSAFE = 10,
} mission_state_e;

typedef enum {  // failsafe_type_e
    FS_NONE = 0, FS_MARKER_TIMEOUT = 1, FS_GRIP_CONFIRM_FAIL = 2, FS_LINK_LOST = 3,
    FS_LOW_BATTERY = 4, FS_EKF_UNHEALTHY = 5, FS_FC_COMM_LOST = 6,
} failsafe_type_e;

typedef enum { GRIP_OPEN = 0, GRIP_CLOSED = 1, GRIP_MOVING = 2, GRIP_ERROR = 3 } gripper_state_e;
typedef enum { ACT_NONE = 0, ACT_PICKUP = 1, ACT_DROPOFF = 2, ACT_WAIT = 3 } wp_action_e;
```

### 14.2 Ánh xạ bản tin ESP-NOW ↔ topic ROS 2 (việc của `gcs_link_node`)

| Bản tin | Chiều | Topic/Service ROS 2 |
|---|---|---|
| `MISSION_BEGIN/WP/END` | vào | gom lại → publish `/mission/plan` (`MissionPlan`) |
| `MISSION_CTRL` | vào | service `~/mission_control` của `mission_manager_node` |
| `CMD_SIMPLE`, `CMD_GOTO` | vào | service của `fc_command_bridge_node` |
| `EMERGENCY` | vào | publish `/failsafe_event` (`FS_*`, ưu tiên cao nhất) + gọi service tương ứng |
| `TAGMAP_*` | vào | cập nhật tham số `known_tags` của `marker_pose_republisher_node` |
| `PARAM_SET` / `REQUEST` | vào | `ros2 param set/get` trên node tương ứng |
| `TELEM_FAST/SLOW` | ra | lấy từ `/telemetry/outgoing` (`TelemetryPacket`) |
| `MISSION_STATE` | ra | `/mission/state` |
| `TAG_DETECT` | ra | `/marker/tracking_quality` |
| `EVENT` | ra | `/failsafe_event` + `/diagnostics` |

`gcs_link_node` giữ nguyên hai nguyên tắc đã chốt trong `ke_hoach_thiet_ke_node.md`: hàng đợi ưu tiên
riêng cho lệnh khẩn cấp, và tự phát hiện mất kết nối bằng watchdog thay vì chờ GCS báo.

### 14.3 Tài liệu nguồn và liên quan

- `phan_tich_thiet_ke_he_thong_van_chuyen.md` — kiến trúc tổng thể hệ thống vận chuyển.
- `thiet_ke_tram_dieu_khien_mat_dat.md` — yêu cầu chức năng GCS bản đầu (tài liệu này kế thừa và cụ thể hóa).
- `docs/GIAO_UOC_FC_ROS2.md` — hợp đồng Pi 4 ↔ FC, **không** bị tài liệu này sửa đổi.
- `docs/ke_hoach_thiet_ke_node.md` — đặc tả 16 node ROS 2, nguồn của bảng ngưỡng mục 10.1.
- `docs/nhat_ky_lam_viec.md` — tình trạng triển khai và các rủi ro đã ghi nhận.
- `docs/yeu_cau_tinh_nang_phan_mem_dieu_khien_he_thong_bay.md` — tổng hợp tính năng ba tầng.

Tài liệu ngoài, đã kiểm chứng cho mục 1.3 và 4.2:
[ESP-NOW — ESP-IDF Programming Guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_now.html),
[ESP-NOW — ESP-FAQ](https://docs.espressif.com/projects/esp-faq/en/latest/application-solution/esp-now.html).
