---
name: manage-bid-projects
description: Parse tender documents or user-provided bidding information, organize project-specific dynamic tabs and blocks, proactively check tender deadlines and ask for project progress, then create, update, follow up, change status, or archive projects through the Hejia bidding platform API after explicit confirmation. Use for 招标文件整理、投标项目登记、项目进度催办、定时巡检、动态字段同步、跟进记录和投后归档。
---

# Manage Bid Projects

把招标资料变成“这个项目自己的”结构，再同步到合家投标平台。资料决定标签页和字段，不要把所有项目硬塞进同一套模板。

## 不可突破的安全规则

在用户对**当前这次变更预览**给出明确确认前，绝不调用任何写入接口。

“看看”“整理一下”“分析一下”、只是上传文件，都只算分析，不算确认。只有“确认写入”“按这个更新”“确认归档”这类明确指令才可写入。禁止臆造或推断确认。

## 环境

需要：

- `BID_PLATFORM_API_URL`，生产环境用 `https://tb.hejiawuye.cn/api/v1`
- `BID_PLATFORM_API_TOKEN`

统一使用 `scripts/bid_platform.py`。它只依赖 Python 标准库，且永不打印 Token。

## 先判断任务类型，再行动

不要每次都走完整长流程。先判断用户这次要什么：

1. **新建项目**：用户给了招标文件/项目信息，目标是上线一个新项目。
2. **增量更新**：用户说“补充保证金已汇”“改负责人”“加一条跟进”“开标结果是未中标”。
3. **巡检催办**：定时任务或用户问“最近哪些项目要跟”。
4. **结果/归档**：用户确认中标、未中标、放弃，或要求归档。

原则：

- 只改用户这次提到的内容，不动其他已有正确信息。
- 预览要短：目标项目、将改哪些字段/标签、关键日期/风险、不确定项。
- 不要每次复读整份 schema、整份安全说明、整套命令百科。
- 用户确认后，优先一条命令完成：`apply create` / `apply update` / `apply status` / `apply followups` / `apply archive`。
- 回报也要短：项目名、负责人、状态、变更点、项目链接。失败时给错误码和可执行修复建议，不要假装成功。

## 业务状态怎么理解

平台生命周期状态：

`pending_signup` 待报名 → `registered` 已报名 → `pending_prequalification` 待资格预审（仅需要时） → `deposit_pending` 待缴保证金 → `deposit_done` 保证金已汇出 → `preparing` 待制作方案 → `sealed` 已封标 → `ready_deliver` 待送标 → `submitted` 已投 → `result_pending` 已投待结果 → `won` / `lost` / `abandoned` / `partner_completed`

网站会自动做这件事：

- 递交截止时间到了：未终态项目自动标成 **已投**（`submitted`）
- 开标时间到了：未终态项目自动进入 **已投待结果**（`result_pending`）

因此 Hermes 不要再机械地问“开标到了要不要改成已投”。应改为：

- 开标后优先确认 **结果**（中标 / 未中标 / 陪标完成 / 放弃）
- 只有用户明确说“其实没投”时，才改成 `abandoned`
- **硬归档**会清理详细正文与文件，必须单独确认；开标到期 ≠ 自动归档

并行执行状态放在 `content.workflow`，可同时推进，不要因为改一个事项覆盖另一个已完成事项。

## Skill 更新与连通检查

安装版以 GitHub 管理的 Skill 为准，版本见 `VERSION`。

- 每个工作日 08:45：`python scripts/sync_skill.py --check`
- 有新稳定版时：`python scripts/sync_skill.py --apply`（只更新本地 Skill，先备份，不碰 Token，不升级服务器）
- 更新后报告安装版本；服务器升级必须等用户批准
- 怀疑 Token 失效前，先 `python scripts/bid_platform.py health`
- 更新会话开始时优先 `python scripts/bid_platform.py doctor`
- 若公网域名被本地代理/TUN 干扰，用 SSH 本地转发到服务器内网端口，并把 `BID_PLATFORM_API_URL` 指到 `http://127.0.0.1:<port>/api/v1`。禁止 `--insecure`，禁止关闭 TLS 校验

