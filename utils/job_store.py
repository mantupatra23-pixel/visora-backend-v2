# utils/job_store.py
from pathlib import Path
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
JOBS_DIR = BASE_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)


class Job:
    storage_dir = JOBS_DIR

    def __init__(self,
                 id: str,
                 script_text: str = "",
                 preset: str = "short",
                 avatar: Optional[str] = None,
                 status: str = "created",
                 meta: Optional[Dict[str, Any]] = None,
                 result: Optional[Dict[str, Any]] = None,
                 created_at: Optional[str] = None,
                 completed_at: Optional[str] = None,
                 error: Optional[str] = None,
                 render_settings: Optional[Dict[str, Any]] = None):
        self.id = id
        self.script_text = script_text
        self.preset = preset
        self.avatar = avatar
        self.status = status
        self.meta = meta or {}
        self.result = result or {}
        self.created_at = created_at or datetime.utcnow().isoformat()
        self.completed_at = completed_at
        self.error = error
        self.render_settings = render_settings or {}

    @property
    def path(self) -> Path:
        return self.storage_dir / f"{self.id}.json"

    def to_dict(self):
        return {
            "id": self.id,
            "script_text": self.script_text,
            "preset": self.preset,
            "avatar": self.avatar,
            "status": self.status,
            "meta": self.meta,
            "result": self.result,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "render_settings": self.render_settings,
        }

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            raise

    @classmethod
    def create(cls, script_text: str, preset: str = "short", avatar: Optional[str] = None, render_settings=None):
        jid = str(uuid.uuid4())
        job = cls(id=jid, script_text=script_text, preset=preset, avatar=avatar, render_settings=render_settings or {})
        job.status = "created"
        job.save()
        return job

    @classmethod
    def get(cls, id: str):
        p = cls.storage_dir / f"{id}.json"
        if not p.exists():
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            job = cls(
                id=data.get("id"),
                script_text=data.get("script_text", ""),
                preset=data.get("preset", "short"),
                avatar=data.get("avatar"),
                status=data.get("status", "created"),
                meta=data.get("meta", {}),
                result=data.get("result", {}),
                created_at=data.get("created_at"),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                render_settings=data.get("render_settings", {}),
            )
            return job
        except Exception:
            return None

    @classmethod
    def find_many(cls, limit=100, skip=0):
        files = sorted(cls.storage_dir.glob("*.json"))
        out = []
        for p in files[skip: skip + limit]:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                continue
        return out
