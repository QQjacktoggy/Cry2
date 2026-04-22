"""Tests for bot.runtime.review_bundle."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from bot.runtime import review_bundle as review_bundle_module
from bot.runtime.review_bundle import build_review_bundle, export_review_bundle


def test_bundle_contains_expected_members(tmp_path: Path) -> None:
    run_id = "11111111-aaaa-bbbb-cccc-222222222222"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.snapshot.yaml").write_text("environment: paper\n")
    (run_dir / "deploy_meta.json").write_text(json.dumps({"run_id": run_id}))

    journal_db = tmp_path / "paper_trades.db"
    journal_db.write_bytes(b"sqlite-placeholder")

    health = tmp_path / "health.json"
    health.write_text("{}")

    out_dir = tmp_path / "bundles"
    bundle = build_review_bundle(
        run_dir=run_dir,
        journal_db=journal_db,
        health_path=health,
        out_dir=out_dir,
    )

    assert bundle.exists()
    with tarfile.open(bundle, "r:gz") as tar:
        names = set(tar.getnames())
    assert "deploy_meta.json" in names
    assert "config.snapshot.yaml" in names
    assert "health.json" in names
    assert "paper_trades.db" in names


def test_bundle_name_uses_run_id(tmp_path: Path) -> None:
    run_id = "33333333-0000-0000-0000-444444444444"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.snapshot.yaml").write_text("environment: paper\n")
    (run_dir / "deploy_meta.json").write_text(json.dumps({"run_id": run_id}))

    journal_db = tmp_path / "t.db"
    journal_db.write_bytes(b"x")
    health = tmp_path / "h.json"
    health.write_text("{}")

    bundle = build_review_bundle(
        run_dir=run_dir,
        journal_db=journal_db,
        health_path=health,
        out_dir=tmp_path / "out",
    )
    assert bundle.name == f"{run_id}.tar.gz"


def test_bundle_requires_core_inputs(tmp_path: Path) -> None:
    run_id = "77777777-0000-0000-0000-888888888888"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "deploy_meta.json").write_text(json.dumps({"run_id": run_id}))

    journal_db = tmp_path / "paper_trades.db"
    journal_db.write_bytes(b"sqlite-placeholder")

    try:
        build_review_bundle(
            run_dir=run_dir,
            journal_db=journal_db,
            health_path=tmp_path / "health.json",
            out_dir=tmp_path / "out",
        )
    except FileNotFoundError as e:
        assert "config.snapshot.yaml" in str(e)
    else:
        raise AssertionError("Expected build_review_bundle to require config.snapshot.yaml")


def test_export_bundle_writes_account_snapshot_and_uploads(tmp_path: Path, monkeypatch) -> None:
    run_id = "55555555-0000-0000-0000-666666666666"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.snapshot.yaml").write_text("environment: paper\n")
    (run_dir / "deploy_meta.json").write_text(json.dumps({"run_id": run_id}))

    journal_db = tmp_path / "paper_trades.db"
    journal_db.write_bytes(b"sqlite-placeholder")
    health = tmp_path / "health.json"
    health.write_text("{}")

    uploads: list[tuple[Path, str, str]] = []

    def _fake_upload(bundle_path: str | Path, bucket: str, prefix: str = "review_bundles") -> bool:
        uploads.append((Path(bundle_path), bucket, prefix))
        return True

    monkeypatch.setattr(review_bundle_module, "upload_to_gcs", _fake_upload)

    result = export_review_bundle(
        run_dir=run_dir,
        journal_db=journal_db,
        health_path=health,
        out_dir=tmp_path / "out",
        account_snapshot={"assets": [{"asset": "USDT", "walletBalance": "123.45"}]},
        gcs_bucket="bundle-bucket",
    )

    assert result.bundle_path.exists()
    assert result.uploaded_to_gcs is True
    assert uploads == [(result.bundle_path, "bundle-bucket", "review_bundles")]

    with tarfile.open(result.bundle_path, "r:gz") as tar:
        names = set(tar.getnames())
    assert "account_snapshot.json" in names
