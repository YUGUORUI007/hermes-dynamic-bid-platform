from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit


SCHEMA_VERSION = "1.0"
MAX_SECTIONS = 24
MAX_BLOCKS_PER_SECTION = 80
MAX_STRING_LENGTH = 20_000
MAX_TABLE_ROWS = 300
MAX_TABLE_COLUMNS = 20

BLOCK_TYPES = {
    "field",
    "status",
    "text",
    "list",
    "table",
    "timeline",
    "checklist",
    "callout",
    "files",
    "divider",
}
PRIORITIES = {"normal", "important", "urgent"}
VISIBILITIES = {"detail", "summary"}
WIDTHS = {"full", "half", "third"}
CALLOUT_TONES = {"info", "success", "warning", "danger"}
STATUS_TONES = {"neutral", "info", "success", "warning", "danger"}
FIELD_SEMANTICS = {"text", "date", "datetime", "amount", "phone", "email", "url"}
PROJECT_STATUSES = {"tracking", "pending_signup", "registered", "pending_prequalification", "deposit_pending", "deposit_done", "preparing", "sealed", "ready_deliver", "submitted", "result_pending", "won", "lost", "abandoned", "partner_completed", "archived"}
WORKFLOW_STAGES = {"signup", "prequalification", "deposit", "proposal", "sealing", "delivery", "bid_open", "deposit_refund"}
WORKFLOW_STATE_VALUES = {"pending", "in_progress", "done", "not_applicable"}
SAFE_URL_SCHEMES = {"http", "https"}
PROJECT_METADATA_FIELDS = {
    "tender_code": 128,
    "buyer": 255,
    "agency": 255,
    "contact_name": 255,
    "contact_phone": 128,
    "signup_deadline": 64,
    "deposit_deadline": 64,
    "submission_datetime": 64,
    "bid_datetime": 64,
}


class SchemaValidationError(ValueError):
    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__(errors[0]["message"] if errors else "动态内容校验失败")


def project_schema_document() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "limits": {
            "max_sections": MAX_SECTIONS,
            "max_blocks_per_section": MAX_BLOCKS_PER_SECTION,
            "max_string_length": MAX_STRING_LENGTH,
            "max_table_rows": MAX_TABLE_ROWS,
            "max_table_columns": MAX_TABLE_COLUMNS,
        },
        "project": {
            "required": ["title", "status", "content"],
            "system_fields": ["title", "status", "owner", "summary", "schema_version", *sorted(PROJECT_METADATA_FIELDS)],
            "content_shape": {"sections": "array<section>"},
        },
        "section": {
            "required": ["id", "title", "blocks"],
            "optional": ["description", "icon", "priority", "visibility", "collapsible"],
        },
        "block_types": sorted(BLOCK_TYPES),
        "display_hints": {
            "priorities": sorted(PRIORITIES),
            "visibilities": sorted(VISIBILITIES),
            "widths": sorted(WIDTHS),
            "callout_tones": sorted(CALLOUT_TONES),
            "status_tones": sorted(STATUS_TONES),
            "field_semantics": sorted(FIELD_SEMANTICS),
        },
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_project_payload(payload: Any, *, partial: bool = False) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        raise SchemaValidationError([error("$", "invalid_type", "项目载荷必须是 JSON 对象。")])

    normalized: dict[str, Any] = {}
    reject_unknown_keys(
        payload,
        {"title", "status", "owner", "summary", "schema_version", "content", "change_summary", "confirmation", "validation_token", *PROJECT_METADATA_FIELDS},
        "$",
        errors,
    )
    required = () if partial else ("title", "status", "content")
    for name in required:
        if name not in payload:
            errors.append(error(f"$.{name}", "required", f"缺少必填字段 {name}。"))

    if "title" in payload:
        normalized["title"] = clean_string(payload.get("title"), "$.title", errors, required=True, max_length=255)
    if "status" in payload:
        normalized["status"] = clean_enum(payload.get("status"), PROJECT_STATUSES, "$.status", errors)
    if "owner" in payload:
        normalized["owner"] = clean_string(payload.get("owner"), "$.owner", errors, max_length=128)
    if "summary" in payload:
        normalized["summary"] = clean_string(payload.get("summary"), "$.summary", errors, max_length=4_000)
    for name, max_length in PROJECT_METADATA_FIELDS.items():
        if name in payload:
            normalized[name] = clean_string(payload.get(name), f"$.{name}", errors, max_length=max_length)

    version = payload.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        errors.append(error("$.schema_version", "unsupported_version", f"当前仅支持 Schema {SCHEMA_VERSION}。"))
    normalized["schema_version"] = SCHEMA_VERSION

    if "content" in payload:
        normalized["content"] = validate_content(payload.get("content"), errors)
    if "change_summary" in payload:
        normalized["change_summary"] = clean_string(payload.get("change_summary"), "$.change_summary", errors, max_length=1_000)
    if "confirmation" in payload:
        normalized["confirmation"] = validate_confirmation(payload.get("confirmation"), errors)
    if "validation_token" in payload:
        normalized["validation_token"] = clean_string(payload.get("validation_token"), "$.validation_token", errors, required=True, max_length=2_000)

    if errors:
        raise SchemaValidationError(errors)
    return normalized


