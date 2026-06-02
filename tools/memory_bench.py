from __future__ import annotations

import json
import os
import re
import tempfile

from langgraph_manager import app as app_module
from langgraph_manager import db


MEMORIES = [
    ("ui_scroll_fix", "Fix UI scroll jump mobile", "Applied overflow-anchor + near-bottom autoscroll guard", "ui,frontend,scroll,mobile"),
    ("auth_timeout_debug", "Debug auth timeout API", "Trace gateway timeout by route + upstream latency", "backend,api,timeout,auth"),
    ("docs_fastapi", "FastAPI docs lookup", "Use context7 for latest docs and version diff", "docs,fastapi,version"),
    ("security_token", "Token leakage prevention", "Mask token in logs and rotate leaked credentials", "security,token,auth"),
    ("feature_pdf", "PDF upload feature path", "Use streaming upload and validate mime", "feature,pdf,upload"),
    ("general_git", "Safe rollback flow", "Create checkpoint before risky refactor", "git,checkpoint,recovery"),
    ("css_mobile", "Mobile CSS form buttons", "Use responsive spacing and tap-target min 44px", "css,frontend,mobile,form"),
    ("route_callers", "Route callers analysis", "Use codegraph to inspect callers/callees impact", "route,callers,impact,codegraph"),
]

CASES = [
    ("UI bug", "sửa lỗi UI chat bị nhảy scroll ở mobile", "ui_scroll_fix"),
    ("Repo trace", "tìm route xử lý auth và trace callers trong repo", "route_callers"),
    ("Docs", "xem docs FastAPI version mới nhất", "docs_fastapi"),
    ("Security", "review lộ token và lỗ hổng auth", "security_token"),
    ("Feature", "implement feature upload PDF và review code", "feature_pdf"),
    ("General debug", "debug lỗi timeout API và tìm nguyên nhân", "auth_timeout_debug"),
    ("Frontend css", "chỉnh css button và form trên màn mobile", "css_mobile"),
    ("Rollback", "nếu lỗi thì rollback cho an toàn", "general_git"),
    ("Auth route", "route auth timeout và caller nào chậm", "auth_timeout_debug"),
    ("Upload policy", "luồng upload pdf kiểm tra mime", "feature_pdf"),
]


def extract_memory_titles(ctx: str) -> list[str]:
    titles: list[str] = []
    in_long = False
    for line in ctx.splitlines():
        text = line.strip()
        if text.startswith("long_term_memory:"):
            in_long = True
            continue
        if not in_long:
            continue
        if text and re.match(r"^[a-z_]+:", text):
            break
        m = re.match(r"- \[[^\]]+\] (.*?) \(tags:", text)
        if m:
            titles.append(m.group(1).strip())
    return titles


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        os.environ["LANGGRAPH_STATE_DIR"] = td
        db.init_db()
        for _, title, content, tags in MEMORIES:
            db.create_memory("note", title, content, tags, "bench")
        job = db.create_chat_session("bench_job", title="Bench")

        rows = []
        top1_hits = 0
        top3_hits = 0
        for name, query, expected_key in CASES:
            db.add_message(job["id"], "user", query)
            app_module.refresh_session_memory(job["id"])
            ctx = app_module.memory_context(job["id"])
            titles = extract_memory_titles(ctx)
            expected_title = next(item[1] for item in MEMORIES if item[0] == expected_key)
            top1 = titles[0] if titles else ""
            top3 = titles[:3]
            hit1 = int(top1 == expected_title)
            hit3 = int(expected_title in top3)
            top1_hits += hit1
            top3_hits += hit3
            rows.append(
                {
                    "case": name,
                    "expected": expected_title,
                    "top1": top1,
                    "top3": top3,
                    "top1_hit": hit1,
                    "top3_hit": hit3,
                }
            )
        print(
            json.dumps(
                {"rows": rows, "summary": {"top1_hits": top1_hits, "top3_hits": top3_hits, "total": len(CASES)}},
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
