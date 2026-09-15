# Validation

Nguồn: `python3 -m unittest discover -s tests/linux -v` trên máy Linux của Hub.

- **101/101** test unit trong `tests/linux/` passed (account add, bulk, hub, install, bootstrap npx/bun, pools, pool server, quota, subscription, switch).
- Installer ghi launcher `codexswitch`, không copy `auth.json` hay file account vào gói cài.
- Test dùng dữ liệu giả; không login account thật và không đọc credential máy host.

Quota/OAuth thật cần account trên máy người dùng, không nằm trong repo.
