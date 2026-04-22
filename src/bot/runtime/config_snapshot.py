"""Config snapshotting for deploy / rollback traceability.

At every boot we dump the *fully merged* config that was actually used
into ``<runs_dir>/<run_id>/config.snapshot.yaml`` plus a parallel
``deploy_meta.json``. The pair is what the review bundle later packages
up so a reviewer can reproduce exactly what ran without needing access
to the VM filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from bot.runtime.deploy_meta import DeployMeta


def _redact_sensitive(data: Any) -> Any:
    """Strip anything that looks like a secret from a config before persisting.

    We never want the snapshot to carry real API keys. The config loader
    may have interpolated env vars directly into values, so we defensively
    mask well-known keys before dumping.
    """
    sensitive_keys = {
        "api_key",
        "api_secret",
        "bot_token",
        "chat_id",
        "password",
        "secret",
    }
    if isinstance(data, dict):
        return {
            k: ("***REDACTED***" if k.lower() in sensitive_keys else _redact_sensitive(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact_sensitive(v) for v in data]
    return data


def write_run_snapshot(
    *,
    config: dict[str, Any],
    meta: DeployMeta,
    runs_dir: str | Path = "./data/runs",
) -> Path:
    """Persist the merged config + deploy metadata under ``runs_dir/<run_id>``.

    Returns the run directory path so callers can reuse it for review
    bundle assembly.
    """
    run_dir = Path(runs_dir) / meta.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    snapshot = run_dir / "config.snapshot.yaml"
    snapshot.write_text(
        yaml.safe_dump(_redact_sensitive(config), sort_keys=True),
        encoding="utf-8",
    )

    deploy_meta_path = run_dir / "deploy_meta.json"
    deploy_meta_path.write_text(
        json.dumps(meta.as_dict(), indent=2),
        encoding="utf-8",
    )

    return run_dir