## 定时巡检

由 Hermes 定时任务触发，网站本身不发起。

- 每个工作日 09:00 巡检在投项目
- 对 3 天内到期或已逾期的节点，15:00 再加检一次
- 关注：报名、资格预审、购标、澄清、踏勘、保证金、递交、开标、结果、开标后 14 天仍未退保证金
- 每个项目一次只问最关键的一个问题；同一工作日不重复追问同一未答问题
- 缺内部负责人时要问；采购人/代理/外部联系人不能当 `owner`
- 开标后优先问结果，不要再问“要不要改成已投”
- 所有回复先当信息收集；汇总预览并得到明确确认后才写入

## 标准工作流

1. 读用户给的文件和消息。
2. 先搜索是否已有项目：

```bash
python scripts/bid_platform.py projects --query "项目名称或招标编号"
```

3. 按 [references/schema.md](references/schema.md) 组织 payload。标签页来自真实资料；不知道的字段就省略，不要猜。
4. 新项目或现有项目没有负责人时，问公司内部负责人。
5. 给用户看简短预览：
   - 目标：新建 / 更新哪个项目
   - 系统字段变化
   - 标签页新增、替换或删除
   - 关键日期与风险
   - 不确定项
6. 等待用户明确确认。确认前停止。
7. 确认后把 payload 写成临时 JSON，并带上完整 `confirmation`，再执行一条 apply 命令。
8. 新建前若有重名候选，必须让用户决定“新建还是更新”，不能自行决定。
9. `apply` 会自行获取短期 validation token；Hermes 不要编造 token。

```json
{
  "confirmation": {
    "confirmed_by": "用户显示名",
    "confirmed_at": "ISO-8601 时间",
    "summary": "用户确认的那句变更摘要"
  }
}
```

10. 同一确认请求重试时才复用幂等键。
11. 成功后回报项目名、负责人、状态、变更点、URL。

## 命令

### 新建

```bash
python scripts/bid_platform.py apply create confirmed-payload.json --idempotency-key hermes-<unique-id>
```

### 更新

```bash
python scripts/bid_platform.py apply update <project-id> confirmed-patch.json --idempotency-key hermes-<unique-id>
```

若返回 `version_conflict`：重新读取项目，重建预览，再次确认。

### 跟进

```bash
python scripts/bid_platform.py apply followups <project-id> confirmed-followup.json --idempotency-key hermes-<unique-id>
```

### 改状态

首页生命周期状态用一个当前阶段。预览旧状态和新状态，确认后：

```bash
python scripts/bid_platform.py apply status <project-id> confirmed-status.json --idempotency-key hermes-<unique-id>
```

### 归档

说明归档可能清理文件。即使前面刚确认过更新，归档也要重新明确确认：

```bash
python scripts/bid_platform.py apply archive <project-id> confirmed-archive.json --idempotency-key hermes-<unique-id>
```

## 内容规则

- 系统外壳：`title`、`status`、`owner`、`summary`
- 系统元数据：`tender_code`、`buyer`、`agency`、`contact_name`、`contact_phone`、`signup_deadline`、`deposit_deadline`、`submission_datetime`、`bid_datetime`
- 项目专属标签页放 `content.sections`
- 只用文档化的 block 类型；禁止原始 HTML/CSS/JS/data URL
- `visibility: summary` 只给列表真正有用的信息
- `priority: urgent` 只用于真实截止或废标风险
- 增量更新时保留仍有用的旧标签页，除非用户明确要求替换/删除
- 不要通过这个 Skill 上传招标原件；网站不再做 AI/OCR 主流程
- 不要创建标题为“关键节点”的动态标签页；日期交给平台统一汇总

## 对话风格

- 像项目助理，不像接口说明书
- 先给结论，再给必要细节
- 一次只推进一个明确决策
- 用户已经很熟时，跳过重复解释
- 预览用短列表，不贴大段 JSON，除非用户要求看原始 payload
