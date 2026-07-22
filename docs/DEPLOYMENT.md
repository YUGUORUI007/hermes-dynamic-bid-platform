# 部署、迁移与回滚

## 生产前置条件

- Python 3.11 或更高版本
- 独立的应用目录、实例目录和存储目录
- Nginx 或同类 HTTPS 反向代理
- systemd、Windows Service 或容器编排负责进程守护
- `BID_PLATFORM_ENV=production`
- 随机生成且不入库的 `BID_PLATFORM_SECRET_KEY`
- 唯一的强管理员密码 `BID_PLATFORM_ADMIN_PASSWORD`
- `BID_PLATFORM_ENABLE_LEGACY_AI=0`
- `BID_PLATFORM_AUTH_MODE=required`（默认；接入统一登录前的可信内网可临时设为 `open`）

可使用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成密钥。生产环境变量文件权限应限制为服务账号可读。

## 部署步骤

1. 将源码发布到版本化目录，例如 `/opt/bid-platform/releases/20260721-1`。
2. 创建虚拟环境并执行 `pip install -r requirements.txt`。
3. 备份实例数据库与存储目录。
4. 运行 `python scripts/migrate_dynamic_projects.py` 做只读预检，确认失败数为 0。
5. 运行正式迁移 `python scripts/migrate_dynamic_projects.py --apply`。
6. 以 `uvicorn platform_server:app --host 127.0.0.1 --port 8010` 启动。
7. 检查 `/healthz`、`/login` 和 `/docs`，再切换反向代理。内部只读模式还应检查未携带 Cookie 的 `/workspace/projects` 可直接打开。
8. 在系统管理中创建专用 Hermes Token，并按最小权限分配 Scope。
9. 通过 `BID_PLATFORM_ACCEPTANCE_USERNAME` 提供验收账号，再运行 `scripts/production_acceptance_check.py --base-url <URL>` 并在交互提示中输入密码。脚本会创建并清理临时用户、Token 和归档记录，审计日志按合规要求保留。

单机 SQLite 部署只允许单写实例。API 限流当前保存在进程内；多 worker 或水平扩容前必须改用 PostgreSQL，并将限流状态迁移到 Redis 等共享存储。

## 数据迁移

迁移脚本会把旧固定字段转换为动态 section/block，并在已有动态内容时跳过写入，因此可重复执行。操作前必须对数据库文件做一致性备份；迁移期间暂停写入。

```bash
python scripts/migrate_dynamic_projects.py
python scripts/migrate_dynamic_projects.py --apply
```

迁移后核对：项目总数、失败数、随机抽样的标签页与字段、动态内容版本、归档记录和审计日志。

## 回滚

1. 停止应用，避免回滚期间继续写入。
2. 将反向代理切回上一版本应用目录。
3. 恢复迁移前数据库备份和对应存储目录；数据库与文件必须来自同一备份时间点。
4. 使用上一版本环境变量启动，检查健康状态和抽样项目。
5. 保留失败版本的数据副本和日志用于定位，不在原库上反复试验。

应用回滚不能只回退源码而保留不兼容数据库。Schema 主版本发生破坏性变化时，必须同时提供反向迁移器或执行整库恢复。

## 安全验收

- 仓库和部署包不含真实密码、Token 或 API Key
- 未登录访问 `/`、项目详情和内部页面会跳转登录
- 旧上传、解析、问答路由返回停用提示
- Token 仅哈希存储，可撤销、可过期、Scope 最小化
- 写请求带确认信息、幂等键；内容更新带校验令牌和版本号
- HTTPS、代理请求体限制、日志保留和备份恢复均已验证

## 内部只读访问模式

`BID_PLATFORM_AUTH_MODE=open` 仅用于可信公司内网、VPN 或已由主站网关限制访问的临时阶段。该模式让所有访问者直接浏览投标工作台，网页端不允许新增、编辑、状态修改或归档；成员管理、API Token 管理和系统密钥配置会被关闭。Hermes 仍需使用已有的最小权限 API Token 写入。不要将开放模式直接暴露到互联网。接入主域名导航、SSO 或人员权限后，将变量恢复为 `required` 并重启服务。
