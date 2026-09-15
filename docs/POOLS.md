# API pool trên Ubuntu

Mở **API Pools** trong Hub. Mỗi tab có danh sách account, port, API key và các
ngưỡng quota còn lại riêng. Mặc định có Pool 1 và Pool 2. Trên Hub, nút **+** ngay
sau tab Pool cuối tạo thêm pool (bao nhiêu tùy bạn: port kế tiếp còn trống, key
riêng, danh sách thành viên riêng). **Create pool** trong cửa sổ Pools làm cùng
việc. Chọn account trong **Available accounts**, bấm **Add →**
để đưa vào danh sách **Accounts in this pool**. **← Remove** chỉ bỏ khỏi pool, không
xóa account khỏi Hub. Các pool lưu danh sách độc lập; có thể dùng chung
account nếu bạn chủ động thêm vào nhiều pool. Bấm **Save pools**, rồi **Start APIs**.
**Copy local URL**, **Copy LAN URL**, **Copy API key**, **Client configs…** cung cấp thông
tin kết nối; không dùng token đăng nhập ChatGPT làm API key của client.

Hai bên danh sách đều có **Search email** và tab **All / Plus / Free / Other**.
Tìm không phân biệt hoa/thường, kết hợp với tab gói; Other gồm Pro, Business,
Unknown và các gói khác. Số `12/100 accounts` là số khớp bộ lọc / tổng số bên đó.
Chọn nhiều dòng để Add/Remove; chỉ dòng đang hiển thị và được chọn bị tác động.
Đổi bộ lọc bỏ lựa chọn bị ẩn, tránh thêm/gỡ nhầm. Bộ lọc độc lập giữa hai bên,
từng pool và được giữ sau thao tác; thay đổi trong cửa sổ này vẫn cần **Save pools**.

Danh sách chính của Hub có tab **Pool 1 / Pool 2 / …** cạnh All / Free / Plus,
cùng Search email/tên và lọc **All / Remaining / Exhausted** (theo quota trên thẻ:
còn % thì Remaining; 0%, không cửa sổ, hoặc stale/unusable thì Exhausted).
Nút **+** sau tab Pool cuối tạo pool mới và mở tab đó; **−** xóa pool đang mở
(còn tối thiểu 2 pool; account vẫn ở Hub; cần restart API để bỏ port cũ). Từ tab
Pool, **Add/Remove** mở hai cột như cửa sổ Pools: Available / In this pool, Search
email, All/Plus/Free/Other, **Select all** (các dòng đang hiện sau filter), **Add →**
và **← Remove**. Lưu ngay, không cần Save pools. Cửa sổ Pools có **Delete pool**
cạnh Create pool.
Account có **priority** 0–4 trên cột riêng của tab Pool (mặc định 1, 0 dùng trước;
cùng mức thì chọn ngẫu nhiên; sticky-current vẫn thắng khi còn đủ quota). Nếu Pools đang mở, các thay đổi chưa lưu ở account khác và các
ô thiết lập được giữ; thao tác trực tiếp thắng chỉnh sửa đang chờ của đúng
account/pool đó. Nếu cấu hình đã bị sửa từ bên ngoài, Hub báo lỗi và không ghi
đè; giữ cửa sổ để kiểm tra chỉnh sửa trước khi đóng/mở lại. Không cần restart API
để áp dụng membership cho request tiếp theo.

URL mặc định, chỉ truy cập trên máy Ubuntu này:

| Pool | Base URL |
| --- | --- |
| 1 | `http://127.0.0.1:8311/v1` |
| 2 | `http://127.0.0.1:8312/v1` |

Cài phần phụ thuộc tùy chọn trước khi chạy trên máy mới:

```bash
python3 -m pip install --user -r linux/requirements-pools.txt
```

Hoặc chạy server trực tiếp để xem lỗi khởi động:

```bash
python3 linux/pool_server.py --data-dir "$HOME/.local/share/codex-cli-hub"
```

Đóng cửa sổ Hub không dừng API. Dùng **Stop APIs** để dừng. Thay port/mạng áp dụng
trong vài giây; membership, ngưỡng và model ảnh áp dụng cho request tiếp theo.
Không tự sửa config hiện có của Codex, pi hoặc OpenClaw.

## Cài vào Ubuntu

```bash
npx github:gonegirl07/CodexSwitch
# hoặc
python3 linux/install.py
```

Không dùng sudo. Bộ cài sao chép mã vào `~/.local/share/codexswitch`, tạo
lệnh `~/.local/bin/codexswitch` (và alias `codex-hub`), biểu tượng **CodexSwitch** trong menu ứng dụng,
và (Linux) service người dùng `codex-hub-pools.service`. Giữ nguyên data directory hiện tại.
Nếu đang chạy API trực tiếp, Stop APIs trước khi cài. Đóng cửa sổ Hub cũ rồi mở
bằng `codexswitch` (hoặc đường dẫn đầy đủ nếu PATH chưa chứa `~/.local/bin`).

