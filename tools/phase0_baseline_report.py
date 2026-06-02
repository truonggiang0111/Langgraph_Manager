from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data"
DB_PATH = STATE_DIR / "manager.sqlite"
TRACE_PATH = STATE_DIR / "state" / "traces" / "manager_trace.log"
if not TRACE_PATH.exists():
    TRACE_PATH = STATE_DIR / "traces" / "manager_trace.log"


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def load_db_metrics() -> dict:
    if not DB_PATH.exists():
        return {"error": f"missing db at {DB_PATH}"}
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=24)).isoformat()
    jobs = con.execute("SELECT id, status, created_at, updated_at FROM jobs WHERE updated_at >= ?", (since,)).fetchall()
    actions = con.execute(
        "SELECT id, kind, status, error, created_at, updated_at FROM pending_actions WHERE updated_at >= ?",
        (since,),
    ).fetchall()
    con.close()

    job_status = Counter(row["status"] for row in jobs)
    action_status = Counter(row["status"] for row in actions)
    action_kind = Counter(row["kind"] for row in actions)
    errors = [str(row["error"] or "").strip() for row in actions if str(row["error"] or "").strip()]
    top_errors = Counter(errors).most_common(5)

    return {
        "window_hours": 24,
        "jobs_total": len(jobs),
        "jobs_status": dict(job_status),
        "actions_total": len(actions),
        "actions_status": dict(action_status),
        "actions_by_kind": dict(action_kind.most_common()),
        "top_action_errors": [{"error": err, "count": count} for err, count in top_errors],
    }


def load_trace_metrics() -> dict:
    if not TRACE_PATH.exists():
        return {"trace_file": str(TRACE_PATH), "events": 0, "note": "no trace file yet"}
    api_durations: list[float] = []
    api_failures = 0
    model_calls = Counter()
    route_calls = Counter()

    with TRACE_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            event = str(item.get("event") or "")
            if event == "api_request":
                elapsed = item.get("elapsed_ms")
                if isinstance(elapsed, (int, float)):
                    api_durations.append(float(elapsed))
                if not item.get("ok", True):
                    api_failures += 1
                route_calls[str(item.get("path") or "")] += 1
            if event == "model_call":
                route = str(item.get("route") or "unknown")
                model_calls[route] += 1

    avg_api = round(sum(api_durations) / len(api_durations), 2) if api_durations else 0.0
    p95_api = 0.0
    if api_durations:
        ordered = sorted(api_durations)
        idx = max(0, int(len(ordered) * 0.95) - 1)
        p95_api = round(ordered[idx], 2)
    return {
        "trace_file": str(TRACE_PATH),
        "api_requests": len(api_durations),
        "api_failures": api_failures,
        "api_avg_ms": avg_api,
        "api_p95_ms": p95_api,
        "top_routes": dict(route_calls.most_common(10)),
        "model_calls": dict(model_calls),
    }


def main() -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_metrics": load_db_metrics(),
        "trace_metrics": load_trace_metrics(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
