"""Background maintenance is optional and never participates in registration correctness."""

import asyncio, logging, subprocess, shutil
from contextlib import asynccontextmanager, suppress
from .core import *

log = logging.getLogger("uvicorn.error")


def local_snapshot():
    if (
        not DEMO
        or os.getenv("DSU_PREVIEW_BACKUP", "1") != "1"
        or not shutil.which("pg_dump")
    ):
        return
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(DSN)
    # Production database credentials must never appear in a process command line.
    if info.get("host") != "/tmp/dsu-socket" or info.get("dbname") != "dsu":
        return
    path = ROOT / "data/pilot.dump"
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    env = os.environ.copy()
    env.update(
        PGHOST=info["host"], PGPORT=info.get("port", "5436"), PGDATABASE=info["dbname"]
    )
    subprocess.run(
        [
            "pg_dump",
            "-Fc",
            "--schema=public",
            "--no-owner",
            "--no-acl",
            "--exclude-table-data=sessions",
            "--exclude-table-data=login_attempts",
            "-f",
            str(tmp),
        ],
        env=env,
        check=True,
        timeout=90,
    )
    tmp.chmod(0o600)
    tmp.replace(path)


def maintenance():
    from .analytics import full_refresh

    with connect() as c:
        db = DB(c)
        db.run("DELETE FROM sessions WHERE expires_at<=now()")
        db.run("DELETE FROM login_attempts WHERE attempted_at<now()-interval '1 hour'")
        db.run(
            "UPDATE import_batches SET payload='{}'::jsonb WHERE created_at<now()-interval '2 hours' AND payload<>'{}'::jsonb"
        )
    full_refresh()
    local_snapshot()


@asynccontextmanager
async def lifespan(app):
    if app.dependency_overrides:
        yield
        return
    with connect() as c:
        marker = DB(c).val("SELECT value FROM workspace_metadata WHERE key='dataset'")
        if DEMO and marker != "synthetic":
            raise RuntimeError(
                "Synthetic preview requires an explicitly seeded, isolated synthetic database."
            )
        if not DEMO and marker == "synthetic":
            raise RuntimeError(
                "Refusing to serve synthetic fixtures as a production installation."
            )

    # Keep the dataset/mode validation above, but never run perpetual workers or
    # local snapshot writes inside an on-demand Vercel Function.
    if SERVERLESS:
        yield
        return

    async def worker():
        while True:
            try:
                await asyncio.to_thread(maintenance)
            except Exception:
                log.exception("Scheduled analytics/backup maintenance did not complete")
            await asyncio.sleep(60)

    job = (
        asyncio.create_task(worker())
        if DEMO or os.getenv("ENABLE_MAINTENANCE", "0") == "1"
        else None
    )
    try:
        yield
    finally:
        if job:
            job.cancel()
            with suppress(asyncio.CancelledError):
                await job
        if DEMO:
            await asyncio.to_thread(local_snapshot)