```bash
systemctl --user status codex-hub-pools
systemctl --user restart codex-hub-pools
journalctl --user -u codex-hub-pools -n 30
```

Service được enable tự chạy; để chạy ngay sau boot mà chưa đăng nhập cần
`Linger=yes` (`loginctl show-user "$USER" -p Linger`). Máy Ubuntu này đã bật sẵn.
Trên máy khác có thể cần quản trị viên bật bằng `loginctl enable-linger <user>`.
Cài lại để cập nhật mã; `--no-start` chỉ cài file, không enable/start service.
Dừng tự khởi động bằng `systemctl --user disable --now codex-hub-pools`.

## Chọn Ethernet hoặc Wi-Fi

Trong mỗi tab chọn **Share API on network**, rồi **Save pools**:

- **Local only**: chỉ máy này, mặc định an toàn cho cấu hình cũ.
- **Ethernet / LAN · tên card · IP** hoặc **Wi-Fi · tên card · IP**: chia sẻ
  API trên card đó. Pool 1 và Pool 2 có thể chọn mạng khác nhau.
- **Reload accounts/networks** cập nhật account mới và danh sách card mạng.
- **Copy LAN URL** lấy URL theo IP hiện tại. Client trên máy khác dùng URL này
  cùng API key của đúng pool. Client configs mặc định vẫn minh họa localhost;
  thay base URL bằng LAN URL đã copy nếu chạy trên máy khác.

Lưu tên interface, không lưu IP cố định; mỗi vài giây server kiểm tra địa chỉ,
gỡ listener cũ và thêm IP mới khi DHCP đổi. Mất mạng thì chỉ còn localhost;
không tự mở toàn bộ interface. Client cần cập nhật URL khi IP máy chủ đổi.
Chỉ hỗ trợ IPv4 riêng 10/8, 172.16/12 và 192.168/16; không bind wildcard/public IP.
Kết nối đang xử lý có thể hoàn tất sau khi đổi mạng.

LAN dùng HTTP, **không mã hóa API key và nội dung trên đường truyền**: chỉ dùng
mạng tin cậy. Không tự tắt/mở firewall và không cấu hình port-forward Internet.
Nếu firewall chặn, chỉ cho phép subnet tin cậy vào port pool cần dùng; đừng mở
toàn Internet. Đường LAN đã được kiểm tra bằng kết nối từ máy chủ tới địa chỉ
interface; vẫn cần thử từ thiết bị LAN thực tế để kiểm tra firewall/routing.

## Xoay tài khoản

- Giữ account hiện tại khi mọi cửa sổ thực sự có đều **lớn hơn ngưỡng tương ứng**. Ví dụ đặt 5h=10,
  weekly=15: account còn 10% 5h **hoặc** 15% weekly sẽ bị bỏ qua từ request sau.
- Ngưỡng 0 vẫn không dùng cửa sổ đã hết quota. Quota không xác minh được thì không
  đoán; account bị bỏ qua. Dữ liệu quota được kiểm tra lại khi cũ hoặc sau response
  hoàn tất. Account chỉ báo một cửa sổ không cần điều chỉnh ngưỡng cửa sổ thiếu.
- Chu kỳ được xác định bằng thời lượng API, không phải vị trí primary/secondary
  hay nhãn Free/Plus. Có ngưỡng 5h, weekly, 30d và other/unknown. Hai ngưỡng mới
  mặc định 5%; cấu hình cũ, API key và danh sách thành viên được giữ nguyên.
  Thời lượng không được cung cấp dùng ngưỡng other; dữ liệu thời lượng sai hoặc
  phần trăm/reset lỗi bị chặn. Quota GPT Reserve không thay thế quota Codex chính.
- HTTP 429 trước khi trả output cho client: thử account kế tiếp. Không phát lại
  request đã bắt đầu streaming để tránh chạy tool hoặc tính phí hai lần.
- Hết account đủ điều kiện: trả HTTP 429 `pool_exhausted`. Chờ reset, chỉnh ngưỡng
  hoặc thêm account đã đăng nhập hợp lệ. Phone-verified vẫn cần hoàn tất OAuth.
- Cùng account ở hai pool dùng chung quota và thời gian chờ 429; hai URL không
  nhân đôi hạn mức. Request đồng thời có thể cùng dùng một account; ngưỡng là
  điều kiện chọn trước request, không phải giới hạn cứng cắt request đang chạy.
