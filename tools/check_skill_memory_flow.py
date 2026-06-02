from __future__ import annotations

import json
import os
import tempfile
import time

from fastapi.testclient import TestClient

from langgraph_manager import app as app_module
from langgraph_manager.app import app
from langgraph_manager import db


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        os.environ["LANGGRAPH_STATE_DIR"] = td
        db.init_db()
        db.create_memory(
            "note",
            "Fix UI scroll jump mobile",
            "Applied overflow-anchor + near-bottom autoscroll guard",
            "ui,frontend,scroll,mobile",
            "bench",
        )
        db.create_memory(
            "note",
            "Token leakage prevention",
            "Mask tokens in logs and rotate leaked credentials",
            "security,token,auth",
            "bench",
        )

        captured: dict = {}

        def fake_run_tool(name, context):
            if name == "coding_agent_executor":
                captured["tool"] = name
                captured["context"] = context
                return {
                    "ok": True,
                    "summary": "done",
                    "data": {
                        "used_skills": ["frontend-design"],
                        "output": '{"type":"result","result":"ok"}',
                        "command": "codex run",
                    },
                }
            return {"ok": True, "summary": "ok", "data": {}}

        app_module.run_tool = fake_run_tool

        with TestClient(app) as client:
            chat = client.post("/api/chats").json()["job"]
            query = "sửa lỗi UI chat bị nhảy scroll ở mobile"
            # Seed successful route so hint can be sent to executor.
            env_fp = app_module.env_fingerprint_for_job(chat)
            task_sig = app_module.task_signature_for_request(query)
            db.record_route_memory(env_fp, task_sig, "codegraph", ok=True)
            updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": query}).json()["job"]
            pending = [a for a in updated.get("pending_actions", []) if a.get("status") == "pending"]
            action = pending[-1]
            payload = action.get("payload") or {}
            client.post(f"/api/actions/{action['id']}/approve")
            for _ in range(30):
                if captured.get("tool") == "coding_agent_executor":
                    break
                time.sleep(0.1)

            mem_ctx = app_module.memory_context(chat["id"])
            discussion = str((captured.get("context") or {}).get("discussion") or "")
            route_hint = str((captured.get("context") or {}).get("route_memory_hint") or "")

            result = {
                "pending_kind": action.get("kind"),
                "recommended_top2": [x.get("name") for x in (payload.get("recommended_tools") or [])[:2]],
                "has_ordered_tools": bool(payload.get("ordered_tool_recommendations")),
                "executor_called": captured.get("tool") == "coding_agent_executor",
                "discussion_has_query": query in discussion,
                "discussion_has_memory_hit": "Fix UI scroll jump mobile" in discussion,
                "route_hint_nonempty": bool(route_hint.strip()),
                "memory_context_has_ui": "Fix UI scroll jump mobile" in mem_ctx,
                "memory_context_has_security": "Token leakage prevention" in mem_ctx,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
