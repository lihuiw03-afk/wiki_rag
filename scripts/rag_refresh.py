#!/usr/bin/env python3
"""Post-compile hook: rebuild deterministic RAG projections, then incrementally refresh QMD."""
from pathlib import Path
import argparse, atexit, json, os, shutil, subprocess, sys, time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".scripts"))
from rag_gateway.config import load_config
from rag_gateway.projection import build_projection, swap_projection, install_metadata
from _locklib import file_lock

try:
    from kb_job import list_jobs, update_active_jobs, finish_active_jobs, fail_active_jobs
except Exception:  # status tracking must never block projection
    list_jobs = update_active_jobs = finish_active_jobs = fail_active_jobs = None

p = argparse.ArgumentParser()
p.add_argument("--no-qmd", action="store_true", help="only rebuild local projection")
p.add_argument("--force-embed", action="store_true")
args = p.parse_args()
root = Path(__file__).resolve().parents[1]
REFRESH_LOCK = root / ".rag_refresh.lock"


def _acquire_refresh_lock():
    """Serialize all rag_refresh runs; OS lock is released on process exit."""
    while True:
        lock = file_lock(REFRESH_LOCK)
        try:
            lock.__enter__()
        except TimeoutError:
            print("refresh lock busy; waiting...", flush=True)
            time.sleep(5)
            continue
        atexit.register(lambda: _release_refresh_lock(lock))
        return


def _release_refresh_lock(lock):
    try:
        lock.__exit__(None, None, None)
    except Exception:
        pass


_acquire_refresh_lock()

# Snapshot the compile batch: an upload arriving during projection/embedding must
# stay queued and must not be marked done by this refresh.
active_job_ids = set()
if list_jobs is not None:
    try:
        active_job_ids = {
            str(j["job_id"]) for j in list_jobs()
            if j.get("status") == "running" and j.get("job_id")
        }
    except Exception:
        active_job_ids = set()


def status(stage, detail=None):
    if update_active_jobs is None:
        return
    try:
        update_active_jobs(stage, detail=detail, job_ids=active_job_ids)
    except Exception:
        pass


def finish(detail=None):
    if finish_active_jobs is None:
        return
    try:
        finish_active_jobs(detail=detail, job_ids=active_job_ids)
    except Exception:
        pass


def fail(reason):
    if fail_active_jobs is None:
        return
    try:
        fail_active_jobs(str(reason)[:2000], job_ids=active_job_ids)
    except Exception:
        pass

# 加载本地环境变量文件（视觉 API 密钥等；位于仓库之外，不入 git）。
# 用途：让本脚本被任何入口（cron 编译任务 / 手动运行）执行时都能拿到 COMPANY_WIKI_VISION_*。
_env_file = Path.home() / ".config" / "company-wiki" / "vision.env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

status("projection")
staging = root / "rag" / f".projection-staging-{os.getpid()}-{int(time.time())}"
try:
    # Build the complete projection off to the side. Live core/evidence/extracted
    # files remain readable while OCR/Vision and Wiki projection run.
    stats = build_projection(root, load_config(root), target_root=staging)
except Exception as exc:
    shutil.rmtree(staging, ignore_errors=True)
    fail(f"projection: {exc}")
    raise
try:
    switch_flag = root / "rag" / ".projection_switch"
    switch_flag.parent.mkdir(parents=True, exist_ok=True)
    switch_flag.write_text(str(os.getpid()), encoding="utf-8")
    try:
        previous = swap_projection(staging, root, defer_metadata=True)
    finally:
        try:
            switch_flag.unlink()
        except FileNotFoundError:
            pass
except Exception as exc:
    shutil.rmtree(staging, ignore_errors=True)
    fail(f"projection switch: {exc}")
    raise
print("projection:", json.dumps(stats, ensure_ascii=False))
print("projection switch: previous=", previous)
if args.no_qmd:
    try:
        install_metadata(staging, root, previous)
    except Exception as exc:
        fail(f"metadata switch: {exc}")
        raise
    finish({"qmd": "skipped"})
    raise SystemExit(0)
if not shutil.which("qmd"):
    print("qmd not installed; projection is ready but index refresh skipped", file=sys.stderr)
    fail("qmd not installed")
    raise SystemExit(3)
# Windows: CreateProcess 不解析无扩展名的 shim（bash 用 qmd，原生进程需要 qmd.cmd/qmd.exe）
if os.name == "nt":
    qmd = next((shutil.which(n) for n in ("qmd.cmd", "qmd.exe", "qmd") if shutil.which(n)), None)
else:
    qmd = shutil.which("qmd")
if not qmd:
    print("qmd not installed; projection is ready but index refresh skipped", file=sys.stderr)
    fail("qmd not installed")
    raise SystemExit(3)

status("qmd_update", {"manifest_nodes": stats.get("wiki_nodes", 0) + stats.get("evidence_chunks", 0)})
try:
    subprocess.run([qmd, "update"], cwd=root, check=True)
except Exception as exc:
    fail(f"qmd update: {exc}")
    raise

status("qmd_embed")
cmd = [qmd, "embed"]
if args.force_embed:
    cmd.append("-f")
try:
    subprocess.run(cmd, cwd=root, check=True)
except Exception as exc:
    fail(f"qmd embed: {exc}")
    raise

# Plan A: keep the old manifest/graph/lexicon active while QMD is being
# updated. Only after update+embed succeeds do we expose the new metadata.
try:
    install_metadata(staging, root, previous)
except Exception as exc:
    fail(f"metadata switch: {exc}")
    raise
finish({"qmd": "done"})
print("QMD index refreshed")
