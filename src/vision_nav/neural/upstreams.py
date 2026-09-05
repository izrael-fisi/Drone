"""Versioned source lock for the cloud-only learned-navigation research stack."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any


UPSTREAM_LOCK_SCHEMA = "vision_nav_research_upstreams_v1"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_WORKFLOW_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def canonical_digest(raw: dict[str, Any]) -> str:
    payload = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_upstream_lock(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_upstream_lock(raw)
    return raw


def validate_upstream_lock(raw: dict[str, Any]) -> None:
    if raw.get("schema_version") != UPSTREAM_LOCK_SCHEMA:
        raise ValueError(f"Upstream lock must use {UPSTREAM_LOCK_SCHEMA}.")
    projects = raw.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ValueError("Upstream lock requires one or more projects.")
    names: set[str] = set()
    for project in projects:
        required = ("name", "role", "repository", "commit", "runtime_scope", "license_status")
        missing = [key for key in required if not project.get(key)]
        if missing:
            raise ValueError(f"Upstream project is missing: {', '.join(missing)}.")
        if project["name"] in names:
            raise ValueError(f"Duplicate upstream project: {project['name']}.")
        names.add(project["name"])
        if not str(project["repository"]).startswith("https://github.com/"):
            raise ValueError(f"Upstream repository must be an official HTTPS GitHub URL: {project['name']}.")
        if not _SHA.fullmatch(str(project["commit"])):
            raise ValueError(f"Upstream project requires a full immutable Git commit: {project['name']}.")
        if project["runtime_scope"] not in {"cloud_teacher", "pi_export_source", "map_production"}:
            raise ValueError(f"Unsupported runtime_scope for {project['name']}.")
        if project["license_status"] not in {"verified", "verify_before_distribution", "research_only"}:
            raise ValueError(f"Unsupported license_status for {project['name']}.")
    workflows = raw.get("workflows", [])
    if not isinstance(workflows, list):
        raise ValueError("Upstream workflows must be a list when provided.")
    workflow_ids: set[str] = set()
    for workflow in workflows:
        required = ("id", "project", "branch", "commit", "entrypoint", "data_contract")
        missing = [key for key in required if not workflow.get(key)]
        if missing:
            raise ValueError(f"Upstream workflow is missing: {', '.join(missing)}.")
        workflow_id = str(workflow["id"])
        if not _WORKFLOW_ID.fullmatch(workflow_id):
            raise ValueError(f"Invalid upstream workflow id: {workflow_id}.")
        if workflow_id in workflow_ids:
            raise ValueError(f"Duplicate upstream workflow: {workflow_id}.")
        workflow_ids.add(workflow_id)
        if workflow["project"] not in names:
            raise ValueError(f"Workflow references an unknown project: {workflow_id}.")
        if not _SHA.fullmatch(str(workflow["commit"])):
            raise ValueError(f"Workflow requires a full immutable Git commit: {workflow_id}.")
        if not isinstance(workflow["branch"], str) or not workflow["branch"].strip():
            raise ValueError(f"Workflow requires a source branch label: {workflow_id}.")
        entrypoint = Path(str(workflow["entrypoint"]))
        if entrypoint.is_absolute() or ".." in entrypoint.parts:
            raise ValueError(f"Workflow entrypoint must be relative to its checkout: {workflow_id}.")
        if workflow["data_contract"] not in {"official_dataset_layout", "vision_nav_map_pairs"}:
            raise ValueError(f"Unsupported workflow data contract: {workflow_id}.")
        checkpoint_key = workflow.get("required_checkpoint_key")
        if checkpoint_key is not None and (not isinstance(checkpoint_key, str) or not checkpoint_key.strip()):
            raise ValueError(f"Workflow checkpoint key must be a non-empty string: {workflow_id}.")


def workflow_by_id(lock: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    for workflow in lock.get("workflows", []):
        if workflow["id"] == workflow_id:
            return workflow
    raise ValueError(f"Unknown upstream workflow: {workflow_id}.")


def materialize_upstreams(lock: dict[str, Any], directory: str | Path) -> list[dict[str, str]]:
    """Clone locked source revisions. This is explicit research setup, never Pi runtime setup."""

    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for project in lock["projects"]:
        destination = root / project["name"]
        if destination.exists():
            raise ValueError(f"Refusing to overwrite existing upstream checkout: {destination}")
        subprocess.run(["git", "clone", "--no-checkout", project["repository"], str(destination)], check=True)
        subprocess.run(["git", "-C", str(destination), "checkout", "--detach", project["commit"]], check=True)
        results.append({"name": project["name"], "path": str(destination), "commit": project["commit"]})
    return results


def materialize_workflow(lock: dict[str, Any], workflow_id: str, directory: str | Path) -> dict[str, str]:
    """Materialize one immutable, model-specific research checkout.

    A workflow gets its own checkout because upstream projects can keep their
    official training stages on different branches. This intentionally does not
    modify the generic source checkout created by ``materialize_upstreams``.
    """

    workflow = workflow_by_id(lock, workflow_id)
    project = next(project for project in lock["projects"] if project["name"] == workflow["project"])
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / workflow["id"]
    if destination.exists():
        raise ValueError(f"Refusing to overwrite existing workflow checkout: {destination}")
    subprocess.run(["git", "clone", "--no-checkout", project["repository"], str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--detach", workflow["commit"]], check=True)
    return {
        "workflow_id": workflow["id"],
        "project": project["name"],
        "path": str(destination),
        "commit": workflow["commit"],
        "data_contract": workflow["data_contract"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or materialize pinned learned-navigation research sources.")
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--materialize-dir", type=Path, help="Explicitly clone locked research sources into this directory.")
    parser.add_argument("--materialize-workflow", help="Clone one immutable upstream training workflow into --workflow-dir.")
    parser.add_argument("--workflow-dir", type=Path, help="Destination for --materialize-workflow.")
    args = parser.parse_args()
    try:
        lock = load_upstream_lock(args.lock)
        if args.materialize_workflow and not args.workflow_dir:
            raise ValueError("--materialize-workflow requires --workflow-dir.")
        if args.workflow_dir and not args.materialize_workflow:
            raise ValueError("--workflow-dir requires --materialize-workflow.")
        results = materialize_upstreams(lock, args.materialize_dir) if args.materialize_dir else []
        workflow = (
            materialize_workflow(lock, args.materialize_workflow, args.workflow_dir)
            if args.materialize_workflow
            else None
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"status": "valid", "lock_digest_sha256": canonical_digest(lock), "materialized": results, "workflow": workflow}, indent=2))


if __name__ == "__main__":
    main()
