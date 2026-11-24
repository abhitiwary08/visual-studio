"""Tiny file-backed job store. One folder per job, one job.json inside it."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import config

_LOCK = threading.RLock()
_MAX_LOG = 400


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class JobStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.JOBS_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- paths -------------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            raise KeyError(job_id)
        return self.root / job_id

    def _file(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    # ---- crud --------------------------------------------------------------
    def create(self, **fields: Any) -> dict:
        job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        job = {
            "id": job_id,
            "created": _now(),
            "updated": _now(),
            "status": "new",
            "stage": "",
            "progress": 0.0,
            "error": None,
            "warnings": [],
            "log": [],
            "script": None,
            "metadata": None,
            "assets": {},
            "outputs": {},
            "youtube": None,
        }
        job.update(fields)
        self.job_dir(job_id).mkdir(parents=True, exist_ok=True)
        self._write(job)
        return job

    def get(self, job_id: str) -> dict:
        with _LOCK:
            f = self._file(job_id)
            if not f.exists():
                raise KeyError(job_id)
            return json.loads(f.read_text(encoding="utf-8"))

    def update(self, job_id: str, **changes: Any) -> dict:
        with _LOCK:
            job = self.get(job_id)
            job.update(changes)
            job["updated"] = _now()
            self._write(job)
            return job

    def list(self) -> list[dict]:
        out = []
        with _LOCK:
            for d in sorted(self.root.iterdir(), reverse=True):
                f = d / "job.json"
                if d.is_dir() and f.exists():
                    try:
                        job = json.loads(f.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        continue
                    out.append(job)
        return out

    def delete(self, job_id: str) -> None:
        with _LOCK:
            d = self.job_dir(job_id)
            if d.exists():
                shutil.rmtree(d)

    # ---- progress helpers --------------------------------------------------
    def log(self, job_id: str, msg: str) -> None:
        with _LOCK:
            job = self.get(job_id)
            job["log"].append({"t": _now(), "msg": msg})
            job["log"] = job["log"][-_MAX_LOG:]
            job["updated"] = _now()
            self._write(job)

    def set_stage(self, job_id: str, stage: str, progress: float | None = None) -> None:
        changes: dict[str, Any] = {"stage": stage}
        if progress is not None:
            changes["progress"] = round(max(0.0, min(1.0, progress)), 3)
        self.update(job_id, **changes)

    def warn(self, job_id: str, msg: str) -> None:
        with _LOCK:
            job = self.get(job_id)
            if msg not in job["warnings"]:
                job["warnings"].append(msg)
            self._write(job)
        self.log(job_id, f"WARNING: {msg}")

    def recover_interrupted(self) -> None:
        """Jobs left in a running state by a crash/restart become retryable."""
        for job in self.list():
            if job.get("imaging"):
                self.update(job["id"], imaging=None, imaging_cancel=False)
            if job.get("previewing"):
                self.update(job["id"], previewing="")
            if job.get("translating"):
                self.update(job["id"], translating=False)
            if job.get("social_uploading"):
                self.update(job["id"], social_uploading="", error="Facebook/Instagram posting was interrupted by a restart.")
            if job["status"] in ("generating_script", "rendering", "uploading"):
                fallback = {
                    "generating_script": "new",
                    "rendering": "script_ready",
                    "uploading": "rendered",
                }[job["status"]]
                self.update(
                    job["id"],
                    status=fallback,
                    stage="",
                    error="Interrupted by a restart. You can run it again.",
                )

    # ---- internals ---------------------------------------------------------
    def _write(self, job: dict) -> None:
        f = self._file(job["id"])
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, f)
