# Hermes 重部署与数据保护

本文件把“网站代码更新”和“投标项目数据”分离。任何部署都不得删除、初始化、复制覆盖或迁移生产数据库，除非用户明确确认数据迁移方案。

## 重部署目标

- 应用代码来自 GitHub 的固定 Release 标签。
- 数据库、上传文件、环境文件始终使用服务器上的外置目录。
- 切换前后均读取项目数量；数量下降时立刻停止，不切换服务。
- Hermes 本地 Skill 可以自动更新；生产网站只能在用户明确确认后升级。

## Hermes 执行顺序

1. 通过 SSH 只读检查服务、Nginx、外置数据路径与当前项目数量。
2. 记录 `data_preflight.py` 输出中的 `projects` 数量为基线。
3. 对应用代码目录、SQLite 数据库与 storage 目录分别建立带时间戳备份。
4. 下载指定 GitHub Release 到新的版本目录，安装依赖；不得写入 `/data/bid-platform/instance`、`/data/bid-platform/storage` 或 `/etc/bid-platform.env`。
5. 使用原有环境文件启动候选版本，先检查本机 `/healthz`。
6. 运行 `data_preflight.py --minimum-projects <基线数量>`；失败则停止并回滚代码，不得尝试重新初始化数据库。
7. 经用户确认后才切换 systemd 服务到候选版本；验证 Nginx、本机健康检查和 HTTPS。
8. 再次运行数据核验，并随机打开既有项目详情。项目数或可读性异常时立即恢复上一版代码和数据备份。

## Hermes 到平台的稳定更新连接

先运行：

```bash
python scripts/bid_platform.py doctor
```

如果公网 HTTPS 在 Hermes 所在设备受 TUN、Fake-IP 或代理影响，使用 SSH 本地转发连接服务器内部端口。示例中的主机、账号和端口必须从现有部署核实，不能猜测或写入 Skill：

```bash
ssh -N -L 18011:127.0.0.1:8011 <ssh-user>@<server-host>
export BID_PLATFORM_API_URL=http://127.0.0.1:18011/api/v1
python scripts/bid_platform.py doctor
```

本地转发成功后，HTTPS/TLS 问题不会影响 Hermes 与站内 API 的通信；Token 仍只保存在 Hermes 本机环境中。不可使用 `--insecure`，不可把 Token 放入仓库、日志或聊天内容。

## 项目更新

用户明确确认预览后，Hermes 使用一条命令完成服务器校验和写入：

```bash
python scripts/bid_platform.py apply update <project-id> confirmed-patch.json --idempotency-key hermes-<unique-id>
```

脚本会自动读取当前版本、获取临时校验令牌并提交更新。确认信息缺失时会在发出网络请求前停止。
