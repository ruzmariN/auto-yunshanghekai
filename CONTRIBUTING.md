# 参与贡献

感谢参与改进。提交代码前请：

1. 使用 Python 3.11 或更高版本创建虚拟环境。
2. 执行 `pip install -e ".[browser,dev]"`。
3. 执行 `python -m pytest -q`，确保全部测试通过。
4. 不要提交 `.cloudriver/`、HAR 抓包、Cookie、CAS ticket 或真实账号信息。
5. 新增接口行为时，应以网站正常前端协议和服务器响应为依据，并添加测试。

提交 issue 时请提供经过脱敏的日志、操作系统、Python 版本和复现步骤。
