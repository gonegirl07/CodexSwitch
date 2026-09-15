# Security

CodexSwitch lưu credential local dưới `${XDG_DATA_HOME:-$HOME/.local/share}/codex-cli-hub`. File không được mã hóa bởi app. Giữ thư mục data riêng tư.

Không đăng token, `auth.json`, `login.json`, password, TOTP, URL callback OAuth hoặc đường dẫn máy cá nhân lên issue công khai.

Báo lỗ hổng qua GitHub **Security → Report a vulnerability** nếu bật; nếu không, mở issue xin kênh riêng, không dán exploit hay dữ liệu nhạy cảm.

Xóa account trong Hub trước khi xóa thư mục data, để tháo symlink sessions an toàn.
