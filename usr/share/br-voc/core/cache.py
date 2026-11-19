"""Cache local (SQLite) das respostas do VOLP.

Fica em $XDG_CACHE_HOME/br-voc/volp.sqlite3. Respostas novas valem por
30 dias; respostas vencidas continuam disponíveis quando não há conexão.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

FRESH_FOR = 30 * 24 * 3600  # 30 dias


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "br-voc"


class Cache:
    def __init__(self, path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        target = str(path) if path else str(cache_dir() / "volp.sqlite3")
        try:
            if target != ":memory:":
                Path(target).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(target, check_same_thread=False)
        except (OSError, sqlite3.Error):
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS respostas ("
                " consulta TEXT PRIMARY KEY,"
                " payload TEXT NOT NULL,"
                " salvo_em REAL NOT NULL)"
            )

    def get(self, query: str) -> tuple[dict, float] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT payload, salvo_em FROM respostas WHERE consulta = ?", (query,)
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0]), float(row[1])
        except ValueError:
            return None

    @staticmethod
    def is_fresh(saved_at: float) -> bool:
        return time.time() - saved_at < FRESH_FOR

    def put(self, query: str, payload: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO respostas (consulta, payload, salvo_em) VALUES (?, ?, ?)",
                (query, json.dumps(payload, ensure_ascii=False), time.time()),
            )

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM respostas").fetchone()[0]

    def clear(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM respostas")
        with self._lock:
            self._db.execute("VACUUM")
