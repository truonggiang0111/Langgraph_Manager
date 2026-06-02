from langgraph_manager.native_runtime import is_onboarding_agent_flow, make_plan, make_user_plan, run_native_job


def test_user_plan_uses_latest_user_goal_from_discussion():
    request = (
        "Create an executable plan from this ongoing session.\n\n"
        "Discussion:\n"
        "manager: hello\n"
        "user: sửa giao diện plan panel cho rõ approve và runtime steps\n"
    )

    plan = make_user_plan(request, "engineering", "low", ["repo_inspector", "coding_agent_executor"])

    assert plan[0] == "Chốt mục tiêu chính: sửa giao diện plan panel cho rõ approve và runtime steps"
    assert "Create an executable" not in plan[0]


def test_onboarding_agent_plan_prioritizes_evidence_gate():
    request = "Xay dung flow onboarding agent end-to-end gom backend UI test deploy checklist"

    plan = make_plan("engineering", "high", ["coding_agent_executor"], request)

    assert is_onboarding_agent_flow(request)
    assert plan == [
        "onboarding_scope_and_acceptance_evidence",
        "backend_flow_and_state_contract",
        "ui_entrypoint_and_report_visibility",
        "backend_ui_contract_tests",
        "anti_fake_report_verification_gate",
        "deploy_checklist_with_rollback",
        "final_verified_report",
    ]


def test_native_runtime_executes_graph_and_reports():
    events = []

    def cb(step, status, detail):
        events.append((step, status, detail))

    result = run_native_job(
        "lg_test",
        "Build backend LangGraph native manager with approve then execute",
        "user: keep n8n out of core execution",
        cb,
    )

    assert "LangGraph native backend" in result
    assert "Permission mode: auto_review" in result
    assert "Task graph:" in result
    assert "Tool results:" in result
    assert "DAG execution:" in result
    assert "memory_log: OK" in result
    assert any(step == "execute_worker_steps" and status == "done" for step, status, _ in events)
    assert any(step.startswith("task:T1:memory_log") and status == "done" for step, status, _ in events)
    assert any(step == "verify_result" and status == "done" for step, status, _ in events)