def validate_content(content: Any, errors: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(content, dict):
        errors.append(error("$.content", "invalid_type", "content 必须是对象。"))
        return {"sections": []}
    sections = content.get("sections")
    if not isinstance(sections, list):
        errors.append(error("$.content.sections", "invalid_type", "sections 必须是数组。"))
        return {"sections": []}
    if len(sections) > MAX_SECTIONS:
        errors.append(error("$.content.sections", "too_many_items", f"标签页不能超过 {MAX_SECTIONS} 个。"))

    normalized_sections: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for section_index, section in enumerate(sections[:MAX_SECTIONS]):
        path = f"$.content.sections[{section_index}]"
        if not isinstance(section, dict):
            errors.append(error(path, "invalid_type", "标签页必须是对象。"))
            continue
        reject_unknown_keys(section, {"id", "title", "description", "icon", "priority", "visibility", "collapsible", "blocks"}, path, errors)
        section_id = clean_identifier(section.get("id"), f"{path}.id", errors, max_length=64)
        if section_id and section_id in seen_ids:
            errors.append(error(f"{path}.id", "duplicate", f"标签页 ID {section_id} 重复。"))
        seen_ids.add(section_id)
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            errors.append(error(f"{path}.blocks", "invalid_type", "blocks 必须是数组。"))
            blocks = []
        if len(blocks) > MAX_BLOCKS_PER_SECTION:
            errors.append(error(f"{path}.blocks", "too_many_items", f"每个标签页最多 {MAX_BLOCKS_PER_SECTION} 个区块。"))
        normalized_section = {
            "id": section_id,
            "title": clean_string(section.get("title"), f"{path}.title", errors, required=True, max_length=100),
            "description": clean_string(section.get("description"), f"{path}.description", errors, max_length=500),
            "icon": clean_identifier(section.get("icon", "file-text"), f"{path}.icon", errors, max_length=64),
            "priority": clean_enum(section.get("priority", "normal"), PRIORITIES, f"{path}.priority", errors),
            "visibility": clean_enum(section.get("visibility", "detail"), VISIBILITIES, f"{path}.visibility", errors),
            "collapsible": bool(section.get("collapsible", False)),
            "blocks": [],
        }
        block_ids: set[str] = set()
        for block_index, block in enumerate(blocks[:MAX_BLOCKS_PER_SECTION]):
            normalized_block = validate_block(block, f"{path}.blocks[{block_index}]", errors)
            block_id = normalized_block.get("id")
            if block_id and block_id in block_ids:
                errors.append(error(f"{path}.blocks[{block_index}].id", "duplicate", f"区块 ID {block_id} 重复。"))
            block_ids.add(block_id)
            normalized_section["blocks"].append(normalized_block)
        normalized_sections.append(normalized_section)
    workflow = validate_workflow(content.get("workflow", {}), errors)
    return {"sections": normalized_sections, "workflow": workflow}


def validate_workflow(value: Any, errors: list[dict[str, str]]) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(error("$.content.workflow", "invalid_type", "workflow 必须是对象。"))
        return {}
    normalized: dict[str, str] = {}
    for stage, state in value.items():
        if stage not in WORKFLOW_STAGES:
            errors.append(error(f"$.content.workflow.{stage}", "unknown_key", "不支持的流程事项。"))
            continue
        normalized[stage] = clean_enum(state, WORKFLOW_STATE_VALUES, f"$.content.workflow.{stage}", errors)
    return normalized


def validate_block(block: Any, path: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(block, dict):
        errors.append(error(path, "invalid_type", "区块必须是对象。"))
        return {"id": "invalid", "type": "divider", "width": "full"}
    block_type = clean_enum(block.get("type"), BLOCK_TYPES, f"{path}.type", errors)
    type_keys = {
        "field": {"label", "value", "semantic"},
        "status": {"label", "value", "tone"},
        "text": {"content"},
        "list": {"ordered", "items"},
        "table": {"columns", "rows"},
        "timeline": {"items"},
        "checklist": {"items"},
        "callout": {"tone", "content"},
        "files": {"items"},
        "divider": set(),
    }
    reject_unknown_keys(block, {"id", "type", "title", "width"} | type_keys.get(block_type, set()), path, errors)
    normalized: dict[str, Any] = {
        "id": clean_identifier(block.get("id"), f"{path}.id", errors, max_length=64),
        "type": block_type,
        "width": clean_enum(block.get("width", "full"), WIDTHS, f"{path}.width", errors),
    }
    if "title" in block:
        normalized["title"] = clean_string(block.get("title"), f"{path}.title", errors, max_length=200)

    if block_type == "field":
        normalized.update(
            label=clean_string(block.get("label"), f"{path}.label", errors, required=True, max_length=200),
            value=clean_scalar(block.get("value"), f"{path}.value", errors),
            semantic=clean_enum(block.get("semantic", "text"), FIELD_SEMANTICS, f"{path}.semantic", errors),
        )
        if normalized["semantic"] == "url" and normalized["value"]:
            validate_url(str(normalized["value"]), f"{path}.value", errors)
    elif block_type == "status":
        normalized.update(
            label=clean_string(block.get("label"), f"{path}.label", errors, required=True, max_length=200),
            value=clean_string(block.get("value"), f"{path}.value", errors, required=True, max_length=500),
            tone=clean_enum(block.get("tone", "neutral"), STATUS_TONES, f"{path}.tone", errors),
        )
    elif block_type == "text":
        normalized["content"] = clean_string(block.get("content"), f"{path}.content", errors, required=True)
    elif block_type == "list":
        normalized["ordered"] = bool(block.get("ordered", False))
        normalized["items"] = validate_string_list(block.get("items"), f"{path}.items", errors)
    elif block_type == "table":
        normalized.update(validate_table(block, path, errors))
    elif block_type == "timeline":
        normalized["items"] = validate_object_list(block.get("items"), f"{path}.items", errors, timeline_item)
    elif block_type == "checklist":
        normalized["items"] = validate_object_list(block.get("items"), f"{path}.items", errors, checklist_item)
    elif block_type == "callout":
        normalized.update(
            tone=clean_enum(block.get("tone", "info"), CALLOUT_TONES, f"{path}.tone", errors),
            content=clean_string(block.get("content"), f"{path}.content", errors, required=True),
        )
    elif block_type == "files":
        normalized["items"] = validate_object_list(block.get("items"), f"{path}.items", errors, file_item)
    return normalized


def validate_table(block: dict[str, Any], path: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    columns = validate_string_list(block.get("columns"), f"{path}.columns", errors, limit=MAX_TABLE_COLUMNS)
    rows = block.get("rows")
    if not isinstance(rows, list):
        errors.append(error(f"{path}.rows", "invalid_type", "表格 rows 必须是数组。"))
        return {"columns": columns, "rows": []}
    if len(rows) > MAX_TABLE_ROWS:
        errors.append(error(f"{path}.rows", "too_many_items", f"表格最多 {MAX_TABLE_ROWS} 行。"))
    normalized_rows: list[list[Any]] = []
    for index, row in enumerate(rows[:MAX_TABLE_ROWS]):
        row_path = f"{path}.rows[{index}]"
        if not isinstance(row, list) or len(row) != len(columns):
            errors.append(error(row_path, "invalid_row", "每行单元格数量必须与 columns 一致。"))
            continue
        normalized_rows.append([clean_scalar(value, f"{row_path}[{cell_index}]", errors) for cell_index, value in enumerate(row)])
    return {"columns": columns, "rows": normalized_rows}


def timeline_item(item: dict[str, Any], path: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    reject_unknown_keys(item, {"label", "at", "description", "status", "tone"}, path, errors)
    return {
        "label": clean_string(item.get("label"), f"{path}.label", errors, required=True, max_length=300),
        "at": clean_string(item.get("at"), f"{path}.at", errors, max_length=100),
        "description": clean_string(item.get("description"), f"{path}.description", errors, max_length=2_000),
        "status": clean_string(item.get("status"), f"{path}.status", errors, max_length=100),
        "tone": clean_enum(item.get("tone", "neutral"), STATUS_TONES, f"{path}.tone", errors),
    }


def checklist_item(item: dict[str, Any], path: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    reject_unknown_keys(item, {"label", "done", "note"}, path, errors)
    return {
        "label": clean_string(item.get("label"), f"{path}.label", errors, required=True, max_length=500),
        "done": bool(item.get("done", False)),
        "note": clean_string(item.get("note"), f"{path}.note", errors, max_length=2_000),
    }


def file_item(item: dict[str, Any], path: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    reject_unknown_keys(item, {"name", "url"}, path, errors)
    result = {
        "name": clean_string(item.get("name"), f"{path}.name", errors, required=True, max_length=255),
        "url": clean_string(item.get("url"), f"{path}.url", errors, required=True, max_length=2_000),
    }
    if result["url"]:
        validate_url(result["url"], f"{path}.url", errors, allow_relative=True)
    return result


def validate_confirmation(value: Any, errors: list[dict[str, str]]) -> dict[str, str]:
    path = "$.confirmation"
    if not isinstance(value, dict):
        errors.append(error(path, "invalid_type", "confirmation 必须是对象。"))
        return {"confirmed_by": "", "confirmed_at": "", "summary": ""}
    reject_unknown_keys(value, {"confirmed_by", "confirmed_at", "summary"}, path, errors)
    confirmed_at = clean_string(value.get("confirmed_at"), f"{path}.confirmed_at", errors, required=True, max_length=64)
    if confirmed_at:
        try:
            datetime.fromisoformat(confirmed_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append(error(f"{path}.confirmed_at", "invalid_datetime", "confirmed_at 必须是 ISO 8601 时间。"))
    return {
        "confirmed_by": clean_string(value.get("confirmed_by"), f"{path}.confirmed_by", errors, required=True, max_length=128),
        "confirmed_at": confirmed_at,
        "summary": clean_string(value.get("summary"), f"{path}.summary", errors, required=True, max_length=2_000),
    }


def validate_object_list(value: Any, path: str, errors: list[dict[str, str]], normalizer, *, limit: int = 300) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(error(path, "invalid_type", "该字段必须是数组。"))
        return []
    if len(value) > limit:
        errors.append(error(path, "too_many_items", f"最多允许 {limit} 项。"))
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value[:limit]):
        if not isinstance(item, dict):
            errors.append(error(f"{path}[{index}]", "invalid_type", "数组元素必须是对象。"))
            continue
        result.append(normalizer(item, f"{path}[{index}]", errors))
    return result


def validate_string_list(value: Any, path: str, errors: list[dict[str, str]], *, limit: int = 300) -> list[str]:
    if not isinstance(value, list):
        errors.append(error(path, "invalid_type", "该字段必须是数组。"))
        return []
    if len(value) > limit:
        errors.append(error(path, "too_many_items", f"最多允许 {limit} 项。"))
    return [clean_string(item, f"{path}[{index}]", errors, required=True) for index, item in enumerate(value[:limit])]


def clean_scalar(value: Any, path: str, errors: list[dict[str, str]]) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return clean_string(value, path, errors)
    errors.append(error(path, "invalid_type", "值必须是字符串、数字、布尔值或 null。"))
    return None


def clean_string(value: Any, path: str, errors: list[dict[str, str]], *, required: bool = False, max_length: int = MAX_STRING_LENGTH) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        errors.append(error(path, "invalid_type", "该字段必须是字符串。"))
        return ""
    value = value.strip()
    if required and not value:
        errors.append(error(path, "required", "该字段不能为空。"))
    if len(value) > max_length:
        errors.append(error(path, "too_long", f"长度不能超过 {max_length} 个字符。"))
        value = value[:max_length]
    return value


def clean_identifier(value: Any, path: str, errors: list[dict[str, str]], *, max_length: int) -> str:
    value = clean_string(value, path, errors, required=True, max_length=max_length)
    if value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        errors.append(error(path, "invalid_identifier", "标识符仅允许字母、数字、点、下划线、冒号和短横线。"))
    return value


def clean_enum(value: Any, choices: set[str], path: str, errors: list[dict[str, str]]) -> str:
    if not isinstance(value, str) or value not in choices:
        errors.append(error(path, "invalid_enum", f"可选值：{', '.join(sorted(choices))}。"))
        return sorted(choices)[0]
    return value


def validate_url(value: str, path: str, errors: list[dict[str, str]], *, allow_relative: bool = False) -> None:
    parsed = urlsplit(value)
    if allow_relative and not parsed.scheme and value.startswith("/"):
        return
    if parsed.scheme.lower() not in SAFE_URL_SCHEMES:
        errors.append(error(path, "unsafe_url", "URL 仅允许 http 或 https 协议。"))


def error(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def reject_unknown_keys(value: dict[str, Any], allowed: set[str], path: str, errors: list[dict[str, str]]) -> None:
    for key in value.keys() - allowed:
        errors.append(error(f"{path}.{key}", "unknown_field", f"不支持字段 {key}。"))


def merge_project_payload(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    core_fields = ("title", "status", "owner", "summary", "schema_version", "content", *PROJECT_METADATA_FIELDS)
    merged = {key: deepcopy(current[key]) for key in core_fields if key in current}
    for key in (*core_fields, "change_summary", "confirmation", "validation_token"):
        if key in patch:
            merged[key] = deepcopy(patch[key])
    return merged
