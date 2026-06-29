from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_doc(name: str) -> str:
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def test_step615_docs_exist_and_are_linked_from_readme():
    for name in ["architecture.md", "deployment.md", "operations.md", "step6.15.md"]:
        assert (ROOT / "docs" / name).exists(), name
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Step 6.15 - Documentation" in readme
    assert "docs/architecture.md" in readme
    assert "docs/deployment.md" in readme
    assert "docs/operations.md" in readme


def test_architecture_document_covers_components_and_sequence():
    text = read_doc("architecture.md")
    required = [
        "Orchestrator",
        "Node-agent",
        "Postgres",
        "Redis",
        "MinIO",
        "Tool containers",
        "Workspaces",
        "sequenceDiagram",
        "POST /tasks",
        "POST /nodes/register",
        "CLUSTER_TOKEN",
        "OPERATOR_TOKEN",
        "ADMIN_TOKEN",
    ]
    for item in required:
        assert item in text


def test_deployment_document_covers_modes_and_environment():
    text = read_doc("deployment.md")
    required = [
        "Local development",
        "Single-machine deployment",
        "Multi-node deployment",
        "Production deployment",
        "DATABASE_URL",
        "QUEUE_URL",
        "NODE_ID",
        "ORCHESTRATOR_URL",
        "PUBLIC_NODE_URL",
        "CORS_ALLOW_ORIGINS",
        "MAX_REQUEST_BODY_BYTES",
        "MAX_RESPONSE_BODY_BYTES",
    ]
    for item in required:
        assert item in text


def test_operations_document_covers_failures_cleanup_and_troubleshooting():
    text = read_doc("operations.md")
    required = [
        "Docker socket access",
        "Tool containers",
        "Node dies",
        "Tool times out",
        "Verification fails",
        "Database is unavailable",
        "Redis is unavailable",
        "Artifact storage is unavailable",
        "Workspaces",
        "Artifacts",
        "Logs",
        "Old tasks",
        "Dead nodes",
        "Docker unavailable",
        "SQLAlchemy missing",
        "Redis connection failed",
        "Postgres connection failed",
        "Node not registering",
        "Task stuck running",
    ]
    for item in required:
        assert item in text
