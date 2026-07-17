from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_operational_docs_are_linked_from_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ["architecture.md", "deployment.md", "operations.md"]:
        assert (ROOT / "docs" / name).is_file()
        assert f"docs/{name}" in readme


def test_documented_validation_commands_match_project_tools():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "ruff check" in readme
    assert "mypy shared orchestrator node_agent agent_server" in readme
    assert "pytest -q" in readme
