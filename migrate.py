#!/usr/bin/env python3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import *

with connect() as c:
    db = DB(c)
    db.run("SELECT pg_advisory_xact_lock(740128)")
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        if not db.val("SELECT to_regclass('schema_migrations')") or not db.val(
            "SELECT version FROM schema_migrations WHERE version=%s", (path.stem,)
        ):
            db.run(path.read_text())
            print("Applied", path.stem)
    if not db.val("SELECT count(*) FROM roles"):
        setup_roles(db)
print("Schema is current.")
