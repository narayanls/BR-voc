"""Preferências do usuário, em $XDG_DATA_HOME/br-voc/preferencias.json."""

from __future__ import annotations

import json
from pathlib import Path

from .history import data_dir

DEFAULTS = {
    "offline_only": False,  # não consultar o site da ABL, só a lista offline
}


class Settings:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or data_dir() / "preferencias.json"
        self._values = dict(DEFAULTS)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._values.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    @property
    def offline_only(self) -> bool:
        return bool(self._values["offline_only"])

    @offline_only.setter
    def offline_only(self, value: bool) -> None:
        self._values["offline_only"] = bool(value)
        self._save()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._values, indent=1), encoding="utf-8")
        except OSError:
            pass
