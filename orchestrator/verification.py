from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except Exception:  # pragma: no cover - optional dependency fallback
    Draft202012Validator = None

from orchestrator.config import settings

VerificationStatus = str


@dataclass
class VerificationResult:
    passed: bool | None
    type: str
    details: str = ""
    exit_code: int | None = None
    verifier_used: str = "deterministic"
    status: VerificationStatus | None = None
    explanation: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if self.status is None:
            self.status = "passed" if self.passed is True else "failed" if self.passed is False else "unverified"
        if not self.explanation:
            self.explanation = self.details

    @property
    def verified(self) -> bool | None:
        return True if self.passed is True else False if self.passed is False else None

    def model_dump(self) -> dict[str, Any]:
        return {
            "verifier_used": self.verifier_used,
            "type": self.type,
            "status": self.status,
            "passed": self.passed,
            "verified": self.verified,
            "explanation": self.explanation,
            "details": self.details,
            "timestamp": self.timestamp,
            "evidence": self.evidence,
            "exit_code": self.exit_code,
        }


def _workspace_path(workspace_id: str) -> Path:
    root = Path(settings.orchestrator_workspace_root).resolve()
    path = (root / workspace_id).resolve()
    if root != path and root not in path.parents:
        raise ValueError("workspace_id resolves outside workspace root")
    return path


def _safe_workspace_target(workspace_id: str, rel: str) -> Path:
    root = _workspace_path(workspace_id)
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise ValueError("path escapes workspace")
    return target


