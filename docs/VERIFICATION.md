# 交付验证报告

验证日期：2026-07-21

## 自动化结果

- Python 测试：37/37 通过
- 隔离 API/网页闭环：45 项检查通过
- Hermes Skill 端到端：8 项检查通过，覆盖校验、新建、读取、增量更新和审计链
- 生产验收：14 项检查通过，覆盖临时用户、最小权限 Token、幂等、页面、版本冲突、跟进、状态、归档、审计和清理
- 迁移预检：现有 9 个项目均可迁移，0 失败
- 浏览器验收：375、768、1024、1440 四个视口，7 个页面共 64 项检查通过
- 视觉产物：28 张全页面截图，位于 `tmp/visual-acceptance-release`
- 核心依赖隔离：模拟缺少 PDF/DOCX/OCR 库时主应用导入成功
- 凭证扫描：未发现历史密码、默认密码或可用 `hbp_live_` Token

## 可复现命令

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.venv\Scripts\python.exe scripts\migrate_dynamic_projects.py
.venv\Scripts\python.exe tests\hermes_skill_runner.py
```

视觉测试首次运行前安装 Playwright 和浏览器：

```powershell
pnpm install
pnpm exec playwright install chromium
node tests\visual_acceptance.js http://127.0.0.1:8010 admin '<本地测试密码>' tmp\visual-acceptance
```

生产验收推荐通过环境变量提供凭证：

```powershell
$env:BID_PLATFORM_ACCEPTANCE_USERNAME='<管理员账号>'
.venv\Scripts\python.exe scripts\production_acceptance_check.py --base-url https://bid.example.com
```

## GOAL 证据映射

- G1：确认后的原型、统一 Jinja2 模板、4 视口 28 张截图
- G2：Schema 1.0、模型、版本记录，以及覆盖空库/正常/异常和真实 SQLite 副本重复执行的迁移测试
- G3：`/api/v1`、OpenAPI 请求示例以及鉴权、Scope、幂等、锁、XSS、请求体和错误码测试
- G4：可安装 Skill 目录及真实 CLI 端到端隔离测试
- G5：动态标签渲染、可视化 block 编辑器、JSON 高级模式、实时预览、未保存提示、打印和移动端布局
- G6：旧 AI 路由默认停用、旧文档依赖移出正式 requirements、安全配置、部署/回滚和生产验收

进程内限流仍属于明确的单实例边界。多 worker 或水平扩容必须先接入共享限流存储，详见 `DEPLOYMENT.md`。
