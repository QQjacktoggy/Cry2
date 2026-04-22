"""Runtime wiring helpers shared by live / paper runners.

This package hosts plumbing that every runtime (live, paper, future CLI
probes) needs but does not belong in the trading domain: deployment
metadata, config snapshotting, preflight checks, single-instance
guards, review-bundle export.
"""

from bot.runtime.deploy_meta import DeployMeta, build_deploy_meta

__all__ = ["DeployMeta", "build_deploy_meta"]
