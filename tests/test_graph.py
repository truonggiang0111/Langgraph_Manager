from langgraph_manager.graph import build_graph


def test_manager_graph_smoke():
    app = build_graph()
    result = app.invoke({"request": "hello"})
    assert result["status"] == "done"
    assert "hello" in result["result"]
