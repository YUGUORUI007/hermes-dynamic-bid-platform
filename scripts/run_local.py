from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def sample_projects():
    return [
        {
            "title": "瓯海区行政中心物业管理服务项目",
            "status": "tracking",
            "owner": "余国锐",
            "summary": "已完成资格文件核对，技术方案正在编制，需重点处理项目经理业绩证明。",
            "schema_version": "1.0",
            "content": {
                "sections": [
                    {
                        "id": "overview",
                        "title": "项目概览",
                        "icon": "layout-dashboard",
                        "visibility": "summary",
                        "blocks": [
                            {"id": "buyer", "type": "field", "label": "采购人", "value": "瓯海区机关事务管理中心", "width": "half"},
                            {"id": "budget", "type": "field", "label": "最高限价", "value": "1,286 万元", "semantic": "amount", "width": "half"},
                            {"id": "risk", "type": "callout", "title": "项目经理到场要求", "tone": "danger", "content": "项目经理必须本人参加现场述标。"},
                        ],
                    },
                    {
                        "id": "dates",
                        "title": "关键节点",
                        "icon": "calendar-range",
                        "blocks": [
                            {"id": "timeline", "type": "timeline", "items": [{"label": "投标文件递交", "at": "2026-07-25 09:30", "status": "未完成", "tone": "danger"}]}
                        ],
                    },
                    {
                        "id": "score",
                        "title": "评分策略",
                        "icon": "chart-no-axes-column",
                        "blocks": [
                            {"id": "score-table", "type": "table", "columns": ["评分板块", "分值", "准备重点"], "rows": [["技术方案", "45", "人员稳定与应急保障"], ["商务资信", "35", "完整业绩闭环"]]}
                        ],
                    },
                ]
            },
        },
        {
            "title": "滨江商务中心秩序维护专项采购",
            "status": "tracking",
            "owner": "林海",
            "summary": "核心为秩序维护人员配置、军事化训练及应急响应。",
            "schema_version": "1.0",
            "content": {
                "sections": [
                    {
                        "id": "deposit",
                        "title": "保证金办理",
                        "icon": "landmark",
                        "visibility": "summary",
                        "blocks": [
                            {"id": "amount", "type": "field", "label": "缴纳金额", "value": "80,000 元", "semantic": "amount", "width": "half"},
                            {"id": "due", "type": "field", "label": "到账期限", "value": "2026-07-22 17:00", "semantic": "datetime", "width": "half"},
                            {"id": "alert", "type": "callout", "tone": "warning", "content": "必须以到账时间为准，转账后立即确认。"},
                        ],
                    },
                    {
                        "id": "staffing",
                        "title": "岗位配置",
                        "icon": "users-round",
                        "blocks": [
                            {"id": "staff", "type": "table", "columns": ["岗位", "人数", "要求"], "rows": [["秩序主管", 1, "持保安员证"], ["固定岗", 12, "身高 170cm 以上"]]}
                        ],
                    },
                ]
            },
        },
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bid platform with an isolated local data directory")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--seed-demo", action="store_true")
    args = parser.parse_args()

    root = Path(args.data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["BID_PLATFORM_INSTANCE_DIR"] = str(root / "instance")
    os.environ["BID_PLATFORM_STORAGE_DIR"] = str(root / "storage")
    os.environ["BID_PLATFORM_ADMIN_PASSWORD"] = args.admin_password
    os.environ.setdefault("BID_PLATFORM_SECRET_KEY", "local-isolated-development-key")
    os.environ["BID_PLATFORM_PUBLIC_BASE_URL"] = f"http://127.0.0.1:{args.port}"

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from platform_app.database import session_scope
    from platform_app.dynamic_schema import validate_project_payload
    from platform_app.main import create_app
    from platform_app.models import Project

    app = create_app()
    if args.seed_demo:
        with session_scope() as session:
            for raw in sample_projects():
                payload = validate_project_payload(raw)
                if session.query(Project).filter(Project.name == payload["title"]).first():
                    continue
                session.add(
                    Project(
                        name=payload["title"],
                        status=payload["status"],
                        owner_name=payload["owner"],
                        summary=payload["summary"],
                        schema_version=payload["schema_version"],
                        content_version=1,
                        dynamic_content=json.dumps(payload["content"], ensure_ascii=False),
                    )
                )

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
