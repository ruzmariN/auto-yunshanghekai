# 安全说明

## 敏感文件

程序会在 `.cloudriver/` 中保存运行数据，其中：

- `session.json` 包含登录 Cookie，等同于当前账号的临时登录凭据。
- `config.json` 是本地配置。
- `checkpoint.json` 是断点信息，不作为完成状态依据。
- `manager.log` 是运行日志。

`.cloudriver/` 已加入 `.gitignore`。请勿上传、提交或发送 `session.json`，也不要把完整 HAR 抓包提交到公开仓库。

## 登录安全

程序只在平台官方页面中完成账号登录，不读取或保存明文密码。自动登录窗口使用独立临时浏览器配置，不复用、占用或锁定日常浏览器用户目录。

## 报告问题

请通过仓库的 Security Advisory 私下报告可能导致 Cookie、CAS ticket 或个人信息泄露的问题。报告中请先清除真实账号、Cookie、ticket、用户 ID 和课程信息。
