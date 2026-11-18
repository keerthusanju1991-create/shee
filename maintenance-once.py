#!/usr/bin/env python3
"""Run from a trusted external scheduler. There is deliberately no public cron URL."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.getenv("DATABASE_URL") or os.getenv("APP_MODE") not in {"synthetic", "production"}:
    raise SystemExit("Set the intended database URL and an explicit APP_MODE in the job environment.")
os.environ["DSU_PREVIEW_BACKUP"] = "0"
from backend.core import connect
from backend.lifecycle import maintenance

with connect() as connection:
    marker = connection.execute("SELECT value FROM workspace_metadata WHERE key = 'dataset'").fetchone()
    is_synthetic = bool(marker and marker["value"] == "synthetic")
    if is_synthetic != (os.environ["APP_MODE"] == "synthetic"):
        raise SystemExit("The dataset does not match APP_MODE. No maintenance was performed.")
maintenance()
print("Expired sessions and import payloads checked; eligibility snapshots refreshed.")