def _run_command(
    command: str, workspace_id: str | None, timeout_seconds: int, vtype: str = "command_exit_zero"
) -> VerificationResult:
    cwd = _workspace_path(workspace_id) if workspace_id else None
    completed = subprocess.run(
        ["bash", "-lc", command],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    output = (
        (completed.stdout or "") + ("\n" if completed.stdout and completed.stderr else "") + (completed.stderr or "")
    )[-4000:]
    passed = completed.returncode == 0
    return VerificationResult(
        passed=passed,
        type=vtype,
        details=output,
        exit_code=completed.returncode,
        explanation="command exited with code 0" if passed else f"command exited with code {completed.returncode}",
        evidence={"command": command, "exit_code": completed.returncode, "output_excerpt": output},
    )


def unavailable(reason: str, *, vtype: str = "unavailable", verifier_used: str = "fallback") -> VerificationResult:
    return VerificationResult(
        passed=None,
        type=vtype,
        status="unverified",
        verifier_used=verifier_used,
        details=reason,
        explanation=reason,
        evidence={},
    )


def verify(
    spec: dict[str, Any] | None,
    *,
    workspace_id: str | None = None,
    text_result: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> VerificationResult:
    """Run conservative deterministic verification.

    Supported types: file_exists, json_schema, tests_pass, build_passes,
    command_exit_zero, artifact_created, expected_output_type, no_failed_subtasks,
    text_contains.

    A missing or unsupported verifier returns status=unverified instead of passed.
    Non-empty text by itself is never success evidence.
    """
    spec = spec or {}
    vtype = str(spec.get("type") or "unavailable")
    artifacts = artifacts or []

    if vtype == "unavailable":
        return unavailable("no explicit verification specification was provided")

    if vtype == "text_result":
        return unavailable("text_result presence is not explicit success evidence", vtype=vtype)

    if vtype == "text_contains":
        needle = str(spec.get("contains") or "")
        if not needle:
            return VerificationResult(False, vtype, "contains value is required")
        passed = bool(text_result and needle in text_result)
        return VerificationResult(
            passed,
            vtype,
            f"required text {'found' if passed else 'not found'}",
            evidence={"contains": needle, "text_excerpt": (text_result or "")[-1000:]},
        )

    if vtype == "file_exists":
        if not workspace_id:
            return VerificationResult(False, vtype, "workspace_id is required")
        rel = str(spec.get("path") or "")
        if not rel:
            return VerificationResult(False, vtype, "path is required")
        try:
            target = _safe_workspace_target(workspace_id, rel)
        except ValueError as exc:
            return VerificationResult(False, vtype, str(exc), evidence={"path": rel})
        passed = target.exists()
        return VerificationResult(
            passed, vtype, f"{rel} exists" if passed else f"{rel} does not exist", evidence={"path": rel}
        )

    if vtype == "json_schema":
        schema = spec.get("schema")
        data = spec.get("data")
        if data is None and text_result:
            try:
                data = json.loads(text_result)
            except json.JSONDecodeError as exc:
                return VerificationResult(
                    False, vtype, f"invalid JSON: {exc}", evidence={"text_excerpt": text_result[:1000]}
                )
        if not isinstance(schema, dict):
            return VerificationResult(False, vtype, "schema object is required")
        if Draft202012Validator is not None:
            errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
            if errors:
                return VerificationResult(False, vtype, errors[0].message)
            return VerificationResult(True, vtype, "json schema validation passed")
        required = schema.get("required", []) if isinstance(schema.get("required", []), list) else []
        if isinstance(data, dict) and all(key in data for key in required):
            return VerificationResult(True, vtype, "json schema fallback validation passed")
        return VerificationResult(False, vtype, "json schema fallback validation failed")

    if vtype == "expected_output_type":
        expected = str(spec.get("expected") or spec.get("expected_output_type") or "")
        if expected == "json":
            try:
                json.loads(text_result or "")
            except json.JSONDecodeError as exc:
                return VerificationResult(False, vtype, f"final result is not valid JSON: {exc}")
            return VerificationResult(True, vtype, "final result is valid JSON")
        if expected == "artifact":
            passed = bool(artifacts)
            return VerificationResult(
                passed, vtype, "artifact exists" if passed else "no artifact exists", evidence={"artifacts": artifacts}
            )
        if expected == "text":
            return unavailable("expected text output has no objective success evidence by itself", vtype=vtype)
        if expected in {"", "any"}:
            return unavailable("expected output type does not provide objective success evidence", vtype=vtype)
        return VerificationResult(False, vtype, f"unsupported expected output type: {expected}")

    if vtype == "artifact_created":
        name = spec.get("name")
        if name:
            passed = any(item.get("name") == name for item in artifacts)
            return VerificationResult(
                passed,
                vtype,
                f"artifact {name} {'found' if passed else 'missing'}",
                evidence={"name": name, "artifacts": artifacts},
            )
        return unavailable("artifact_created requires an explicit artifact name", vtype=vtype)

    if vtype in {"tests_pass", "build_passes", "command_exit_zero"}:
        command = str(spec.get("command") or ("pytest -q" if vtype == "tests_pass" else ""))
        if not command:
            return VerificationResult(False, vtype, "command is required")
        result = _run_command(command, workspace_id, int(spec.get("timeout_seconds", 300)), vtype=vtype)
        return result

    if vtype == "no_failed_subtasks":
        subtasks = spec.get("subtasks")
        if not isinstance(subtasks, list):
            return VerificationResult(False, vtype, "subtasks list is required")
        failed = [item.get("subtask_id") for item in subtasks if item.get("success") is not True]
        passed = not failed
        return VerificationResult(
            passed,
            vtype,
            "no failed subtasks" if passed else "failed subtasks present",
            evidence={"failed_subtasks": failed},
        )

    return unavailable(f"unsupported verification type: {vtype}", vtype=vtype)


def criteria_to_specs(
    criteria: dict[str, Any] | None, *, subtasks: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Expand task-level success criteria into deterministic verifier specs."""
    criteria = criteria or {}
    specs: list[dict[str, Any]] = []
    for path in criteria.get("required_files") or []:
        specs.append({"type": "file_exists", "path": str(path)})
    for command in criteria.get("required_commands") or []:
        if isinstance(command, dict):
            specs.append({"type": "command_exit_zero", **command})
        else:
            specs.append({"type": "command_exit_zero", "command": str(command)})
    expected = criteria.get("expected_output_type")
    if expected:
        specs.append({"type": "expected_output_type", "expected": expected})
    verification_command = criteria.get("verification_command")
    if verification_command:
        specs.append(
            {"type": "command_exit_zero", "command": str(verification_command), "verifier": "verification_command"}
        )
    for acceptance in criteria.get("acceptance_criteria") or []:
        if isinstance(acceptance, dict):
            specs.append(dict(acceptance))
        else:
            # Natural-language criteria cannot be treated as passed without an evaluator.
            specs.append({"type": "unavailable", "reason": str(acceptance)})
    if subtasks is not None:
        specs.append({"type": "no_failed_subtasks", "subtasks": subtasks})
    return specs


def verify_all(
    specs: list[dict[str, Any]],
    *,
    workspace_id: str | None = None,
    text_result: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate deterministic checks into the task verification summary contract."""
    results = [
        verify(spec, workspace_id=workspace_id, text_result=text_result, artifacts=artifacts).model_dump()
        for spec in specs
    ]
    failed = [item for item in results if item.get("passed") is False]
    unverified = [item for item in results if item.get("passed") is None]
    passed = bool(results) and not failed and not unverified
    status = "passed" if passed else "failed" if failed else "unverified"
    return {
        "verifier_used": "deterministic_composite",
        "status": status,
        "passed": passed if status != "unverified" else None,
        "verified": True if status == "passed" else False if status == "failed" else None,
        "explanation": "all success criteria passed"
        if status == "passed"
        else "one or more success criteria failed"
        if status == "failed"
        else "verification unavailable or insufficient",
        "timestamp": datetime.now(UTC).isoformat(),
        "evidence": {"checks": results},
    }
