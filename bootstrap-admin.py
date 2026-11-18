#!/usr/bin/env python3
from pathlib import Path
import sys, getpass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import *

name = os.getenv("BOOTSTRAP_USERNAME") or input("New administrator username: ").strip()
pw = os.getenv("BOOTSTRAP_PASSWORD") or getpass.getpass(
    "New administrator password (at least 12 characters): "
)
if len(name) < 3 or len(pw) < 12:
    raise SystemExit(
        "A unique username and a password of at least 12 characters are required."
    )
with connect() as c:
    db = DB(c)
    db.run("SELECT pg_advisory_xact_lock(740128)")
    if db.val(
        "SELECT count(*) FROM users u JOIN roles r ON r.id=u.role_id WHERE r.name='Super Administrator' AND active AND NOT demo"
    ):
        raise SystemExit(
            "A named administrator already exists; use authorised account management."
        )
    if (
        db.val("SELECT value FROM workspace_metadata WHERE key='dataset'")
        == "synthetic"
    ):
        raise SystemExit("Use a clean production database, not a demonstration copy.")
    uid = db.insert(
        "users",
        username=name,
        name="University Administrator",
        password_hash=PH.hash(pw),
        role_id=db.val("SELECT id FROM roles WHERE name='Super Administrator'"),
    )
    db.audit(None, "Bootstrapped named administrator", "users", uid)
print(
    "Named administrator created. Remove bootstrap secrets from the runtime environment."
)
