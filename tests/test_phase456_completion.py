import subprocess

from fastapi.testclient import TestClient

from langgraph_manager import app as app_module
from langgraph_manager import db
from langgraph_manager.app import app, classify_action_policy


def test_host_browser_action_exposes_policy_classification():
    action = {
        "kind": "host_browser_open_current",
        "title": "Open Gmail",
        "preview": "Open mail.google.com",
        "payload": {"url": "https://mail.google.com"},
        "status": "pending",
    }

    policy = classify_action_policy(action)
    assert policy["approval_class"] == "credentialed"
    assert policy["requires_approval"] is True
    assert "browser" in policy["capability_tags"]
    assert "credentialed" in policy["capability_tags"]


def test_host_operator_write_actions_require_approval_and_do_not_auto_run():
    action = {
        "kind": "host_service_restart",
        "title": "Restart nginx",
        "preview": "Restart host service nginx",
        "payload": {"service": "nginx"},
        "status": "pending",
    }

    policy = classify_action_policy(action)
    assert policy["approval_class"] == "operator_write"
    assert policy["requires_approval"] is True
    assert app_module.should_auto_execute_action(action, "full_access") is False


def test_host_file_mutation_actions_require_approval():
    action = {
        "kind": "host_file_delete",
        "title": "Delete tmp file",
        "preview": "Delete host file",
        "payload": {"path": "/home/giang/tmp.txt"},
        "status": "pending",
    }

    policy = classify_action_policy(action)
    assert policy["approval_class"] == "operator_write"
    assert policy["requires_approval"] is True
    assert app_module.should_auto_execute_action(action, "full_access") is False


def test_host_process_recovery_requires_approval():
    action = {
        "kind": "host_process_recovery",
        "title": "Recover process",
        "preview": "Signal stuck process",
        "payload": {"pid": 4321, "signal_name": "TERM"},
        "status": "pending",
    }

    policy = classify_action_policy(action)
    assert policy["approval_class"] == "operator_write"
    assert policy["requires_approval"] is True
    assert app_module.should_auto_execute_action(action, "full_access") is False