- `previous_response_id` gắn với account đã tạo nó. Nếu phải đổi account hoặc
  server vừa restart, trả 409 và yêu cầu client gửi lại đầy đủ input. Không đảm bảo
  chuyển tiếp mọi trạng thái hội thoại giữa các account. Không xóa được account
  khỏi Hub khi còn trong pool; hãy bỏ khỏi mọi pool và đợi request đang chạy hoàn tất trước.
- WebSocket dùng `store=false`: ID cũ có thể mất hiệu lực sau reconnect dù vẫn
  cùng account. Với lỗi 400 `Invalid \`previous_response_id\`.` thiếu mã lỗi,
  proxy bổ sung `previous_response_not_found` trước khi có output và đóng kết nối
  lỗi để Codex CLI tự retry bằng đầy đủ ngữ cảnh. Proxy không tự replay request,
  không xóa ID khỏi input dạng delta, và không biến lỗi sau output thành retry.
  Client khác phải hỗ trợ gửi lại full input khi nhận mã này. CLI có thể hiện
  thông báo reconnect ngắn; đây không phải bảo đảm mọi lỗi upstream đều tự hồi phục.
  Test offline với Codex cài trên máy: `python3 tests/linux/smoke_codex_recovery.py`.

## Codex CLI

### Thiết lập nhanh bằng Switch

Mở **Pools → Pool 1/Pool 2 → Switch**. Pool phải có thành viên đã lưu và API đang
chạy. Hub tự cấu hình provider, localhost URL và key đúng pool trong Codex home
mặc định của Settings; không cần export biến môi trường. Pool đang chia sẻ LAN
vẫn dùng localhost cho Codex trên cùng máy. **Switch** trên thẻ account chuyển về
provider `openai` và auth.json account đó, kể cả sau khi đã dùng pool.

Switch áp dụng root provider và provider của profile mặc định nếu có; giữ nguyên
model đang chọn. Model đó phải được account/pool cấp quyền. Cờ dòng lệnh `-p`,
`-c`, hoặc cấu hình cấp project có thể ghi đè cấu hình người dùng; khi kiểm tra
switch hãy mở phiên mới không kèm provider override.

Hub sao lưu config/auth trước mỗi lần đổi vào data directory `switch-backups/`.
Các file này chứa credential: không chia sẻ hay đưa vào Git. Pool key được ghi
trong `experimental_bearer_token` của provider, file config quyền 0600; đây là
cách cấu hình tiện dụng cục bộ, không phải secret manager. Auth.json không bị đổi
khi switch pool. Khi cần khôi phục, đóng Codex rồi dùng bản sao trong thư mục backup
được hiển thị sau Switch; file manifest cho biết file nào tồn tại trước đó.
Các phiên Codex đã mở không bị chuyển ngầm; hãy đóng/mở lại.

### Cấu hình thủ công

Đặt biến môi trường `CODEX_POOL_KEY` bằng key được copy trong Hub; đừng lưu key
vào repository. Ví dụ cấu hình cho Pool 1 trong config người dùng hoặc CODEX_HOME
riêng (chọn model có trong `GET /v1/models`):

```toml
model_provider = "pool_1"
model = "MODEL_FROM_V1_MODELS"

[model_providers.pool_1]
name = "Hub Pool 1"
base_url = "http://127.0.0.1:8311/v1"
env_key = "CODEX_POOL_KEY"
wire_api = "responses"
supports_websockets = true
```

Pool 2 đổi tên provider, port thành 8312 và dùng key Pool 2. Native `/responses`
giữ model ID, tools, reasoning, encrypted content và SSE event; có WebSocket,
`/responses/lite`, `/responses/compact`. `/models` lấy catalog thật từ account
được chọn, không hardcode danh sách model. Quyền truy cập model vẫn phụ thuộc
account và backend; không cam kết mọi account chạy được mọi model.

## pi và OpenClaw

Trong `~/.pi/agent/models.json`, gộp provider này với cấu hình đang có:

```json
{"providers":{"pool_1":{"baseUrl":"http://127.0.0.1:8311/v1","api":"openai-responses","apiKey":"CODEX_POOL_KEY","models":[{"id":"MODEL_FROM_V1_MODELS","reasoning":true,"input":["text","image"]}]}}}
```

Thay `MODEL_FROM_V1_MODELS` bằng ID thật trong catalog. Trong OpenClaw, gộp vào
`models.providers`, sau đó chọn model `pool_1/<ID model>`:

```json
{"models":{"providers":{"pool_1":{"baseUrl":"http://127.0.0.1:8311/v1","api":"openai-responses","apiKey":"${CODEX_POOL_KEY}","models":[{"id":"MODEL_FROM_V1_MODELS","name":"MODEL_FROM_V1_MODELS","reasoning":true,"input":["text","image"]}]}}}}
```

