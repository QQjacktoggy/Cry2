"""Review bundle export.

Purpose
-------
After a paper / live run we want a single versioned artefact that lets
an analyst (or GPT reviewer) reconstruct what happened without SSH
access to the VM:

::

    review_bundles/<run_id>.tar.gz
        paper_trades.db          # or live_trades.db
        health.json
        config.snapshot.yaml
        deploy_meta.json
        account_snapshot.json    # optional — only if callers provided it

Ergonomics
----------
Exposes both:

* :func:`build_review_bundle` — a library entry point the runner or
  ops script can call directly.
* ``python -m bot.runtime.review_bundle`` — CLI for ad-hoc use.

GCS upload is intentionally optional: the tarball is the irreversible
artefact, and a failed push should not prevent the bundle from being
available on disk for a later retry.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReviewBundleExportResult:
    """Outcome of a review bundle export."""

    bundle_path: Path
    uploaded_to_gcs: bool | None = None


def build_review_bundle(
    *,
    run_dir: str | Path,
    journal_db: str | Path,
    health_path: str | Path,
    out_dir: str | Path,
    account_snapshot: str | Path | None = None,
    bundle_name: str | None = None,
) -> Path:
    """Build a review bundle tarball.

    Args:
        run_dir: Directory created by ``write_run_snapshot`` containing
            ``config.snapshot.yaml`` and ``deploy_meta.json``.
        journal_db: Path to the SQLite trade journal.
        health_path: Path to the latest health snapshot JSON.
        out_dir: Directory to write ``<run_id>.tar.gz`` into.
        account_snapshot: Optional JSON describing exchange-side account
            state (balance, positions) at export time.
        bundle_name: Optional override for the bundle filename stem.

    Returns:
        Path to the written tarball.
    """
    run_dir_p = Path(run_dir)
    if not run_dir_p.is_dir():
        raise FileNotFoundError(f"run dir does not exist: {run_dir_p}")

    meta_path = run_dir_p / "deploy_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"deploy_meta.json missing from {run_dir_p}")
    config_snapshot_path = run_dir_p / "config.snapshot.yaml"
    journal_path = Path(journal_db)
    health_file_path = Path(health_path)
    required_inputs = [
        (config_snapshot_path, "config.snapshot.yaml"),
        (journal_path, f"journal db missing: {journal_path}"),
        (health_file_path, f"health snapshot missing: {health_file_path}"),
    ]
    for src, message in required_inputs:
        if not src.exists():
            raise FileNotFoundError(str(message))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    run_id = bundle_name or str(meta.get("run_id") or run_dir_p.name)

    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)
    bundle_path = out_dir_p / f"{run_id}.tar.gz"

    with tarfile.open(bundle_path, "w:gz") as tar:
        for src, arcname in [
            (meta_path, "deploy_meta.json"),
            (config_snapshot_path, "config.snapshot.yaml"),
            (journal_path, journal_path.name),
            (health_file_path, "health.json"),
        ]:
            tar.add(str(src), arcname=arcname)

        if account_snapshot and Path(account_snapshot).exists():
            tar.add(str(account_snapshot), arcname="account_snapshot.json")

    return bundle_path


def export_review_bundle(
    *,
    run_dir: str | Path,
    journal_db: str | Path,
    health_path: str | Path,
    out_dir: str | Path,
    account_snapshot: dict[str, Any] | None = None,
    gcs_bucket: str | None = None,
    gcs_prefix: str = "review_bundles",
) -> ReviewBundleExportResult:
    """Build a review bundle and optionally upload it to GCS."""
    account_snapshot_path: Path | None = None
    run_dir_p = Path(run_dir)
    if account_snapshot is not None:
        account_snapshot_path = run_dir_p / "account_snapshot.json"
        account_snapshot_path.write_text(
            json.dumps(account_snapshot, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    bundle_path = build_review_bundle(
        run_dir=run_dir_p,
        journal_db=journal_db,
        health_path=health_path,
        out_dir=out_dir,
        account_snapshot=account_snapshot_path,
    )

    uploaded: bool | None = None
    if gcs_bucket:
        uploaded = upload_to_gcs(bundle_path, bucket=gcs_bucket, prefix=gcs_prefix)

    return ReviewBundleExportResult(
        bundle_path=bundle_path,
        uploaded_to_gcs=uploaded,
    )


def upload_to_gcs(bundle_path: str | Path, bucket: str, prefix: str = "review_bundles") -> bool:
    """Best-effort upload via ``gsutil``.

    We shell out to ``gsutil`` rather than depend on the Python SDK:
    the SDK adds a ~50 MB dependency footprint for a one-shot copy,
    and every GCP VM already has ``gsutil`` installed.
    """
    gsutil = shutil.which("gsutil")
    if not gsutil:
        return False
    target = f"gs://{bucket.rstrip('/')}/{prefix.strip('/')}/{Path(bundle_path).name}"
    try:
        subprocess.run(
            [gsutil, "cp", str(bundle_path), target],
            check=True,
            timeout=120,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a review bundle")
    parser.add_argument("--run-dir", required=True, help="data/runs/<run_id> directory")
    parser.add_argument("--journal", required=True, help="trade journal .db path")
    parser.add_argument("--health", required=True, help="health.json path")
    parser.add_argument("--out", default="./data/review_bundles", help="output dir")
    parser.add_argument("--account-snapshot", default=None)
    parser.add_argument("--gcs-bucket", default=None, help="Optional GCS bucket for upload")
    parser.add_argument("--gcs-prefix", default="review_bundles")
    args = parser.parse_args(argv)

    bundle = build_review_bundle(
        run_dir=args.run_dir,
        journal_db=args.journal,
        health_path=args.health,
        out_dir=args.out,
        account_snapshot=args.account_snapshot,
    )
    print(f"bundle: {bundle}")

    if args.gcs_bucket:
        pushed = upload_to_gcs(bundle, bucket=args.gcs_bucket, prefix=args.gcs_prefix)
        print("gcs_upload:", "ok" if pushed else "skipped/failed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
