from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> Path:
    state_dir = Path(os.getenv("LANGGRAPH_STATE_DIR", "/data/state"))
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "manager.sqlite"


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(str(db_path or default_db_path()))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              request TEXT NOT NULL,
              status TEXT NOT NULL,
              plan_json TEXT NOT NULL DEFAULT '[]',
              permission_mode TEXT NOT NULL DEFAULT 'auto_review',
              result TEXT NOT NULL DEFAULT '',
              error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS job_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS job_steps (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              status TEXT NOT NULL,
              detail TEXT NOT NULL DEFAULT '',
              started_at TEXT,
              finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS job_focus_files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL DEFAULT 'note',
              title TEXT NOT NULL,
              content TEXT NOT NULL,
              tags TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT 'manual',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skills (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              triggers TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              body TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_actions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,
              title TEXT NOT NULL,
              preview TEXT NOT NULL DEFAULT '',
              payload_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'pending',
              result TEXT NOT NULL DEFAULT '',
              error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_memory (
              job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
              summary TEXT NOT NULL DEFAULT '',
              current_goal TEXT NOT NULL DEFAULT '',
              open_tasks TEXT NOT NULL DEFAULT '',
              important_files TEXT NOT NULL DEFAULT '',
              decisions TEXT NOT NULL DEFAULT '',
              last_result TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assistant_commands (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              description TEXT NOT NULL DEFAULT '',
              action_kind TEXT NOT NULL DEFAULT 'note',
              payload_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recurring_tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              prompt TEXT NOT NULL,
              schedule TEXT NOT NULL DEFAULT 'manual',
              action_kind TEXT NOT NULL DEFAULT 'note',
              payload_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'active',
              last_run_at TEXT NOT NULL DEFAULT '',
              next_run_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              root_path TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL DEFAULT '',
              memory TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS role_plugins (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT '',
              description TEXT NOT NULL DEFAULT '',
              instructions TEXT NOT NULL DEFAULT '',
              tool_hints TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_preferences (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT 'manual',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS action_lessons (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              signature TEXT NOT NULL UNIQUE,
              kind TEXT NOT NULL,
              error_excerpt TEXT NOT NULL DEFAULT '',
              strategy TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'active',
              fail_count INTEGER NOT NULL DEFAULT 1,
              last_failed_at TEXT NOT NULL,
              resolved_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_performance (
              skill_name TEXT PRIMARY KEY,
              score REAL NOT NULL DEFAULT 0,
              trials INTEGER NOT NULL DEFAULT 0,
              success_count INTEGER NOT NULL DEFAULT 0,
              failure_count INTEGER NOT NULL DEFAULT 0,
              negative_streak INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'active',
              needs_review INTEGER NOT NULL DEFAULT 0,
              last_outcome TEXT NOT NULL DEFAULT '',
              last_used_at TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS git_checkpoints (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              cwd TEXT NOT NULL,
              branch TEXT NOT NULL DEFAULT '',
              commit_hash TEXT NOT NULL,
              label TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executor_sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
              executor TEXT NOT NULL,
              session_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(job_id, executor)
            );

            CREATE TABLE IF NOT EXISTS route_memory (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              env_fingerprint TEXT NOT NULL,
              task_signature TEXT NOT NULL,
              route TEXT NOT NULL,
              success_count INTEGER NOT NULL DEFAULT 0,
              failure_count INTEGER NOT NULL DEFAULT 0,
              last_outcome TEXT NOT NULL DEFAULT '',
              last_error_class TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              UNIQUE(env_fingerprint, task_signature, route)
            );

            CREATE INDEX IF NOT EXISTS idx_job_messages_job_id ON job_messages(job_id);
            CREATE INDEX IF NOT EXISTS idx_job_steps_job_id ON job_steps(job_id);
            CREATE INDEX IF NOT EXISTS idx_job_focus_files_job_id ON job_focus_files(job_id);
            CREATE INDEX IF NOT EXISTS idx_memory_entries_kind ON memory_entries(kind);
            CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
            CREATE INDEX IF NOT EXISTS idx_pending_actions_job_id ON pending_actions(job_id);
            CREATE INDEX IF NOT EXISTS idx_pending_actions_status ON pending_actions(status);
            CREATE INDEX IF NOT EXISTS idx_assistant_commands_status ON assistant_commands(status);
            CREATE INDEX IF NOT EXISTS idx_recurring_tasks_status ON recurring_tasks(status);
            CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
            CREATE INDEX IF NOT EXISTS idx_role_plugins_status ON role_plugins(status);
            CREATE INDEX IF NOT EXISTS idx_user_preferences_source ON user_preferences(source);
            CREATE INDEX IF NOT EXISTS idx_action_lessons_status ON action_lessons(status);
            CREATE INDEX IF NOT EXISTS idx_skill_performance_status ON skill_performance(status);
            CREATE INDEX IF NOT EXISTS idx_git_checkpoints_job_id ON git_checkpoints(job_id);
            CREATE INDEX IF NOT EXISTS idx_executor_sessions_job_executor ON executor_sessions(job_id, executor);
            CREATE INDEX IF NOT EXISTS idx_route_memory_lookup ON route_memory(env_fingerprint, task_signature, updated_at);
            """
        )
        columns = {row["name"] for row in con.execute("PRAGMA table_info(jobs)").fetchall()}
        if "permission_mode" not in columns:
            con.execute("ALTER TABLE jobs ADD COLUMN permission_mode TEXT NOT NULL DEFAULT 'auto_review'")


def row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["plan"] = json.loads(data.pop("plan_json") or "[]")
    return data


def create_job(
    job_id: str,
    title: str,
    request: str,
    plan: list[str],
    status: str = "planned",
    permission_mode: str = "auto_review",
    manager_message: str = "Đã tạo plan nháp. Chờ approve trước khi chạy.",
) -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        con.execute(
            """
            INSERT INTO jobs (id, title, request, status, plan_json, permission_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, title, request, status, json.dumps(plan, ensure_ascii=False), permission_mode, ts, ts),
        )
        con.execute(
            "INSERT INTO job_messages (job_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (job_id, "user", request, ts),
        )
        if manager_message:
            con.execute(
                "INSERT INTO job_messages (job_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (job_id, "langgraph", manager_message, ts),
            )
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row_to_job(row)


def get_or_create_chat(job_id: str = "main_chat", permission_mode: str = "auto_review") -> dict[str, Any]:
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row:
            return row_to_job(row)
        ts = now_iso()
        con.execute(
            """
            INSERT INTO jobs (id, title, request, status, plan_json, permission_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, "Main chat", "", "chat", "[]", permission_mode, ts, ts),
        )
        con.execute(
            "INSERT INTO job_messages (job_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (job_id, "langgraph", "Em ở đây. Anh cứ nhắn liền mạch, khi nào muốn làm task riêng thì bấm New task.", ts),
        )
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row_to_job(row)


def create_chat_session(job_id: str, title: str = "New session", permission_mode: str = "auto_review") -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        con.execute(
            """
            INSERT INTO jobs (id, title, request, status, plan_json, permission_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, title, "", "chat", "[]", permission_mode, ts, ts),
        )
        con.execute(
            "INSERT INTO job_messages (job_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (job_id, "langgraph", "Em ở đây. Anh cứ nhắn để bàn tiếp; bật Plan khi muốn em biến cuộc trao đổi này thành việc cần làm.", ts),
        )
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row_to_job(row)


def replace_plan(job_id: str, plan: list[str], note: str = "", request: str | None = None) -> None:
    ts = now_iso()
    with connect() as con:
        if request is None:
            con.execute(
                "UPDATE jobs SET status = 'planned', plan_json = ?, result = '', error = '', updated_at = ? WHERE id = ?",
                (json.dumps(plan, ensure_ascii=False), ts, job_id),
            )
        else:
            con.execute(
                "UPDATE jobs SET status = 'planned', request = ?, plan_json = ?, result = '', error = '', updated_at = ? WHERE id = ?",
                (request, json.dumps(plan, ensure_ascii=False), ts, job_id),
            )
        con.execute("DELETE FROM job_steps WHERE job_id = ?", (job_id,))
        if note:
            con.execute(
                "INSERT INTO job_messages (job_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (job_id, "langgraph", note, ts),
            )


def list_jobs() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
        return [row_to_job(row) for row in rows]


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        job = row_to_job(row)
        # Guardrail: if job is no longer running, any long-stale running action is likely orphaned.
        if str(job.get("status") or "") != "running":
            cutoff = datetime.now(timezone.utc).timestamp() - 120
            stale_before = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
            con.execute(
                """
                UPDATE pending_actions
                SET status = 'failed',
                    error = CASE WHEN error = '' THEN 'Action timed out (stale running state).' ELSE error END,
                    updated_at = ?
                WHERE job_id = ? AND status = 'running' AND updated_at < ?
                """,
                (now_iso(), job_id, stale_before),
            )
        job["messages"] = [dict(r) for r in con.execute("SELECT * FROM job_messages WHERE job_id = ? ORDER BY id", (job_id,))]
        job["steps"] = [dict(r) for r in con.execute("SELECT * FROM job_steps WHERE job_id = ? ORDER BY id", (job_id,))]
        job["focus_files"] = [
            dict(r)
            for r in con.execute(
                "SELECT id, name, content, created_at FROM job_focus_files WHERE job_id = ? ORDER BY id",
                (job_id,),
            )
        ]
        job["pending_actions"] = [
            row_to_pending_action(r)
            for r in con.execute(
                "SELECT * FROM pending_actions WHERE job_id = ? AND status IN ('pending','running') ORDER BY id",
                (job_id,),
            )
        ]
        memory = con.execute("SELECT * FROM session_memory WHERE job_id = ?", (job_id,)).fetchone()
        job["session_memory"] = dict(memory) if memory else default_session_memory(job_id)
        return job


def default_session_memory(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "summary": "",
        "current_goal": "",
        "open_tasks": "",
        "important_files": "",
        "decisions": "",
        "last_result": "",
        "updated_at": "",
    }


def get_session_memory(job_id: str) -> dict[str, Any]:
    with connect() as con:
        row = con.execute("SELECT * FROM session_memory WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else default_session_memory(job_id)


def upsert_session_memory(
    job_id: str,
    summary: str = "",
    current_goal: str = "",
    open_tasks: str = "",
    important_files: str = "",
    decisions: str = "",
    last_result: str = "",
) -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        con.execute(
            """
            INSERT INTO session_memory
              (job_id, summary, current_goal, open_tasks, important_files, decisions, last_result, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
              summary = excluded.summary,
              current_goal = excluded.current_goal,
              open_tasks = excluded.open_tasks,
              important_files = excluded.important_files,
              decisions = excluded.decisions,
              last_result = excluded.last_result,
              updated_at = excluded.updated_at
            """,
            (job_id, summary, current_goal, open_tasks, important_files, decisions, last_result, ts),
        )
        row = con.execute("SELECT * FROM session_memory WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row)


def delete_job(job_id: str) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0


def update_job_title(job_id: str, title: str) -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            "UPDATE jobs SET title = ?, updated_at = ? WHERE id = ?",
            (title, ts, job_id),
        )
        return cur.rowcount > 0


def update_permission_mode(job_id: str, permission_mode: str) -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            "UPDATE jobs SET permission_mode = ?, updated_at = ? WHERE id = ?",
            (permission_mode, ts, job_id),
        )
        return cur.rowcount > 0


def fail_running_jobs_on_startup() -> int:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                error = 'Job was interrupted when the server restarted.',
                updated_at = ?
            WHERE status = 'running'
            """,
            (ts,),
        )
        con.execute(
            """
            UPDATE job_steps
            SET status = 'failed',
                detail = CASE
                    WHEN detail = '' THEN 'Interrupted by server restart'
                    ELSE detail || ' | Interrupted by server restart'
                END,
                finished_at = COALESCE(finished_at, ?)
            WHERE status = 'running'
            """,
            (ts,),
        )
        con.execute(
            """
            UPDATE pending_actions
            SET status = 'failed',
                error = CASE
                    WHEN error = '' THEN 'Action was interrupted when the server restarted.'
                    ELSE error || ' | Interrupted by server restart'
                END,
                updated_at = ?
            WHERE status = 'running'
            """,
            (ts,),
        )
        return cur.rowcount


def add_message(job_id: str, role: str, content: str) -> None:
    ts = now_iso()
    with connect() as con:
        con.execute(
            "INSERT INTO job_messages (job_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (job_id, role, content, ts),
        )
        con.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, job_id))


def row_to_pending_action(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["payload"] = json.loads(data.pop("payload_json") or "{}")
    except json.JSONDecodeError:
        data["payload"] = {}
    return data


def add_pending_action(job_id: str, kind: str, title: str, preview: str, payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO pending_actions (job_id, kind, title, preview, payload_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (job_id, kind, title, preview, json.dumps(payload, ensure_ascii=False), ts, ts),
        )
        con.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, job_id))
        row = con.execute("SELECT * FROM pending_actions WHERE id = ?", (cur.lastrowid,)).fetchone()
        return row_to_pending_action(row)


def get_pending_action(action_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        return row_to_pending_action(row) if row else None


def update_pending_action_status(action_id: int, status: str, result: str = "", error: str = "") -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE pending_actions
            SET status = ?, result = ?, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, result, error, ts, action_id),
        )
        row = con.execute("SELECT job_id FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        if row:
            con.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, row["job_id"]))
        return cur.rowcount > 0


def claim_pending_action(action_id: int, claim_status: str = "running") -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE pending_actions
            SET status = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (claim_status, ts, action_id),
        )
        if cur.rowcount > 0:
            row = con.execute("SELECT job_id FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
            if row:
                con.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, row["job_id"]))
        return cur.rowcount > 0


def close_open_pending_actions(job_id: str, status: str = "rejected", note: str = "") -> int:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE pending_actions
            SET status = ?, error = CASE WHEN error = '' THEN ? ELSE error END, updated_at = ?
            WHERE job_id = ? AND status IN ('pending', 'running')
            """,
            (status, note, ts, job_id),
        )
        con.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, job_id))
        return int(cur.rowcount or 0)


def add_focus_file(job_id: str, name: str, content: str) -> None:
    ts = now_iso()
    with connect() as con:
        con.execute(
            "INSERT INTO job_focus_files (job_id, name, content, created_at) VALUES (?, ?, ?, ?)",
            (job_id, name, content, ts),
        )
        con.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, job_id))


def clear_job_run_state(job_id: str) -> None:
    ts = now_iso()
    with connect() as con:
        con.execute("UPDATE jobs SET result = '', error = '', updated_at = ? WHERE id = ?", (ts, job_id))
        con.execute("DELETE FROM job_steps WHERE job_id = ?", (job_id,))


def update_job_status(job_id: str, status: str, result: str = "", error: str = "") -> None:
    ts = now_iso()
    with connect() as con:
        con.execute(
            "UPDATE jobs SET status = ?, result = COALESCE(NULLIF(?, ''), result), error = ?, updated_at = ? WHERE id = ?",
            (status, result, error, ts, job_id),
        )


def approve_job(job_id: str) -> None:
    update_job_status(job_id, "approved")


def update_step(job_id: str, name: str, status: str, detail: str = "") -> None:
    ts = now_iso()
    with connect() as con:
        if status == "running":
            cur = con.execute(
                "UPDATE job_steps SET status = ?, detail = ?, started_at = COALESCE(started_at, ?) WHERE job_id = ? AND name = ?",
                (status, detail, ts, job_id, name),
            )
        elif status in {"done", "failed"}:
            cur = con.execute(
                "UPDATE job_steps SET status = ?, detail = ?, finished_at = ? WHERE job_id = ? AND name = ?",
                (status, detail, ts, job_id, name),
            )
        else:
            cur = con.execute(
                "UPDATE job_steps SET status = ?, detail = ? WHERE job_id = ? AND name = ?",
                (status, detail, job_id, name),
            )
        if cur.rowcount == 0:
            con.execute(
                """
                INSERT INTO job_steps (job_id, name, status, detail, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    name,
                    status,
                    detail,
                    ts if status == "running" else None,
                    ts if status in {"done", "failed"} else None,
                ),
            )
        con.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, job_id))


def list_memories() -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM memory_entries ORDER BY updated_at DESC, id DESC")]


def list_user_memories(limit: int = 40) -> list[dict[str, Any]]:
    safe_limit = max(1, min(200, int(limit or 40)))
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM memory_entries
            WHERE lower(kind) = 'user'
               OR lower(tags) LIKE '%user:%'
               OR lower(tags) LIKE '%profile%'
               OR lower(tags) LIKE '%persona%'
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_memory(kind: str, title: str, content: str, tags: str = "", source: str = "manual") -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO memory_entries (kind, title, content, tags, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (kind, title, content, tags, source, ts, ts),
        )
        return dict(con.execute("SELECT * FROM memory_entries WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_memory(memory_id: int, kind: str, title: str, content: str, tags: str = "", source: str = "manual") -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE memory_entries
            SET kind = ?, title = ?, content = ?, tags = ?, source = ?, updated_at = ?
            WHERE id = ?
            """,
            (kind, title, content, tags, source, ts, memory_id),
        )
        return cur.rowcount > 0


def delete_memory(memory_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM memory_entries WHERE id = ?", (memory_id,))
        return cur.rowcount > 0


def list_user_preferences() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM user_preferences ORDER BY updated_at DESC, key ASC").fetchall()
        return [dict(row) for row in rows]


def get_user_preference(key: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM user_preferences WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None


def upsert_user_preference(key: str, value: str, source: str = "manual") -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        con.execute(
            """
            INSERT INTO user_preferences (key, value, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              source = excluded.source,
              updated_at = excluded.updated_at
            """,
            (key, value, source, ts, ts),
        )
        row = con.execute("SELECT * FROM user_preferences WHERE key = ?", (key,)).fetchone()
        return dict(row)


def delete_user_preference(key: str) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM user_preferences WHERE key = ?", (key,))
        return cur.rowcount > 0


def list_action_lessons(status: str | None = None) -> list[dict[str, Any]]:
    with connect() as con:
        if status:
            rows = con.execute("SELECT * FROM action_lessons WHERE status = ? ORDER BY updated_at DESC, id DESC", (status,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM action_lessons ORDER BY updated_at DESC, id DESC").fetchall()
        return [dict(row) for row in rows]


def get_action_lesson(signature: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM action_lessons WHERE signature = ?", (signature,)).fetchone()
        return dict(row) if row else None


def upsert_action_lesson_failure(signature: str, kind: str, error_excerpt: str, strategy: str) -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        con.execute(
            """
            INSERT INTO action_lessons
              (signature, kind, error_excerpt, strategy, status, fail_count, last_failed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', 1, ?, ?, ?)
            ON CONFLICT(signature) DO UPDATE SET
              kind = excluded.kind,
              error_excerpt = excluded.error_excerpt,
              strategy = excluded.strategy,
              status = 'active',
              fail_count = action_lessons.fail_count + 1,
              last_failed_at = excluded.last_failed_at,
              updated_at = excluded.updated_at
            """,
            (signature, kind, error_excerpt, strategy, ts, ts, ts),
        )
        row = con.execute("SELECT * FROM action_lessons WHERE signature = ?", (signature,)).fetchone()
        return dict(row)


def resolve_action_lesson(signature: str) -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE action_lessons
            SET status = 'resolved',
                resolved_at = ?,
                updated_at = ?
            WHERE signature = ? AND status != 'resolved'
            """,
            (ts, ts, signature),
        )
        return cur.rowcount > 0


def list_skill_performance() -> list[dict[str, Any]]:
    try:
        with connect() as con:
            rows = con.execute("SELECT * FROM skill_performance ORDER BY score DESC, trials DESC, skill_name ASC").fetchall()
            return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []


def get_skill_performance(skill_name: str) -> dict[str, Any] | None:
    try:
        with connect() as con:
            row = con.execute("SELECT * FROM skill_performance WHERE skill_name = ?", (skill_name,)).fetchone()
            return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def _next_skill_status(score: float, trials: int, success_count: int, failure_count: int, negative_streak: int) -> tuple[str, int]:
    # Be conservative: never retire early; require enough evidence and poor long-term performance.
    if trials < 6:
        return "active", 0
    if score <= -8 and negative_streak >= 4 and trials >= 10 and failure_count >= max(6, success_count * 2):
        return "retired", 1
    if score <= -3 and negative_streak >= 2:
        return "deprioritized", 1
    return "active", 0


def record_skill_outcome(skill_name: str, ok: bool, notes: str = "") -> dict[str, Any]:
    ts = now_iso()
    existing = get_skill_performance(skill_name)
    score = float((existing or {}).get("score") or 0.0)
    trials = int((existing or {}).get("trials") or 0)
    success_count = int((existing or {}).get("success_count") or 0)
    failure_count = int((existing or {}).get("failure_count") or 0)
    negative_streak = int((existing or {}).get("negative_streak") or 0)

    trials += 1
    if ok:
        success_count += 1
        negative_streak = 0
        score += 1.2
    else:
        failure_count += 1
        negative_streak += 1
        score -= 1.0
    # Mild decay toward zero to avoid permanent lock-in.
    score = score * 0.98

    status, needs_review = _next_skill_status(score, trials, success_count, failure_count, negative_streak)
    # Recovery path: if previously degraded and now showing wins, reopen it.
    if ok and status != "active" and score > -1.0:
        status = "active"
        needs_review = 0

    with connect() as con:
        con.execute(
            """
            INSERT INTO skill_performance
              (skill_name, score, trials, success_count, failure_count, negative_streak, status, needs_review, last_outcome, last_used_at, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_name) DO UPDATE SET
              score = excluded.score,
              trials = excluded.trials,
              success_count = excluded.success_count,
              failure_count = excluded.failure_count,
              negative_streak = excluded.negative_streak,
              status = excluded.status,
              needs_review = excluded.needs_review,
              last_outcome = excluded.last_outcome,
              last_used_at = excluded.last_used_at,
              notes = excluded.notes,
              updated_at = excluded.updated_at
            """,
            (
                skill_name,
                score,
                trials,
                success_count,
                failure_count,
                negative_streak,
                status,
                needs_review,
                "success" if ok else "failure",
                ts,
                notes[:1000],
                (existing or {}).get("created_at") or ts,
                ts,
            ),
        )
        row = con.execute("SELECT * FROM skill_performance WHERE skill_name = ?", (skill_name,)).fetchone()
        return dict(row)


def get_executor_session(job_id: str, executor: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM executor_sessions WHERE job_id = ? AND executor = ?",
            (job_id, executor),
        ).fetchone()
        return dict(row) if row else None


def upsert_executor_session(job_id: str, executor: str, session_id: str) -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        con.execute(
            """
            INSERT INTO executor_sessions (job_id, executor, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(job_id, executor) DO UPDATE SET
              session_id = excluded.session_id,
              updated_at = excluded.updated_at
            """,
            (job_id, executor, session_id, ts, ts),
        )
        row = con.execute(
            "SELECT * FROM executor_sessions WHERE job_id = ? AND executor = ?",
            (job_id, executor),
        ).fetchone()
        return dict(row)


def record_route_memory(
    env_fingerprint: str,
    task_signature: str,
    route: str,
    ok: bool,
    error_class: str = "",
) -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        con.execute(
            """
            INSERT INTO route_memory
              (env_fingerprint, task_signature, route, success_count, failure_count, last_outcome, last_error_class, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(env_fingerprint, task_signature, route) DO UPDATE SET
              success_count = route_memory.success_count + excluded.success_count,
              failure_count = route_memory.failure_count + excluded.failure_count,
              last_outcome = excluded.last_outcome,
              last_error_class = excluded.last_error_class,
              updated_at = excluded.updated_at
            """,
            (
                env_fingerprint,
                task_signature,
                route,
                1 if ok else 0,
                0 if ok else 1,
                "success" if ok else "failure",
                error_class,
                ts,
            ),
        )
        row = con.execute(
            """
            SELECT * FROM route_memory
            WHERE env_fingerprint = ? AND task_signature = ? AND route = ?
            """,
            (env_fingerprint, task_signature, route),
        ).fetchone()
        return dict(row)


def list_route_memory(env_fingerprint: str, task_signature: str, limit: int = 4) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM route_memory
            WHERE env_fingerprint = ? AND task_signature = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (env_fingerprint, task_signature, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]


def list_successful_routes(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT
              env_fingerprint,
              task_signature,
              route,
              success_count,
              failure_count,
              last_outcome,
              last_error_class,
              updated_at
            FROM route_memory
            WHERE success_count > 0
            ORDER BY success_count DESC, updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(row) for row in rows]


def create_git_checkpoint(job_id: str, cwd: str, branch: str, commit_hash: str, label: str = "") -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO git_checkpoints (job_id, cwd, branch, commit_hash, label, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (job_id, cwd, branch, commit_hash, label, ts, ts),
        )
        row = con.execute("SELECT * FROM git_checkpoints WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_git_checkpoints(job_id: str = "") -> list[dict[str, Any]]:
    with connect() as con:
        if job_id:
            rows = con.execute("SELECT * FROM git_checkpoints WHERE job_id = ? ORDER BY id DESC", (job_id,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM git_checkpoints ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]


def get_git_checkpoint(checkpoint_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM git_checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
        return dict(row) if row else None


def update_git_checkpoint_status(checkpoint_id: int, status: str) -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            "UPDATE git_checkpoints SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, checkpoint_id),
        )
        return cur.rowcount > 0


def list_skills(include_inactive: bool = True) -> list[dict[str, Any]]:
    with connect() as con:
        if include_inactive:
            rows = con.execute("SELECT * FROM skills ORDER BY updated_at DESC, id DESC").fetchall()
        else:
            rows = con.execute("SELECT * FROM skills WHERE status = 'active' ORDER BY updated_at DESC, id DESC").fetchall()
        return [dict(r) for r in rows]


def create_skill(
    name: str,
    description: str,
    triggers: str,
    source: str,
    body: str,
    status: str = "active",
) -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO skills (name, description, triggers, source, body, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, description, triggers, source, body, status, ts, ts),
        )
        return dict(con.execute("SELECT * FROM skills WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_skill(
    skill_id: int,
    name: str,
    description: str,
    triggers: str,
    source: str,
    body: str,
    status: str = "active",
) -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE skills
            SET name = ?, description = ?, triggers = ?, source = ?, body = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, description, triggers, source, body, status, ts, skill_id),
        )
        return cur.rowcount > 0


def delete_skill(skill_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        return cur.rowcount > 0


def _json_payload(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    if "payload_json" in data:
        try:
            data["payload"] = json.loads(data.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            data["payload"] = {}
    return data


def list_commands(include_inactive: bool = True) -> list[dict[str, Any]]:
    with connect() as con:
        sql = "SELECT * FROM assistant_commands ORDER BY name"
        if not include_inactive:
            sql = "SELECT * FROM assistant_commands WHERE status = 'active' ORDER BY name"
        return [_json_payload(r) for r in con.execute(sql).fetchall()]


def get_command(name: str) -> dict[str, Any] | None:
    clean = name.strip().lstrip("/").lower()
    with connect() as con:
        row = con.execute("SELECT * FROM assistant_commands WHERE lower(name) = ? AND status = 'active'", (clean,)).fetchone()
        return _json_payload(row) if row else None


def create_command(name: str, description: str, action_kind: str, payload: dict[str, Any], status: str = "active") -> dict[str, Any]:
    ts = now_iso()
    clean = name.strip().lstrip("/").lower()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO assistant_commands (name, description, action_kind, payload_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (clean, description, action_kind, json.dumps(payload, ensure_ascii=False), status, ts, ts),
        )
        return _json_payload(con.execute("SELECT * FROM assistant_commands WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_command(command_id: int, name: str, description: str, action_kind: str, payload: dict[str, Any], status: str = "active") -> bool:
    ts = now_iso()
    clean = name.strip().lstrip("/").lower()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE assistant_commands
            SET name = ?, description = ?, action_kind = ?, payload_json = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (clean, description, action_kind, json.dumps(payload, ensure_ascii=False), status, ts, command_id),
        )
        return cur.rowcount > 0


def delete_command(command_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM assistant_commands WHERE id = ?", (command_id,))
        return cur.rowcount > 0


def list_recurring_tasks(include_inactive: bool = True) -> list[dict[str, Any]]:
    with connect() as con:
        sql = "SELECT * FROM recurring_tasks ORDER BY updated_at DESC, id DESC"
        if not include_inactive:
            sql = "SELECT * FROM recurring_tasks WHERE status = 'active' ORDER BY updated_at DESC, id DESC"
        return [_json_payload(r) for r in con.execute(sql).fetchall()]


def list_due_recurring_tasks(now: str) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM recurring_tasks
            WHERE status = 'active' AND next_run_at != '' AND next_run_at <= ?
            ORDER BY next_run_at, id
            """,
            (now,),
        ).fetchall()
        return [_json_payload(r) for r in rows]


def create_recurring_task(name: str, prompt: str, schedule: str, action_kind: str, payload: dict[str, Any], status: str = "active", next_run_at: str = "") -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO recurring_tasks (name, prompt, schedule, action_kind, payload_json, status, next_run_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, prompt, schedule, action_kind, json.dumps(payload, ensure_ascii=False), status, next_run_at, ts, ts),
        )
        return _json_payload(con.execute("SELECT * FROM recurring_tasks WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_recurring_task(task_id: int, name: str, prompt: str, schedule: str, action_kind: str, payload: dict[str, Any], status: str = "active", next_run_at: str = "") -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE recurring_tasks
            SET name = ?, prompt = ?, schedule = ?, action_kind = ?, payload_json = ?, status = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, prompt, schedule, action_kind, json.dumps(payload, ensure_ascii=False), status, next_run_at, ts, task_id),
        )
        return cur.rowcount > 0


def mark_recurring_task_run(task_id: int, next_run_at: str = "") -> None:
    ts = now_iso()
    with connect() as con:
        con.execute(
            "UPDATE recurring_tasks SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
            (ts, next_run_at, ts, task_id),
        )


def delete_recurring_task(task_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM recurring_tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


def list_projects(include_inactive: bool = True) -> list[dict[str, Any]]:
    with connect() as con:
        sql = "SELECT * FROM projects ORDER BY updated_at DESC, id DESC"
        if not include_inactive:
            sql = "SELECT * FROM projects WHERE status = 'active' ORDER BY updated_at DESC, id DESC"
        return [dict(r) for r in con.execute(sql).fetchall()]


def create_project(name: str, root_path: str, summary: str, memory: str, status: str = "active") -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO projects (name, root_path, summary, memory, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, root_path, summary, memory, status, ts, ts),
        )
        return dict(con.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_project(project_id: int, name: str, root_path: str, summary: str, memory: str, status: str = "active") -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE projects
            SET name = ?, root_path = ?, summary = ?, memory = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, root_path, summary, memory, status, ts, project_id),
        )
        return cur.rowcount > 0


def delete_project(project_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0


def list_role_plugins(include_inactive: bool = True) -> list[dict[str, Any]]:
    with connect() as con:
        sql = "SELECT * FROM role_plugins ORDER BY updated_at DESC, id DESC"
        if not include_inactive:
            sql = "SELECT * FROM role_plugins WHERE status = 'active' ORDER BY updated_at DESC, id DESC"
        return [dict(r) for r in con.execute(sql).fetchall()]


def create_role_plugin(name: str, role: str, description: str, instructions: str, tool_hints: str, status: str = "active") -> dict[str, Any]:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO role_plugins (name, role, description, instructions, tool_hints, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, role, description, instructions, tool_hints, status, ts, ts),
        )
        return dict(con.execute("SELECT * FROM role_plugins WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_role_plugin(plugin_id: int, name: str, role: str, description: str, instructions: str, tool_hints: str, status: str = "active") -> bool:
    ts = now_iso()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE role_plugins
            SET name = ?, role = ?, description = ?, instructions = ?, tool_hints = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, role, description, instructions, tool_hints, status, ts, plugin_id),
        )
        return cur.rowcount > 0


def delete_role_plugin(plugin_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM role_plugins WHERE id = ?", (plugin_id,))
        return cur.rowcount > 0
