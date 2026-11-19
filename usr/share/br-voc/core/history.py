"""Histórico das últimas buscas, em $XDG_DATA_HOME/br-voc/historico.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

LIMIT = 20


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "br-voc"


class History:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or data_dir() / "historico.json"
        self._items: list[str] = []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._items = [str(x) for x in data][:LIMIT]
        except (OSError, ValueError):
            pass

    def items(self) -> list[str]:
        return list(self._items)

    def add(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        self._items = [query] + [q for q in self._items if q != query]
        self._items = self._items[:LIMIT]
        self._save()

    def clear(self) -> None:
        self._items = []
        self._save()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._items, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass
