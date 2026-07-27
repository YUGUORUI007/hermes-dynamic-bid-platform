---
name: manage-bid-projects
description: Parse tender documents or user-provided bidding information, organize project-specific dynamic tabs and blocks, proactively check tender deadlines and ask for project progress, then create, update, follow up, change status, or archive projects through the Hejia bidding platform API after explicit confirmation. Use for 招标文件整理、投标项目登记、项目进度催办、定时巡检、动态字段同步、跟进记录和投后归档。
---

# Manage Bid Projects

Turn tender material into project-specific structured content and synchronize it to the Hejia bidding platform. Let the material determine the tabs and fields; do not force every project into one fixed template.

## Non-negotiable safety rule

Never call a write operation before the user explicitly confirms the exact proposed change in the current conversation.

Treat phrases such as “看看”, “整理一下”, “分析一下”, or merely uploading a file as analysis requests, not confirmation. Accept an unambiguous instruction such as “确认写入”, “按这个更新”, or “确认归档”. Never invent or infer confirmation.

## Environment

Require:

- `BID_PLATFORM_API_URL`, for example `https://bid.hejiauwye.cloud/api/v1`
- `BID_PLATFORM_API_TOKEN`

Use `scripts/bid_platform.py`. It uses only the Python standard library and never prints the token.

## Skill Updates And Connection Checks

Install this Skill as a GitHub-managed copy, not an untracked manual copy. Its installed version is in `VERSION`.

- On every workday at 08:45, run `python scripts/sync_skill.py --check` from the installed Skill directory.
- If a newer stable GitHub release is available, run `python scripts/sync_skill.py --apply` to update only the local Skill. The updater creates a timestamped backup and never reads or changes `BID_PLATFORM_API_TOKEN`.
- Report the installed Skill version after an update. Do not automatically deploy website/server releases; tell the user that a server upgrade is available and wait for approval.
- Before reporting an API token failure, run `python scripts/bid_platform.py health`. A network or TLS failure is not an invalid-token result.
- If health succeeds but API calls return `401 invalid_token`, report the status and ask the user to check the service token configuration. Never print the token.

## Scheduled Progress Check

When invoked by a scheduled Hermes task, inspect active projects before asking the user. The website does not initiate this task itself.

- Run on every workday at 09:00. Run an additional check at 15:00 for deadlines due within three calendar days or already overdue.
- List active projects, read each relevant project, and use the tender-derived dates and current project data to identify: signup, qualification-pre-review, document purchase, clarification, site visit, deposit, submission, bid opening, result, and deposit refund follow-up.
- Ask about a deadline within seven days; treat three days or fewer, overdue deadlines, and an unreturned deposit more than 14 days after bid opening as urgent.
- Ask only the most actionable question per project in one check. Do not repeat the same unanswered question more than once per workday; group routine questions into one concise message.
- Do not ask about a completed or not-applicable item unless later tender information creates a new requirement.
- Include `content.workflow.prequalification` only when the tender explicitly requires qualification-pre-review materials. Omit it when no such requirement exists; use `not_applicable` only when clearing an item that was previously shown.
- Ask for the internal project owner when it is missing. Never treat the purchaser, tender agent, or external contact as the owner.
- State the project name, deadline or reason, current recorded progress, and the exact information needed. Example: “XX 项目资格预审资料三天后截止，当前未确认是否提交。资料是否已提交？”
- Treat every reply as information collection only. Summarize the proposed updates and request explicit confirmation before any API write.

## Workflow

1. Read the supplied files and messages.
2. Search for a likely existing project before proposing a create operation:

   ```bash
   python scripts/bid_platform.py projects --query "项目名称或招标编号"
   ```

3. Build a payload following [references/schema.md](references/schema.md). Choose tabs from the actual material. Omit unsupported or unknown facts instead of guessing.
4. For a new project, or when the existing project has no owner, ask who inside the company is responsible for advancing this bid. Do not use a tender-agency contact, purchaser contact, or an external project manager as `owner`.
5. Present a concise preview containing:
   - target project or “new project”;
   - system fields being set;
   - tabs being added, replaced, or removed;
   - important dates and risks;
   - any uncertain information.
6. Ask the user to confirm that exact preview. Stop before any write command.
7. After explicit confirmation, save the intended payload to a temporary JSON file and validate it:

   ```bash
   python scripts/bid_platform.py validate payload.json
   ```

   For a partial update, use `validate --partial payload.json`.
8. If validation returns duplicate candidates, tell the user and obtain a separate decision to create or update. Do not decide silently.
9. Add the returned `validation_token` and a `confirmation` object to the same payload:

   ```json
   {
     "confirmation": {
       "confirmed_by": "the user's displayed name",
       "confirmed_at": "ISO-8601 timestamp",
       "summary": "the exact change the user confirmed"
     }
   }
   ```

10. Run the intended write command with a new stable idempotency key. Reuse that key only when retrying the exact same request.
11. Report the project name, internal owner, current status, changed tabs, and returned project URL. On failure, report the API error code and follow its repair guidance; do not claim success.

## Operations

### Create

```bash
python scripts/bid_platform.py create confirmed-payload.json --idempotency-key hermes-<unique-id>
```

### Update

Read the project immediately before updating. Use its returned `version`:

```bash
python scripts/bid_platform.py get <project-id>
python scripts/bid_platform.py validate --partial patch.json
python scripts/bid_platform.py update <project-id> confirmed-patch.json --version <version> --idempotency-key hermes-<unique-id>
```

If the API returns `version_conflict`, re-read the project, rebuild the preview, and ask the user to confirm again.

### Add follow-up

Preview the exact note, confirm, then run:

```bash
python scripts/bid_platform.py followup <project-id> confirmed-followup.json --idempotency-key hermes-<unique-id>
```

### Change status

Use one current lifecycle stage for the homepage, chosen from: `pending_signup`, `registered`, `pending_prequalification`, `deposit_pending`, `deposit_done`, `preparing`, `sealed`, `ready_deliver`, `submitted`, and `result_pending`. Preview the old and new status, confirm, then run:

```bash
python scripts/bid_platform.py status <project-id> confirmed-status.json --idempotency-key hermes-<unique-id>
```

### Archive

Explain that archive can trigger file cleanup. Require a fresh explicit confirmation even if the user confirmed an earlier update.

```bash
python scripts/bid_platform.py archive <project-id> confirmed-archive.json --idempotency-key hermes-<unique-id>
```

## Content rules

- Keep `title`, `status`, `owner`, and `summary` in the system envelope.
- Create project-specific tabs under `content.sections`.
- Use only documented block types; never send raw HTML, CSS, JavaScript, or data URLs.
- Use `visibility: summary` only for information useful in project lists.
- Use `priority: urgent` sparingly for real deadlines or disqualification risks.
- Preserve useful existing tabs during partial updates unless the user explicitly approves replacement or removal.
- Do not upload tender files through this skill. The website no longer performs AI/OCR analysis.
