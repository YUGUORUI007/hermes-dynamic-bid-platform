---
name: manage-bid-projects
description: Parse tender documents or user-provided bidding information, organize project-specific dynamic tabs and blocks, ask the user for explicit confirmation, and then create, update, follow up, change status, or archive projects through the Hejia bidding platform API. Use for 招标文件整理、投标项目登记、项目进度更新、动态字段同步、跟进记录和投后归档。
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

## Workflow

1. Read the supplied files and messages.
2. Search for a likely existing project before proposing a create operation:

   ```bash
   python scripts/bid_platform.py projects --query "项目名称或招标编号"
   ```

3. Build a payload following [references/schema.md](references/schema.md). Choose tabs from the actual material. Omit unsupported or unknown facts instead of guessing.
4. Present a concise preview containing:
   - target project or “new project”;
   - system fields being set;
   - tabs being added, replaced, or removed;
   - important dates and risks;
   - any uncertain information.
5. Ask the user to confirm that exact preview. Stop before any write command.
6. After explicit confirmation, save the intended payload to a temporary JSON file and validate it:

   ```bash
   python scripts/bid_platform.py validate payload.json
   ```

   For a partial update, use `validate --partial payload.json`.
7. If validation returns duplicate candidates, tell the user and obtain a separate decision to create or update. Do not decide silently.
8. Add the returned `validation_token` and a `confirmation` object to the same payload:

   ```json
   {
     "confirmation": {
       "confirmed_by": "the user's displayed name",
       "confirmed_at": "ISO-8601 timestamp",
       "summary": "the exact change the user confirmed"
     }
   }
   ```

9. Run the intended write command with a new stable idempotency key. Reuse that key only when retrying the exact same request.
10. Report the project name, version, changed tabs, and returned project URL. On failure, report the API error code and follow its repair guidance; do not claim success.

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

Preview the old and new status, confirm, then run:

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