Ưu tiên `openai-responses`. Client chỉ có Chat Completions có thể dùng
`/v1/chat/completions`: hỗ trợ text, ảnh đầu vào, function calls và streaming.
Không phải mọi tùy chọn Platform đều tương thích subscription Codex. Đặc biệt
backend từ chối `max_output_tokens`; Hub bỏ trường này (kể cả giá trị chuyển từ
Chat `max_tokens`) và báo header `X-Hub-Ignored-Parameters: max_output_tokens`.
**Không có giới hạn output token được cưỡng chế bởi proxy.**

## Công cụ tạo ảnh

`POST /v1/images/generations` dùng chính account trong pool qua Codex/ChatGPT
OAuth, không cần OpenAI Platform API key. Hub forward sang Codex Images
(`https://chatgpt.com/backend-api/codex/images/generations`), không bọc thành
tool `image_generation` trên `/codex/responses`. Chat/text vẫn đi `/codex/responses`.

Đặt **Native response model for image bridge** trong tab pool để catalog
`GET /v1/models` hiện các ID ảnh tương thích. Trường này không còn là model
Responses cho bridge.

Tool kết nối base URL pool như trên, dùng API key pool và gửi:

```json
{"model":"gpt-image-2.5-flare","prompt":"A blue square on a white background","n":1,"response_format":"b64_json"}
```

Endpoint chấp nhận `gpt-image-2.5-sunburst`,
`gpt-image-2.5-sunburst-2026-09-08`, `gpt-image-2.5-flare`,
`gpt-image-2.5-flare-2026-09-08` và alias `gpt-image-1` / `gpt-image-2`.
Bridge chỉ hỗ trợ n=1, non-stream và b64_json; chưa có `/images/edits`,
multipart hoặc URL ảnh.

Giới hạn đã biết: account **Free** bị Codex Images từ chối HTTP 403
`Forbidden`. Cần Plus/Pro/Go (hoặc gói có quyền ảnh) trong pool. Khi upstream
không trả `data[].b64_json`, Hub trả `image_not_generated` và không giả lập ảnh.

ChatGPT Desktop trên Windows trỏ pool bằng `CODEX_HOME` đang dùng (ví dụ
`D:\CodexData\.codex`), không phải luôn `%USERPROFILE%\.codex`. Dùng auth API
key của pool, không vừa login ChatGPT vừa custom provider:

```toml
model_provider = "pool_1"
model = "MODEL_FROM_V1_MODELS"
cli_auth_credentials_store = "file"

[model_providers.pool_1]
name = "Hub Pool 1"
base_url = "http://LAN_IP:8311/v1"
env_key = "CODEX_POOL_KEY"
wire_api = "responses"
supports_websockets = true
```

Đặt `CODEX_POOL_KEY` bằng biến môi trường user (`setx`), rồi Quit hẳn app.

Smoke test an toàn, không in base64. Dùng pool có account Plus, không phải pool
toàn Free:

```bash
curl -s http://127.0.0.1:8311/v1/images/generations \
  -H "Authorization: Bearer $CODEX_POOL_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2.5-flare","prompt":"A blue square on a white background","n":1,"response_format":"b64_json"}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["data"][0]["b64_json"]))'
```

## Bảo mật và kiểm chứng

Server mặc định chỉ bind `127.0.0.1`; chỉ mở LAN khi chọn interface và Save.
Mỗi pool vẫn có key riêng, không tự mở Internet.
Key, auth và runtime status ở data directory, ghi atomic với quyền private;
không nằm trong gói phát hành. Không ghi prompt hoặc token vào access log.
Chỉ dùng tài khoản bạn có quyền sử dụng; proxy không nâng quyền model hay quota.

Kiểm thử tự động: `python3 -m unittest discover -s tests/linux -v`.
Giao diện/server lifecycle: `xvfb-run -a python3 tests/linux/smoke_gui.py`.
Đã chạy request thật bằng Codex CLI (SSE và cấu hình bật WebSocket), pi Responses
transport và catalog model trên hai pool. OpenClaw có mẫu cấu hình, chưa kiểm thử
end-to-end trong một phiên agent OpenClaw. Kiểm thử tùy chọn
`python3 tests/linux/smoke_pool_clients.py` dùng quota thật và không sửa config client.

Ngày 2026-09-12, Images native (`/codex/images/generations`) trả HTTP 200 với
ảnh base64 qua Pool 1 Plus, model `gpt-image-2.5-flare`. Cùng ngày Pool 2 Free
bị 403 Forbidden. Bridge Responses cũ không tạo `image_generation_call`.
Luôn kiểm tra catalog và gói tài khoản khi chọn pool cho ảnh.
