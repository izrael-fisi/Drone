"""Fail-closed preflight for model-specific upstream research workflows.

This module never starts a trainer. It verifies the immutable checkout and the
explicitly supplied data/checkpoint inputs before a CUDA operator follows an
upstream project's own documented training command.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any

from .upstreams import canonical_digest, load_upstream_lock, workflow_by_id


PREFLIGHT_SCHEMA = "vision_nav_upstream_training_preflight_v1"


def _git_head(checkout: Path) -> str:
    if not (checkout / ".git").exists():
        raise ValueError(f"Workflow checkout is not a Git worktree: {checkout}")
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    top = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "--show-toplevel"], text=True).strip()
    if Path(top).resolve() != checkout.resolve():
        raise ValueError("Expected root of its own pinned checkout")
    if subprocess.check_output(["git", "-C", str(checkout), "diff", "HEAD", "--"], text=True).strip():
        raise ValueError("Tracked upstream contents changed")
    tracked = set(subprocess.check_output(["git", "-C", str(checkout), "ls-files"], text=True).splitlines())
    if any(path.relative_to(checkout).as_posix() not in tracked for path in checkout.rglob("*.py")):
        raise ValueError("Untracked upstream Python could shadow pinned code")
    return result.stdout.strip()


def preflight_upstream_workflow(
    lock: dict[str, Any],
    workflow_id: str,
    *,
    checkout: str | Path,
    dataset_root: str | Path,
    checkpoint: str | Path | None = None,
) -> dict[str, Any]:
    """Validate one staged upstream workflow without changing its checkout."""

    workflow = workflow_by_id(lock, workflow_id)
    checkout_path = Path(checkout).expanduser().resolve()
    dataset_path = Path(dataset_root).expanduser().resolve()
    errors: list[str] = []
    actual_head: str | None = None
    try:
        actual_head = _git_head(checkout_path)
        if actual_head != workflow["commit"]:
            errors.append("checkout_commit_mismatch")
    except (ValueError, subprocess.CalledProcessError):
        errors.append("invalid_or_missing_git_checkout")
    entrypoint = checkout_path / workflow["entrypoint"]
    if not entrypoint.is_file():
        errors.append("missing_workflow_entrypoint")
    if not dataset_path.is_dir() or not any(dataset_path.iterdir()):
        errors.append("missing_or_empty_dataset_root")
    required_checkpoint_key = workflow.get("required_checkpoint_key")
    checkpoint_path = Path(checkpoint).expanduser().resolve() if checkpoint else None
    if required_checkpoint_key and (checkpoint_path is None or not checkpoint_path.is_file()):
        errors.append(f"missing_required_checkpoint:{required_checkpoint_key}")

    video2bev = None
    if workflow["project"] == "video2bev":
        from .video2bev_preflight import inspect_workflow
        video2bev = inspect_workflow(checkout_path, dataset_path, second_stage=bool(required_checkpoint_key))
        errors.extend(video2bev["errors"])

    return {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": "ready" if not errors else "not_ready",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "workflow": {
            "id": workflow["id"],
            "project": workflow["project"],
            "branch_label": workflow["branch"],
            "expected_commit": workflow["commit"],
            "data_contract": workflow["data_contract"],
            "entrypoint": workflow["entrypoint"],
            "required_checkpoint_key": required_checkpoint_key,
        },
        "checkout": {"path": str(checkout_path), "head_commit": actual_head},
        "dataset_root": str(dataset_path),
        "checkpoint": str(checkpoint_path) if checkpoint_path else None,
        "lock_digest_sha256": canonical_digest(lock),
        "errors": errors,
        "readiness_scope": "source_and_input_preflight_not_training_execution",
        "video2bev": video2bev,
        "publication": {"px4_external_vision": "disabled", "shadow_only": True},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight an immutable learned-navigation upstream training workflow.")
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = preflight_upstream_workflow(
            load_upstream_lock(args.lock),
            args.workflow,
            checkout=args.checkout,
            dataset_root=args.dataset_root,
            checkpoint=args.checkpoint,
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["status"] != "ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
