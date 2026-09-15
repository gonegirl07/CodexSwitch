# CodexSwitch on Linux / Armbian / macOS

Bản desktop Python 3.10+ / Tk của [Gonegirl07](https://github.com/gonegirl07).
Mã giao diện và Codex integration nằm trong `linux/`.

## Cài một lệnh

```bash
npx github:gonegirl07/CodexSwitch
```

```bash
bunx github:gonegirl07/CodexSwitch
```

Lệnh cài launcher `codexswitch` vào `~/.local/bin`, giữ nguyên account local, rồi mở app.
Linux/Armbian cần `python3-tk` (installer gọi `apt` nếu thiếu). macOS: `brew install python-tk`.

## Chạy từ source

```bash
./install.sh
# hoặc
./run-linux.sh
```

Máy cần `python3`, `python3-tk`, Codex CLI và một terminal: GNOME Terminal,
Konsole, XFCE Terminal, xterm, hoặc Terminal.app trên macOS. Trên Debian/Armbian, nếu thiếu Tk:

```bash
sudo apt install python3-tk gnome-terminal
```

Chạy từ một phiên desktop; SSH không có display sẽ không mở được giao diện.
Settings cho phép chọn đường dẫn executable Codex, Codex home gốc và thư mục làm việc.

Để có biểu tượng ứng dụng, lệnh `codexswitch` và (trên Linux) API service tự khởi động:

```bash
./install.sh
# hoặc
python3 linux/install.py
```

Chạy không sudo; giữ nguyên account hiện có. Xem [cài đặt và chọn mạng LAN/Wi-Fi](POOLS.md).

## Sử dụng

Tính năng mới: **API Pools** tạo hai URL riêng cho Codex, pi, OpenClaw và công cụ
ảnh, xoay account theo quota 5h/weekly. Xem [hướng dẫn API Pools](POOLS.md).

1. Bấm **+ Add**, chọn Pool 1/Pool 2 nếu muốn gán account ngay và nhập ghi chú tùy chọn.
   Tên tài khoản luôn lấy từ email, không cần đặt tên thủ công.
2. Chọn **Browser OAuth**, **Import auth.json**, **Existing Codex** hoặc **Bulk login**.
   Không tạo profile trước khi đăng nhập/nhập dữ liệu thành công.
3. Hub tự kiểm tra quota khi mở ứng dụng, sau khi thêm/switch account và khoảng mỗi
   30 giây sau lần cập nhật thành công. Tối đa hai kiểm tra tự động chạy đồng thời;
   nhiều account sẽ xếp lượt. Lỗi chờ khoảng 60 giây trước khi thử lại. Có thể bấm
   **Refresh** để cập nhật ngay. Đây là polling, không phải luồng push từng token.
   Phần trăm hiển thị trong thanh xanh; dữ liệu cũ giữ lại và đánh dấu `stale` màu xám.
4. **Open** chọn project từ session metadata hoặc Browse rồi mở Codex trong terminal riêng.
   **More → Resume** dùng `codex resume --all` tại project đã chọn.
5. **Switch** trên account chuyển Codex local về provider OpenAI trực tiếp và
   auth.json của account đó; **Switch** trong tab Pool 1/2 chuyển sang pool tương ứng.
   Đóng phiên Codex cũ, switch rồi mở phiên mới. Auth/config được sao lưu trước khi
   đổi; model, lịch sử và thiết lập không liên quan được giữ. Xem chi tiết ở POOLS.md.
6. **More** có đăng nhập lại, mở thư mục, sửa ghi chú và xóa account.
   Tên thẻ luôn theo email của credential, không có ô nhập tên thủ công.
   Tài khoản cũ được cập nhật tên khi mở app hoặc Refresh. Profile chưa đăng nhập
   hiện `Pending login`; credential API key không có email hiện `API key account`.

### Quota Codex, GPT Reserve và Reset

Vùng quota chính chỉ lấy bucket `codex`, không lấy quota GPT Reserve dù provider
trả Reserve trước. Trong panel chi tiết, **GPT Reserve weekly** chỉ xuất hiện khi
account có dữ liệu weekly của Reserve (ở bất kỳ mức phần trăm nào). Quota refresh
cập nhật widget tại chỗ; không xây lại danh sách hoặc panel.

Thanh quota dựa trên thời lượng API, không dựa trên gói: `5h`, `Weekly`, `30d`
hoặc chu kỳ khác. Primary có thể là weekly/30d; cửa sổ không tồn tại được ẩn.
Account một cửa sổ dùng toàn bộ vùng quota; detail hiển thị thời điểm reset.
Thiếu thời lượng hiển thị `Unknown`, thiếu phần trăm là `--`; lỗi đọc giữ dữ liệu
cũ và stale. Dữ liệu mới lưu định danh primary/secondary, thời lượng và timestamp;
account cũ vẫn đọc được các dòng quota đã lưu, kể cả `43200 minutes`.
Kiểm thử: `xvfb-run -a python3 tests/linux/smoke_dynamic_quota.py`.

Nút **Reset(n)** nằm trên hàng Open / Switch của mỗi account (và trong detail / menu **…**).
Số lượt lấy từ payload quota gần nhất. `Reset(0)` vẫn
bấm được để xem thông báo hết lượt, không gửi yêu cầu tiêu hao; `Reset(?)` yêu cầu
Refresh vì chưa biết số lượt, không giả định là 0. Khi còn lượt, nút mở hộp xác nhận
email và việc tiêu hao một lượt; lựa chọn mặc định là **No**. Sau xác nhận, Hub đọc
lại số lượt rồi gọi `account/rateLimitResetCredit/consume` qua Codex CLI trong home
tạm riêng của đúng account. Không switch account, không chỉnh pool hay xóa lịch sử.

Mỗi lần reset lưu mã chống trùng trước khi gửi; bấm lại sau lỗi chưa rõ kết quả
sẽ dùng cùng mã. Không tự động retry thao tác tiêu hao. Provider quyết định cửa sổ
nào đủ điều kiện reset; kết quả có thể là đã reset, đã thực hiện trước đó, hết lượt,
hoặc không có cửa sổ đủ điều kiện. Hub đọc lại quota/số lượt sau đó. Nếu đọc lỗi,
giữ quota cũ với dấu stale; không tự đặt về 100% hoặc tự trừ số lượt.

Kiểm tra nút, xác nhận và luồng reset giả lập, không dùng lượt thật:
`xvfb-run -a python3 tests/linux/smoke_reset_quota.py`.

## Tab gói và nhãn đang sử dụng

Mỗi account có nhãn gói bên cạnh email, lấy từ `chatgpt_plan_type` trong metadata
token hoặc `planType` trong phản hồi quota. Tab **All** hiển thị tất cả; tab **Pool 1 / Pool 2 / …** luôn có theo số pool đã lưu;
tab Free, Plus, Pro, Business… tự xuất hiện khi có account tương ứng và có số lượng.
Thanh tab cuộn ngang khi nhiều gói. Tab gói chỉ lọc theo subscription; tab Pool N
chỉ hiện thành viên pool đó. Nút **+** sau tab Pool cuối tạo thêm pool; **−** xóa
pool đang chọn (tối thiểu 2). **Add/Remove** mở hai cột Available / In this pool
(Search email, All/Plus/Free/Other, Select all, Add/Remove; lưu ngay, không xóa
account khỏi Hub). Priority 0–4 (mặc định 1, số 0 dùng trước)
chỉ hiện cột số và mũi tên trên tab Pool, không hiện trên All/Plus. Danh sách chính
có Search và lọc Remaining / Exhausted quota.
Khi gói của account thay đổi, account chuyển tab nhưng detail đang mở vẫn giữ.
Nếu tab gói đang chọn không còn account, quay về All. Tab pool trống vẫn giữ. Account trong tab ẩn vẫn được
polling quota. Refresh quota đơn thuần không
tạo lại widget, panel chi tiết, hoặc thông báo thành công.

Trong mỗi tab, account `current` đứng đầu, tiếp theo là account đang chạy trong
pool, rồi các account còn lại theo thứ tự sẵn có. Chỉ sắp lại khi mức ưu tiên
thay đổi, giữ nguyên widget và tab đang chọn.

Thẻ account dùng một hàng gọn, email dài rút gọn; click account để xem email
đầy đủ và detail. Detail có vùng cuộn và đầy đủ nút Open, Switch, Refresh,
Reset(n), Log in again, Resume, Open folder, Details, Delete account và Add/Remove
từng pool. Menu **…** dùng cùng bộ thao tác; Reset/Delete giữ xác nhận bảo vệ.
Add/Remove pool lưu ngay, không xóa account khỏi Hub. Details có thể lưu email /
password / 2FA riêng (không dùng ô Note) để tự đăng nhập lại khi session mất
hoặc lỗi 401; không 2FA thì để trống. Nếu không lưu thì bỏ qua. Bulk add luôn lưu
bộ ba này vào `profiles/<id>/login.json`. Lỗi auth hiện mã HTTP trên thẻ/detail
(`401 · token_invalidated`); 403 hiện mã nhưng không auto-login. Xem [bộ lọc hai chiều và
đồng bộ chỉnh sửa pool](POOLS.md).

Kiểm thử offline: `xvfb-run -a python3 tests/linux/smoke_account_tabs.py` và
`xvfb-run -a python3 tests/linux/smoke_pool_filters.py`.

Cạnh tag gói là thời gian còn lại: `30d`, `29d`, `5h`, `1h`, `<1h`;
hết hạn hiện `Expired`, không có dữ liệu hiện `--`. Ngày/giờ được làm tròn xuống.
Nguồn là claim `chatgpt_subscription_active_until` trong metadata OAuth, cùng
trường mà [Cockpit sử dụng](https://github.com/jlcodes99/cockpit-tools/blob/main/src-tauri/src/models/codex.rs).
Hub không lấy thời hạn token `exp` hay ngày reset quota thay cho hạn gói, và
không gọi thêm API subscription. Credential không có claim này sẽ hiện `--`;
giá trị đã biết được giữ khi metadata tạm thiếu, đồng hồ cập nhật tại chỗ.

Chỉ hiển thị Pro5x/Pro20x nếu metadata ghi rõ `pro_5x`/`pro_20x`;
metadata chỉ ghi `pro` thì hiển thị Pro, `prolite` thì hiển thị Pro Lite.
Schema xuất từ Codex CLI cài trên máy chưa có enum `pro_5x`/`pro_20x`;
hai nhãn đó chỉ dùng nếu metadata token ghi rõ, không đảm bảo sẽ xuất hiện.
Không suy ra gói từ số phần trăm quota. `team` được hiển thị
Business. Thiếu metadata hoặc gói chưa nhận diện thì dùng Unknown; nếu đã biết
gói trước đó, lỗi/thiếu dữ liệu tạm thời giữ nguyên giá trị đã biết.
Thông tin token chỉ dùng hiển thị, không dùng làm bằng chứng cấp quyền.

- `current`: account khớp danh tính và provider OpenAI trong cấu hình Codex home
  mặc định của Settings, không phải dòng đang chọn. Switch sang pool/provider
  khác sẽ bỏ nhãn này dù auth.json cũ vẫn còn. Nhãn mô tả cấu hình cho lần mở
  Codex, không xác nhận những terminal cũ đã nạp lại cấu hình; profile/flag riêng
  của từng terminal nằm ngoài phạm vi nhãn này.
- `current pool 1` / `current pool 2`: account thực sự có request xử lý qua pool
  tương ứng, gồm thời gian chờ phản hồi và stream HTTP/WebSocket. Nhiều account
  có thể có nhãn đồng thời; cùng account cũng có thể chạy ở cả hai pool.
  Đọc trạng thái cục bộ khoảng mỗi giây; request ngắn hơn chu kỳ này có thể không
  kịp hiện. Nhãn bỏ khi request kết thúc hoặc trạng thái quá 10 giây/tiến trình
  không còn. Không dùng account được pool chọn gần nhất để suy đoán hoạt động.

Sau khi nâng cấp bản cài, mở lại Hub. Pool server cũng cần chạy mã mới để xuất
bộ đếm riêng từng pool; chọn lúc không có request để chạy
`systemctl --user restart codex-hub-pools.service`.

## Add account: OAuth trình duyệt và auth.json

**Browser OAuth:** mở **+ Add** là có sẵn link đăng nhập trong ô chỉ đọc. Bấm
**Copy login link** để dán vào trình duyệt bạn chọn; **Open browser** là tùy chọn,
không phải điều kiện để tạo link. **New login link** hủy phiên cũ và sinh link mới.
Hoàn tất đăng nhập ở trang OpenAI.
Không cần nhập password trong Hub. Callback tự động chỉ lắng nghe trên
`127.0.0.1:1455`, tách biệt với API pool LAN; kiểm tra PKCE/state và nhận code một lần.
Pool/ghi chú đang chọn lúc callback hoàn tất sẽ được dùng cho account mới.
Link dùng originator `codex_vscode` và scope giống luồng Cockpit được đối chiếu:
`openid profile email offline_access api.connectors.read api.connectors.invoke`.
State và PKCE sinh riêng cho mỗi phiên; không dùng lại link của ứng dụng khác.

Nếu callback không về được hoặc
port 1455 đang được Codex/9router sử dụng, copy URL đầy đủ trên thanh địa chỉ sau
khi browser chuyển tới `http://localhost:1455/auth/callback?...`, dán vào ô callback
và bấm **Finish with callback URL**. Hub không dừng listener của ứng dụng khác.
Có thể đăng nhập trên thiết bị khác rồi dán URL callback về Hub. Không chia sẻ URL
này: nó chứa authorization code. **Cancel login** hoặc đóng cửa sổ sẽ hủy phiên;
hết 10 phút cần bắt đầu lại. Account chỉ lưu sau khi đổi code lấy token thành công.

**Import auth.json:** chọn một/nhiều file bằng **Choose auth.json files…** (nhập ngay
sau khi chọn), hoặc dán toàn bộ JSON rồi **Import pasted JSON**. Hỗ trợ một object
`auth.json` hoặc mảng các object; tối đa 100 account và 2 MiB dữ liệu mỗi lần.
File cần credential ChatGPT: `tokens.access_token`, `refresh_token`, `id_token`,
`account_id`, và email trong token. Không hỗ trợ file chỉ chứa `OPENAI_API_KEY`
hay export JSON cockpit-tools/9router ở luồng này. Không cần sửa/xóa trường sẵn có.

Mọi object được kiểm tra cấu trúc trước khi nhập. Token/email được đọc offline để
hiển thị tên, **không phải bằng chứng token còn hợp lệ**; Refresh kiểm tra đăng nhập
và quota sau đó. Token hết hạn có refresh token có thể được làm mới bởi Codex/pool;
credential bị thu hồi vẫn cần OAuth lại. JSON input được xóa sau khi đọc hợp lệ.

**Existing Codex:** sao chép riêng auth.json trong Codex home đã chọn ở Settings,
không đổi đăng nhập mặc định. Cả ba cách đều bỏ qua email đã có, không ghi đè
credential cũ và chỉ gán account mới vào pool được tick. Muốn đổi pool của account
cũ hãy dùng Pools. Nếu Pools đang mở khi thêm, đóng rồi mở lại trước khi lưu để
không ghi đè danh sách mới. **Bulk login** mở luồng hàng loạt cũ; gán pool sau khi
batch hoàn tất. Không ghi password/token/callback vào log.

## Thêm tài khoản hàng loạt

Chọn **Bulk add**, dán danh sách hoặc **Load .txt…**, mỗi dòng:

```text
person@example.com|password|BASE32_TOTP_SECRET
another@example.com|password
```

Hỗ trợ dấu `|` hoặc tab; không dùng dấu phân cách đó bên trong mật khẩu.
2FA là secret Base32 của ứng dụng authenticator, không phải mã 6 số đang hiển thị.
Bỏ trống 2FA nếu không dùng. Dòng bắt đầu bằng `#` được bỏ qua.

Chọn 1–6 trình duyệt đồng thời (mặc định 3), bấm **Start**. Mỗi account dùng
browser context riêng, tự điền email/mật khẩu/TOTP và chấp thuận OAuth. Nếu có
CAPTCHA, OTP email, SSO hoặc yêu cầu xác minh điện thoại, xử lý trực tiếp trong
trình duyệt; app chờ tối đa 3 phút. Đã verify phone không thay thế mật khẩu,
2FA hoặc các yêu cầu xác nhận bổ sung của dịch vụ. Đây là đăng nhập account
có sẵn, không phải tạo tài khoản hoặc tự xác minh số điện thoại.

Bulk ưu tiên Google Chrome đã cài, dự phòng Chromium. Luồng Submit/TOTP và tùy
chọn khởi chạy trình duyệt được đối chiếu với import9router; không giả User-Agent.
Nếu trang OAuth lỗi tạm thời (HTML thay JSON, phiên hết hạn) hoặc callback không
về sau khi chấp thuận, Hub mở phiên mới với PKCE/state mới, tối đa 3 lần.
Sai mật khẩu hoặc yêu cầu xác minh thủ công không bị tự thử lại liên tục.
Lỗi hiển thị ở dòng account tương ứng; mật khẩu, TOTP và token không vào log.

Sau OAuth thành công, email thực tế phải khớp dòng yêu cầu thì mới tạo profile
và tự đọc quota. Email trùng trong danh sách được gộp; email đã có trong Hub
được bỏ qua, không thay credential cũ. Account lỗi không tạo profile rỗng.
**Stop** dừng hàng chờ và các browser của đợt hiện tại; yêu cầu đang chạy có thể
cần tối đa 30 giây để kết thúc. Account đã thêm thành công được giữ.

Hub không lưu mật khẩu/TOTP, screenshot đăng nhập hay URL callback vào file/log.
Ô nhập được xóa khi bắt đầu; file `.txt` bạn chọn vẫn nằm nguyên tại nguồn.
OAuth callback được bắt trong Chromium (kể cả HTTP redirect) và kiểm tra state
riêng từng account, không chiếm port 1455 hay gửi kết quả vào 9router.

Máy Ubuntu này đã có dependency từ tool import9router. Trên máy khác, cài thêm:

```bash
python3 -m pip install --user -r linux/requirements-bulk.txt
python3 -m playwright install chromium
```

Phần quản lý tài khoản thông thường vẫn chạy mà không cần các dependency bulk.

Quota dùng `codex app-server` qua stdio; không tạo model turn. Khi đọc thất bại,
snapshot cũ được giữ và đánh dấu stale/unavailable. Không tự polling quota.
Tài khoản API key có thể không hỗ trợ quota subscription.

Mỗi lần đọc quota dùng một Codex home tạm với bản sao `auth.json` của profile
và config tối thiểu, không chứa sessions hay database của profile. Cách này tránh
app-server bị timeout khi lập chỉ mục lịch sử lớn. Nếu CLI làm mới credential,
manager ghi lại vào profile chỉ khi credential nguồn chưa bị thay đổi. Thư mục
tạm được xóa sau yêu cầu. Quota lấy từ dịch vụ tài khoản chính thức; không sử dụng
endpoint/provider tùy chỉnh trong config của phiên làm việc.

## Dữ liệu

Mặc định: `${XDG_DATA_HOME:-$HOME/.local/share}/codex-cli-hub`.
Chọn một thư mục riêng khác nếu muốn portable:

```bash
./run-linux.sh --data-dir /absolute/path/to/hub-data
```

Thư mục dữ liệu/profile có quyền `700`; file tạo bởi manager có quyền `600`.
Khóa `flock` ngăn hai manager cùng dùng một thư mục dữ liệu.
Schema data của Hub độc lập; không trộn thư mục data với tool khác.

Chỉ `profiles/<id>/sessions` là symlink đến `<default Codex home>/sessions`.
Đích sessions phải tồn tại; app không tự tạo hay sửa Codex home gốc khi thêm profile.
Config gốc được sao chép, giữ comment/nội dung khác và đổi root setting
`cli_auth_credentials_store` thành `file`. CLI cũng nhận override này mỗi lần chạy.
Các biến credential/remote attachment đã biết được loại khỏi môi trường CLI con.

Xóa profile dùng cơ chế xóa an toàn theo file descriptor của Python trên Linux,
không đi theo symlink. Sessions dùng chung được giữ. Xóa private profile là vĩnh viễn;
đóng mọi terminal của profile trước khi xóa. Manager không tự đóng các terminal.
Symlink sessions là đọc/ghi: chính CLI có thể sửa session dùng chung.
Database/index/history vẫn tách riêng, nên resume picker không nhất thiết giống nhau.
Không đặt dữ liệu manager trong shared sessions hoặc dưới đường dẫn symlink.

## Kiểm thử và đóng gói

```bash
python3 -m unittest discover -s tests/linux -v
python3 tests/linux/smoke_bulk_browser.py
xvfb-run -a python3 tests/linux/smoke_bulk_ui.py
xvfb-run -a python3 tests/linux/smoke_gui.py
./scripts/package-linux.sh
```

Gói `dist/codex-cli-hub-linux.tar.gz` chỉ chứa mã chạy, logo, license và hướng dẫn.
Giải nén rồi chạy `codex-cli-hub-linux/run-linux.sh`. Không zip cả thư mục dữ liệu
đã sử dụng vì có credential. Không cần tải dependency từ pip.

Giao diện dùng danh sách thẻ Tk. Đăng nhập OAuth và quota thực cần tài khoản cùng
kết nối mạng. Bộ kiểm thử dùng dữ liệu giả, không sao chép hoặc thay credential
đang dùng trên máy.
