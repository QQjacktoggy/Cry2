"""Deployment metadata for every bot run.

A ``DeployMeta`` is created once per process at startup and stamped onto
every persisted artefact (trade journal rows, health snapshot, review
bundle) so that later analysis can answer *which version, which config,
which process* produced the data.

Fields
------
run_id
    UUID4 generated at startup. Uniquely identifies this process.
config_fingerprint
    Short SHA-256 prefix over the fully merged configuration dict.
    Two runs with identical merged config share the same fingerprint.
git_sha
    Short git commit SHA. Sourced from ``GIT_SHA`` env var first, then
    ``git rev-parse --short HEAD`` as a fallback. Empty when neither
    works (e.g. a Docker image built without git history).
image_tag
    Docker image tag. Sourced from ``IMAGE_TAG`` env var. Optional.
environment
    ``backtest`` / ``paper`` / ``live`` — mirrors the merged config key
    so we never lie about which venue ran.
version
    Strategy pack label (``v6`` / ``v72`` / ``v74``).
boot_time
    UTC ISO-8601 timestamp captured at construction.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _compute_fingerprint(config: dict[str, Any]) -> str:
    """Return a short SHA-256 prefix over the merged config.

    We dump the dict with sorted keys to keep the fingerprint stable
    across ordering differences in upstream YAML merges.
    """
    payload = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _resolve_git_sha() -> str:
    """Best-effort git SHA resolution."""
    env_sha = os.environ.get("GIT_SHA", "").strip()
    if env_sha:
        return env_sha[:12]

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ""


@dataclass(frozen=True)
class DeployMeta:
    """Immutable metadata for a single bot process run."""

    run_id: str
    config_fingerprint: str
    git_sha: str
    image_tag: str
    environment: str
    version: str
    boot_time: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict[str, str]:
        """Return the metadata as a plain dict for JSON serialization."""
        return {k: str(v) for k, v in asdict(self).items()}


def build_deploy_meta(
    *,
    config: dict[str, Any],
    version: str,
    environment: str | None = None,
) -> DeployMeta:
    """Construct a ``DeployMeta`` for the current process.

    Args:
        config: Fully merged config dict (after environment overlays).
        version: Strategy pack label (``v6`` / ``v72`` / ``v74``).
        environment: Optional override; defaults to ``config["environment"]``.
    """
    env = environment or str(config.get("environment", ""))
    return DeployMeta(
        run_id=str(uuid.uuid4()),
        config_fingerprint=_compute_fingerprint(config),
        git_sha=_resolve_git_sha(),
        image_tag=os.environ.get("IMAGE_TAG", "").strip(),
        environment=env,
        version=version,
    )