def test_host_shell_and_file_actions_work_on_linux_foundation(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    sample = tmp_path / "sample.txt"
    sample.write_text("hello linux agent\n", encoding="utf-8")

    shell = app_module.run_host_shell_action("pwd", cwd=str(tmp_path), timeout=10)
    assert shell["ok"] is True
    assert str(tmp_path) in str(shell["data"]["stdout"])

    listing = app_module.list_host_files(str(tmp_path), limit=20)
    assert listing["ok"] is True
    assert any(item["name"] == "sample.txt" for item in listing["data"]["entries"])

    read = app_module.read_host_file(str(sample), max_chars=200)
    assert read["ok"] is True
    assert "hello linux agent" in read["data"]["content"]


def test_host_file_write_move_delete_work_on_linux_foundation(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "_try_host_worker_call", lambda *args, **kwargs: (False, "worker offline"))

    written = app_module.write_host_file(str(tmp_path / "draft.txt"), "hello host file\n", overwrite=True)
    assert written["ok"] is True
    assert (tmp_path / "draft.txt").read_text(encoding="utf-8") == "hello host file\n"

    moved = app_module.move_host_file(
        str(tmp_path / "draft.txt"),
        str(tmp_path / "archive" / "draft.txt"),
        overwrite=False,
        create_parents=True,
    )
    assert moved["ok"] is True
    assert not (tmp_path / "draft.txt").exists()
    assert (tmp_path / "archive" / "draft.txt").exists()

    deleted = app_module.delete_host_path(str(tmp_path / "archive"), recursive=True)
    assert deleted["ok"] is True
    assert not (tmp_path / "archive").exists()


def test_host_process_signal_local_fallback(monkeypatch):
    monkeypatch.setattr(app_module, "_try_host_worker_call", lambda *args, **kwargs: (False, "worker offline"))
    calls: list[tuple[int, int]] = []

    monkeypatch.setattr(app_module.os, "kill", lambda pid, sig: calls.append((int(pid), int(sig))))

    result = app_module.run_host_process_signal(4321, "TERM")
    assert result["ok"] is True
    assert result["data"]["pid"] == 4321
    assert calls and calls[0][0] == 4321


def test_host_shell_policy_blocks_destructive_command():
    blocked = app_module.run_host_shell_action("rm -rf /", cwd="/home/giang", timeout=5)
    assert blocked["ok"] is False
    assert "blocked destructive machine command" in blocked["summary"]


def test_host_worker_is_preferred_for_shell_files_and_ops(monkeypatch):
    def fake_host_browser_call(path, payload=None, method="POST", timeout=30):
        if path == "/shell/run":
            return {"ok": True, "summary": "remote shell", "data": {"stdout": "/real-host\n"}}
        if path == "/files/list":
            return {"ok": True, "summary": "remote list", "data": {"entries": [{"name": "host.txt"}]}}
        if path == "/files/read":
            return {"ok": True, "summary": "remote read", "data": {"content": "real host file"}}
        if path == "/ops/docker-ps":
            return {"ok": True, "summary": "remote docker", "data": {"containers": [{"Names": "host-api"}]}}
        if path == "/ops/service-status":
            return {"ok": True, "summary": "remote status", "data": {"service": "nginx", "stdout": "active"}}
        if path == "/ops/service-logs":
            return {"ok": True, "summary": "remote logs", "data": {"service": "nginx", "stdout": "started"}}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(app_module, "host_browser_call", fake_host_browser_call)

    shell = app_module.run_host_shell_action("pwd", cwd="/tmp", timeout=10)
    assert shell["ok"] is True
    assert shell["data"]["transport"] == "host_worker"
    assert "/real-host" in shell["data"]["stdout"]

    listing = app_module.list_host_files("/tmp", limit=20)
    assert listing["ok"] is True
    assert listing["data"]["transport"] == "host_worker"
    assert listing["data"]["entries"][0]["name"] == "host.txt"

    read = app_module.read_host_file("/tmp/host.txt", max_chars=50)
    assert read["ok"] is True
    assert read["data"]["transport"] == "host_worker"
    assert "real host file" in read["data"]["content"]

    docker = app_module.run_host_docker_ps(all_containers=True, limit=10, timeout=5)
    assert docker["ok"] is True
    assert docker["data"]["transport"] == "host_worker"
    assert docker["data"]["containers"][0]["Names"] == "host-api"

    status = app_module.run_host_service_status("nginx", lines=30, timeout=5)
    assert status["ok"] is True
    assert status["data"]["transport"] == "host_worker"
    assert status["data"]["service"] == "nginx"

    logs = app_module.run_host_service_logs("nginx", lines=20, since="1 hour ago", timeout=5)
    assert logs["ok"] is True
    assert logs["data"]["transport"] == "host_worker"
    assert "started" in logs["data"]["stdout"]


def test_job_response_exposes_notifications_session_summary_and_autonomy(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        created = client.post("/api/jobs", json={"request": "build testable workflow"}).json()["job"]
        client.post(f"/api/jobs/{created['id']}/approve")

        def fake_run(job_id, request, discussion, callback, permission_mode="full_access", focus_files=None):
            callback("verify_result", "done", "ok=False")
            return {
                "result": "Verify: NOT OK",
                "verification": {
                    "ok": False,
                    "missing_requirements": ["prove deployment health"],
                    "tool_failure_details": ["workspace_executor:permission_denied"],
                },
            }

        monkeypatch.setattr(app_module, "run_native_job_state", fake_run)
        ran = client.post(f"/api/jobs/{created['id']}/run").json()["job"]

        assert ran["notifications"]
        assert any(item["code"] == "verification_failed" for item in ran["notifications"])
        assert "status=failed" in ran["session_summary"]
        assert ran["autonomy"]["blocked"] is True
        assert ran["autonomy"]["stop_reason"] == "verification_failed"


def test_recurring_job_notification_and_autonomy_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        task = client.post(
            "/api/recurring-tasks",
            json={
                "name": "Morning report",
                "prompt": "Prepare daily report",
                "schedule": "daily",
                "action_kind": "note",
                "payload": {"note": "Prepare daily report"},
                "status": "active",
                "next_run_at": "2000-01-01T00:00:00+00:00",
            },
        ).json()["recurring_task"]
        queued = client.post("/api/recurring-tasks/run-due").json()["queued"]
        job = client.get(f"/api/jobs/{queued[0]['job_id']}").json()["job"]

        assert task["name"] in job["session_summary"]
        assert any(item["code"] == "recurring_due" for item in job["notifications"])
        assert job["autonomy"]["approval_required_count"] == 0
        assert job["next_actions"][0]["title"] == "Recurring: Morning report"


def test_host_shell_and_file_api_endpoints_return_structured_results(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    note = tmp_path / "note.txt"
    note.write_text("host file api\n", encoding="utf-8")

    with TestClient(app) as client:
        shell = client.post("/api/host-shell/run", json={"command": "pwd", "cwd": str(tmp_path), "timeout": 10})
        assert shell.status_code == 200
        assert shell.json()["ok"] is True

        listing = client.post("/api/host-files/list", json={"path": str(tmp_path), "limit": 20})
        assert listing.status_code == 200
        assert any(item["name"] == "note.txt" for item in listing.json()["data"]["entries"])

        read = client.post("/api/host-files/read", json={"path": str(note), "max_chars": 100})
        assert read.status_code == 200
        assert "host file api" in read.json()["data"]["content"]


def test_host_file_mutation_api_endpoints_return_structured_results(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "_try_host_worker_call", lambda *args, **kwargs: (False, "worker offline"))

    with TestClient(app) as client:
        write = client.post(
            "/api/host-files/write",
            json={"path": str(tmp_path / "draft.txt"), "content": "hello", "overwrite": True},
        )
        assert write.status_code == 200
        assert write.json()["ok"] is True

        move = client.post(
            "/api/host-files/move",
            json={
                "src_path": str(tmp_path / "draft.txt"),
                "dest_path": str(tmp_path / "moved" / "draft.txt"),
                "create_parents": True,
            },
        )
        assert move.status_code == 200
        assert move.json()["ok"] is True

        delete = client.post(
            "/api/host-files/delete",
            json={"path": str(tmp_path / "moved"), "recursive": True},
        )
        assert delete.status_code == 200
        assert delete.json()["ok"] is True


def test_linux_operator_workflows_are_structured_and_read_only(monkeypatch):
    def fake_run(argv, cwd, capture_output, text, timeout):
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout='{"Names":"web","Status":"Up 2 hours"}\n{"Names":"worker","Status":"Up 5 minutes"}\n',
                stderr="",
            )
        if argv[:2] == ["systemctl", "status"]:
            return subprocess.CompletedProcess(argv, 0, stdout="nginx.service active (running)\n", stderr="")
        if argv[:2] == ["journalctl", "-u"]:
            return subprocess.CompletedProcess(argv, 0, stdout="Jun 04 nginx started\n", stderr="")
        raise AssertionError(f"unexpected argv: {argv}")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    docker = app_module.run_host_docker_ps(all_containers=True, limit=10, timeout=5)
    assert docker["ok"] is True
    assert docker["data"]["all_containers"] is True
    assert docker["data"]["containers"][0]["Names"] == "web"

    status = app_module.run_host_service_status("nginx", lines=30, timeout=5)
    assert status["ok"] is True
    assert status["data"]["service"] == "nginx"
    assert "active" in status["data"]["stdout"]

    logs = app_module.run_host_service_logs("nginx", lines=20, since="1 hour ago", timeout=5)
    assert logs["ok"] is True
    assert logs["data"]["since"] == "1 hour ago"
    assert "nginx started" in logs["data"]["stdout"]

    docker_policy = classify_action_policy({"kind": "host_docker_ps", "payload": {}})
    assert docker_policy["approval_class"] == "read_only"
    assert "docker" in docker_policy["capability_tags"]


def test_execute_pending_action_supports_linux_operator_workflows(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()

    def fake_run(argv, cwd, capture_output, text, timeout):
        return subprocess.CompletedProcess(argv, 0, stdout='{"Names":"api","Status":"Up"}\n', stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    ok, output = app_module.execute_pending_action(
        {
            "kind": "host_docker_ps",
            "payload": {"all_containers": False, "limit": 5, "timeout": 5},
        },
        {"id": "job-1", "permission_mode": "full_access"},
    )

    assert ok is True
    assert '"Names": "api"' in output


def test_linux_operator_api_endpoints_return_structured_results(monkeypatch):
    def fake_run(argv, cwd, capture_output, text, timeout):
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(argv, 0, stdout='{"Names":"ops","Status":"Up"}\n', stderr="")
        if argv[:2] == ["systemctl", "status"]:
            return subprocess.CompletedProcess(argv, 0, stdout="ssh.service active (running)\n", stderr="")
        if argv[:2] == ["journalctl", "-u"]:
            return subprocess.CompletedProcess(argv, 0, stdout="Jun 04 ssh login accepted\n", stderr="")
        raise AssertionError(f"unexpected argv: {argv}")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    with TestClient(app) as client:
        docker = client.post("/api/host-ops/docker-ps", json={"all_containers": True, "limit": 10, "timeout": 5})
        assert docker.status_code == 200
        assert docker.json()["ok"] is True
        assert docker.json()["data"]["containers"][0]["Names"] == "ops"

        status = client.post("/api/host-ops/service-status", json={"service": "ssh", "lines": 20, "timeout": 5})
        assert status.status_code == 200
        assert status.json()["ok"] is True
        assert status.json()["data"]["service"] == "ssh"

        logs = client.post(
            "/api/host-ops/service-logs",
            json={"service": "ssh", "lines": 20, "since": "30 minutes ago", "timeout": 5},
        )
        assert logs.status_code == 200
        assert logs.json()["ok"] is True
        assert logs.json()["data"]["since"] == "30 minutes ago"


def test_linux_operator_write_and_recovery_workflows(monkeypatch):
    def fake_worker_call(path, payload=None, method="POST", timeout=30):
        if path == "/ops/docker-restart":
            return True, {"ok": True, "summary": "restarted docker", "data": {"container": payload["container"], "transport": "host_worker"}}
        if path == "/ops/service-restart":
            return True, {"ok": True, "summary": "restarted service", "data": {"service": payload["service"], "transport": "host_worker"}}
        if path == "/ops/docker-ps":
            return True, {"ok": True, "summary": "docker ps", "data": {"containers": [{"Names": payload.get("container", "api"), "Status": "Up"}], "transport": "host_worker"}}
        if path == "/ops/docker-logs":
            return True, {"ok": True, "summary": "docker logs", "data": {"container": payload["container"], "stdout": "container healthy", "transport": "host_worker"}}
        if path == "/ops/service-status":
            return True, {"ok": True, "summary": "service status", "data": {"service": payload["service"], "stdout": "active (running)", "transport": "host_worker"}}
        if path == "/ops/service-logs":
            return True, {"ok": True, "summary": "service logs", "data": {"service": payload["service"], "stdout": "service healthy", "transport": "host_worker"}}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(app_module, "_try_host_worker_call", fake_worker_call)

    docker_restart = app_module.run_host_docker_restart("api", timeout=10)
    assert docker_restart["ok"] is True
    assert docker_restart["data"]["container"] == "api"

    service_restart = app_module.run_host_service_restart("nginx", timeout=10)
    assert service_restart["ok"] is True
    assert service_restart["data"]["service"] == "nginx"

    container_recovery = app_module.run_host_container_recovery("api", logs_lines=40, timeout=10)
    assert container_recovery["ok"] is True
    assert container_recovery["data"]["restart"]["container"] == "api"
    assert "healthy" in container_recovery["data"]["logs"]["stdout"]

    service_recovery = app_module.run_host_service_recovery("nginx", status_lines=20, logs_lines=40, timeout=10)
    assert service_recovery["ok"] is True
    assert service_recovery["data"]["restart"]["service"] == "nginx"
    assert "active" in service_recovery["data"]["after"]["stdout"]


def test_linux_operator_write_and_recovery_api_endpoints(monkeypatch):
    def fake_worker_call(path, payload=None, method="POST", timeout=30):
        if path == "/ops/docker-restart":
            return True, {"ok": True, "summary": "restarted docker", "data": {"container": payload["container"], "transport": "host_worker"}}
        if path == "/ops/service-restart":
            return True, {"ok": True, "summary": "restarted service", "data": {"service": payload["service"], "transport": "host_worker"}}
        if path == "/ops/docker-ps":
            return True, {"ok": True, "summary": "docker ps", "data": {"containers": [{"Names": "api", "Status": "Up"}], "transport": "host_worker"}}
        if path == "/ops/docker-logs":
            return True, {"ok": True, "summary": "docker logs", "data": {"container": payload["container"], "stdout": "container healthy", "transport": "host_worker"}}
        if path == "/ops/service-status":
            return True, {"ok": True, "summary": "service status", "data": {"service": payload["service"], "stdout": "active (running)", "transport": "host_worker"}}
        if path == "/ops/service-logs":
            return True, {"ok": True, "summary": "service logs", "data": {"service": payload["service"], "stdout": "service healthy", "transport": "host_worker"}}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(app_module, "_try_host_worker_call", fake_worker_call)

    with TestClient(app) as client:
        docker_restart = client.post("/api/host-ops/docker-restart", json={"container": "api", "timeout": 10})
        assert docker_restart.status_code == 200
        assert docker_restart.json()["ok"] is True
        assert docker_restart.json()["data"]["container"] == "api"

        service_restart = client.post("/api/host-ops/service-restart", json={"service": "nginx", "timeout": 10})
        assert service_restart.status_code == 200
        assert service_restart.json()["ok"] is True
        assert service_restart.json()["data"]["service"] == "nginx"

        container_recovery = client.post("/api/host-ops/container-recovery", json={"container": "api", "logs_lines": 40, "timeout": 10})
        assert container_recovery.status_code == 200
        assert container_recovery.json()["ok"] is True
        assert container_recovery.json()["data"]["restart"]["container"] == "api"

        service_recovery = client.post("/api/host-ops/service-recovery", json={"service": "nginx", "status_lines": 20, "logs_lines": 40, "timeout": 10})
        assert service_recovery.status_code == 200
        assert service_recovery.json()["ok"] is True
        assert service_recovery.json()["data"]["restart"]["service"] == "nginx"


def test_host_process_signal_api_endpoint(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "_try_host_worker_call",
        lambda path, payload=None, method="POST", timeout=30: (
            True,
            {
                "ok": True,
                "summary": "process signaled",
                "data": {"pid": payload["pid"], "signal_name": payload["signal_name"], "transport": "host_worker"},
            },
        ),
    )

    with TestClient(app) as client:
        signaled = client.post("/api/host-worker/process-signal", json={"pid": 4321, "signal_name": "TERM"})
        assert signaled.status_code == 200
        assert signaled.json()["ok"] is True
        assert signaled.json()["data"]["pid"] == 4321


def test_host_process_recovery_workflow(monkeypatch):
    def fake_worker_call(path, payload=None, method="POST", timeout=30):
        if path.startswith("/processes?"):
            return True, {
                "ok": True,
                "summary": "process list",
                "data": {"entries": [{"pid": payload["pid"] if isinstance(payload, dict) and "pid" in payload else 4321}], "transport": "host_worker"},
            }
        if path == "/processes/signal":
            return True, {
                "ok": True,
                "summary": "process signaled",
                "data": {"pid": payload["pid"], "signal_name": payload["signal_name"], "transport": "host_worker"},
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(app_module, "_try_host_worker_call", fake_worker_call)

    recovery = app_module.run_host_process_recovery(4321, signal_name="TERM", query="4321")
    assert recovery["ok"] is True
    assert recovery["data"]["pid"] == 4321
    assert recovery["data"]["signal"]["pid"] == 4321
    assert "entries" in recovery["data"]["before"]
    assert "entries" in recovery["data"]["after"]


def test_host_process_recovery_api_endpoint(monkeypatch):
    def fake_worker_call(path, payload=None, method="POST", timeout=30):
        if path.startswith("/processes?"):
            return True, {
                "ok": True,
                "summary": "process list",
                "data": {"entries": [{"pid": 4321}], "transport": "host_worker"},
            }
        if path == "/processes/signal":
            return True, {
                "ok": True,
                "summary": "process signaled",
                "data": {"pid": payload["pid"], "signal_name": payload["signal_name"], "transport": "host_worker"},
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(app_module, "_try_host_worker_call", fake_worker_call)

    with TestClient(app) as client:
        recovered = client.post("/api/host-worker/process-recovery", json={"pid": 4321, "signal_name": "TERM", "query": "4321"})
        assert recovered.status_code == 200
        assert recovered.json()["ok"] is True
        assert recovered.json()["data"]["signal"]["pid"] == 4321


def test_inbox_prioritizes_verification_failures_and_recurring_attention(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        recurring = client.post(
            "/api/recurring-tasks",
            json={
                "name": "Morning report",
                "prompt": "Prepare daily report",
                "schedule": "daily",
                "action_kind": "note",
                "payload": {"note": "Prepare daily report"},
                "status": "active",
                "next_run_at": "2000-01-01T00:00:00+00:00",
            },
        ).json()["recurring_task"]
        queued = client.post("/api/recurring-tasks/run-due").json()["queued"]
        recurring_job_id = queued[0]["job_id"]

        created = client.post("/api/jobs", json={"request": "build testable workflow"}).json()["job"]
        client.post(f"/api/jobs/{created['id']}/approve")

        def fake_run(job_id, request, discussion, callback, permission_mode="full_access", focus_files=None):
            callback("verify_result", "done", "ok=False")
            return {
                "result": "Verify: NOT OK",
                "verification": {
                    "ok": False,
                    "missing_requirements": ["prove deployment health"],
                    "tool_failure_details": ["workspace_executor:permission_denied"],
                },
            }

        monkeypatch.setattr(app_module, "run_native_job_state", fake_run)
        client.post(f"/api/jobs/{created['id']}/run")

        inbox = client.get("/api/inbox").json()["inbox"]
        assert inbox
        assert inbox[0]["job_id"] == created["id"]
        assert "verification_failed" in inbox[0]["attention_reasons"]
        recurring_entry = next(item for item in inbox if item["job_id"] == recurring_job_id)
        assert "recurring_due" in recurring_entry["attention_reasons"]
        assert recurring["name"] in recurring_entry["session_summary"]


def test_inbox_exposes_primary_project_context(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        backend = client.post(
            "/api/projects",
            json={
                "name": "Backend API",
                "root_path": "/srv/backend-api",
                "summary": "Dockerized backend API and deployment health",
                "memory": "Use docker health checks and service logs first",
            },
        ).json()["project"]

        created = client.post(
            "/api/jobs",
            json={"request": "check docker health and service logs for Backend API in /srv/backend-api"},
        ).json()["job"]

        inbox = client.get("/api/inbox").json()["inbox"]
        entry = next(item for item in inbox if item["job_id"] == created["id"])
        assert entry["primary_project"]["id"] == backend["id"]
        assert entry["primary_project"]["name"] == "Backend API"
        assert entry["related_projects"][0]["score"] > 0


def test_project_assistant_view_groups_jobs_by_project(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        backend = client.post(
            "/api/projects",
            json={
                "name": "Backend API",
                "root_path": "/srv/backend-api",
                "summary": "Dockerized backend API and deployment health",
                "memory": "Prefer docker ps and service logs for outages",
            },
        ).json()["project"]
        frontend = client.post(
            "/api/projects",
            json={
                "name": "Landing Web",
                "root_path": "/srv/landing-web",
                "summary": "Marketing landing page and typography refresh",
                "memory": "Track hero, layout, and copy tasks",
            },
        ).json()["project"]

        client.post("/api/jobs", json={"request": "check docker health for Backend API in /srv/backend-api"})
        client.post("/api/jobs", json={"request": "refresh typography for Landing Web in /srv/landing-web"})

        grouped = client.get("/api/projects/assistant-view").json()["projects"]
        backend_entry = next(item for item in grouped if item["project"]["id"] == backend["id"])
        frontend_entry = next(item for item in grouped if item["project"]["id"] == frontend["id"])

        assert backend_entry["open_jobs_count"] == 1
        assert backend_entry["jobs"][0]["primary_project"]["name"] == "Backend API"
        assert frontend_entry["open_jobs_count"] == 1
        assert frontend_entry["jobs"][0]["primary_project"]["name"] == "Landing Web"

        single = client.get(f"/api/projects/{backend['id']}/assistant-view").json()
        assert single["project"]["id"] == backend["id"]
        assert single["jobs"][0]["primary_project"]["id"] == backend["id"]


def test_autonomy_reports_managed_loop_budget_and_blocks_extra_auto_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()

    job = db.create_chat_session("loop_budget_job", permission_mode="full_access")
    for index in range(3):
        action = db.add_pending_action(
            job["id"],
            "note",
            f"Auto step {index + 1}",
            "continue automatically",
            {"note": f"step {index + 1}", "continuation_mode": "auto"},
        )
        db.update_pending_action_status(int(action["id"]), "done", f"step {index + 1} done", "")
    pending = db.add_pending_action(
        job["id"],
        "note",
        "Auto step 4",
        "continue automatically",
        {"note": "step 4", "continuation_mode": "auto"},
    )

    hydrated = app_module.job_response(job["id"])["job"]
    assert hydrated["autonomy"]["stop_reason"] == "continuation_budget_exhausted"
    assert hydrated["autonomy"]["escalation_level"] == "budget"
    assert hydrated["autonomy"]["managed_loop_state"] == "blocked"
    assert hydrated["autonomy"]["loop"]["remaining_auto_continuations"] == 0
    assert hydrated["autonomy"]["can_continue_without_user"] is False
    assert hydrated["next_actions"][0]["action_id"] == pending["id"]
    assert hydrated["next_actions"][0]["blocked_reason"] == "continuation_budget_exhausted"
    assert hydrated["next_actions"][0]["auto_runnable"] is False
    assert hydrated["next_actions"][0]["continuation_mode"] == "auto"


def test_save_pending_action_respects_auto_continuation_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()
    job = db.create_chat_session("loop_guard_job", permission_mode="full_access")

    for index in range(4):
        action = db.add_pending_action(
            job["id"],
            "note",
            f"Auto step {index + 1}",
            "continue automatically",
            {"note": f"step {index + 1}", "continuation_mode": "auto"},
        )
        db.update_pending_action_status(int(action["id"]), "done", f"step {index + 1} done", "")

    started: list[int] = []
    monkeypatch.setattr(app_module, "start_action_background", lambda action_id: started.append(int(action_id)))

    saved = app_module.save_pending_action_from_decision(
        job["id"],
        {
            "reply": "Tiếp tục tự động.",
            "action": {
                "kind": "note",
                "title": "Auto continuation",
                "preview": "continue",
                "payload": {"note": "continue"},
            },
        },
        auto_execute_safe=True,
    )

    assert saved["status"] == "pending"
    assert started == []
    stored = db.get_pending_action(int(saved["id"]))
    assert stored["payload"]["continuation_mode"] == "auto"
