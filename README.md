# Hermes 动态投标项目平台

这是一个由 Hermes 驱动的投标项目协作平台。招标文件的理解与结构设计在 Hermes 中完成；用户确认预览后，Hermes Skill 通过受控 API 将动态标签页、字段、表格、时间线、清单和风险提示写入网站。

网站本身不依赖 LLM 或 OCR 完成核心流程，也不把项目限制为一组固定的 AI 提取字段。项目仅保留名称、状态、负责人、摘要和内容版本等管理外壳，具体展示内容由版本化 Schema 决定。

## 核心能力

- 动态项目 Schema 1.0，支持 10 类安全渲染 block，拒绝任意 HTML、CSS 和 JavaScript
- `/api/v1` 项目查询、校验、新建、更新、跟进、状态变更和归档
- Bearer Token、最小 Scope、哈希存储、限流、幂等、乐观锁、审计和 2 MB 请求限制
- Hermes Skill 强制执行“预览 -> 用户明确确认 -> 写入”
- 工作台、项目列表、日程、动态详情、动态编辑器、归档、系统管理和登录页
- 桌面顶部导航与移动端底部导航，项目详情按 Hermes 提交的 section 生成标签页
- 旧数据可重复迁移，归档统一清理详细内容并保留最小记录

## 本地启动

推荐使用隔离数据目录启动：

```powershell
cd C:\项目记录表\bid_manager
.venv\Scripts\python.exe scripts\run_local.py `
  --data-root .local-data `
  --admin-password "<你的本地密码>" `
  --seed-demo `
  --port 8010
```

打开 `http://127.0.0.1:8010`。管理员用户名默认是 `admin`，密码必须由启动参数或环境变量明确提供。仓库不提供可预测的默认密码。

也可以复制 `.env.example` 并设置环境变量后启动：

```powershell
.venv\Scripts\python.exe -m uvicorn platform_server:app --host 127.0.0.1 --port 8010
```

生产模式 `BID_PLATFORM_ENV=production` 下，缺少 `BID_PLATFORM_SECRET_KEY` 或 `BID_PLATFORM_ADMIN_PASSWORD` 时应用会拒绝启动。

### 内部只读访问模式

主导航和统一登录尚未接入时，可信内网部署可设置：

```powershell
$env:BID_PLATFORM_AUTH_MODE='open'
```

此模式下同事可直接浏览工作台、项目、日程和归档，但网页端不能新增、编辑、更新状态或归档项目。项目更新仅能由 Hermes 使用已创建的专用 API Token 完成；成员权限、Hermes Token 与系统密钥设置会被隐藏并拒绝访问。接入主站导航或统一登录后，将变量改回 `required`（默认值）即可恢复登录和角色权限。

## Hermes 接入

1. 在 `BID_PLATFORM_AUTH_MODE=required` 时，管理员登录网站，在“系统管理 -> Hermes 接口”创建最小权限 Token。
2. 为 Skill 设置 `BID_PLATFORM_API_URL=https://你的域名/api/v1`。
3. 设置 `BID_PLATFORM_API_TOKEN`，Token 只在创建后显示一次。
4. 将 `hermes-skill/manage-bid-projects` 安装到 Hermes。
5. Hermes 先调用校验接口生成预览；用户明确确认后才提交带确认信息的写请求。

### Hermes Skill 自动同步

Skill 自带 `VERSION` 文件和 GitHub Release 同步工具，避免将本地复制副本长期遗忘。Hermes 可在已安装 Skill 目录执行：

```bash
python scripts/sync_skill.py --check
python scripts/sync_skill.py --apply
```

`--apply` 只更新 Hermes 本地 Skill，会先创建时间戳备份，不会读取或修改 API Token，也不会升级服务器。建议 Hermes 每个工作日 08:45 检查 GitHub Release；服务器版本升级仍需单独确认。连接异常时先执行 `python scripts/bid_platform.py health`，只有健康检查成功且 API 返回 `401 invalid_token` 时才排查 Token。

Skill 客户端示例：

```powershell
$env:BID_PLATFORM_API_URL='https://tb.hejiawuye.cn/api/v1'
$env:BID_PLATFORM_API_TOKEN='<token>'
python hermes-skill\manage-bid-projects\scripts\bid_platform.py schema
```

接口 Schema 位于 `docs/api/project.schema.json`，静态 OpenAPI 产物位于 `docs/api/openapi.json`，交互式文档位于运行实例的 `/docs`。

## 验证

完整测试需先安装开发依赖；它们不会进入生产运行环境：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.venv\Scripts\python.exe scripts\migrate_dynamic_projects.py
```

生产验收使用动态 API，不上传文件、不调用 AI：

```powershell
$env:BID_PLATFORM_ACCEPTANCE_USERNAME='<管理员账号>'
.venv\Scripts\python.exe scripts\production_acceptance_check.py --base-url https://bid.example.com
```

脚本会创建临时用户和最小权限 Token，校验 Schema、幂等创建、页面读取、更新、版本冲突、跟进、状态、归档与审计链，最后删除临时归档和用户并撤销 Token。

## 文档

- 产品需求：`docs/product/Hermes动态投标平台需求文档.md`
- 完整交付标准：`docs/product/GOAL-完整交付标准.md`
- 部署、迁移与回滚：`docs/DEPLOYMENT.md`
- 验证报告与复现命令：`docs/VERIFICATION.md`
- Hermes Skill：`hermes-skill/manage-bid-projects/SKILL.md`

旧站内 AI/OCR 路由默认关闭。只有显式设置 `BID_PLATFORM_ENABLE_LEGACY_AI=1` 才会临时开放，正式部署不得启用。

旧上传解析兼容代码的文档处理库已从正式依赖中移出。仅在离线迁移历史资料时才安装 `requirements-legacy.txt`；生产 Hermes 工作流使用 `requirements.txt`。
