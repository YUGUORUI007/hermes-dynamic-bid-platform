# Dynamic project schema

Use UTF-8 JSON. Fetch the authoritative current schema with:

```bash
python scripts/bid_platform.py schema
```

## Project envelope

```json
{
  "title": "项目名称",
  "status": "tracking",
  "owner": "负责人",
  "summary": "用于列表和详情页顶部的简短摘要",
  "schema_version": "1.0",
  "content": {"sections": []},
  "change_summary": "本次变更摘要"
}
```

System lifecycle statuses currently used by the platform: `tracking`, `pending_signup`, `registered`, `deposit_pending`, `deposit_done`, `preparing`, `sealed`, `ready_deliver`, `submitted`, `result_pending`, `won`, `lost`, `abandoned`, `partner_completed`, `archived`.

## Section

Each section renders as a project-detail tab.

```json
{
  "id": "key-dates",
  "title": "关键节点",
  "description": "报名、递交及开标安排",
  "icon": "calendar-range",
  "priority": "important",
  "visibility": "detail",
  "collapsible": false,
  "blocks": []
}
```

Allowed priorities: `normal`, `important`, `urgent`.  
Allowed visibility: `detail`, `summary`.  
Allowed widths: `full`, `half`, `third`.

## Blocks

All blocks require `id`, `type`, and may use `width` and `title`.

### Field

```json
{"id":"budget","type":"field","label":"最高限价","value":"1286 万元","semantic":"amount","width":"half"}
```

Semantics: `text`, `date`, `datetime`, `amount`, `phone`, `email`, `url`.

### Status

```json
{"id":"deposit-state","type":"status","label":"保证金","value":"等待回单","tone":"warning"}
```

Tones: `neutral`, `info`, `success`, `warning`, `danger`.

### Text, list, and callout

```json
{"id":"scope","type":"text","title":"服务范围","content":"物业综合服务。"}
{"id":"risks","type":"list","title":"废标风险","ordered":false,"items":["未按要求签章","保证金未到账"]}
{"id":"warning","type":"callout","tone":"danger","title":"到场要求","content":"项目经理必须本人述标。"}
```

Callout tones: `info`, `success`, `warning`, `danger`.

### Table

```json
{
  "id":"staffing",
  "type":"table",
  "title":"人员配置",
  "columns":["岗位","人数","要求"],
  "rows":[["项目经理",1,"5 年以上经验"]]
}
```

Every row must contain exactly as many cells as `columns`.

### Timeline

```json
{
  "id":"dates",
  "type":"timeline",
  "items":[
    {"label":"投标文件递交","at":"2026-07-25 09:30","description":"电子递交","status":"未完成","tone":"danger"}
  ]
}
```

### Checklist

```json
{
  "id":"tasks",
  "type":"checklist",
  "items":[
    {"label":"核验营业执照","done":true,"note":"2026-07-21 完成"}
  ]
}
```

### Files and divider

```json
{"id":"attachments","type":"files","items":[{"name":"确认函.pdf","url":"/projects/12/files/8"}]}
{"id":"split-1","type":"divider"}
```

File URLs must be an HTTPS URL or a platform-relative path beginning with `/`.
