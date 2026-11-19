"""Lista de palavras para uso offline (VERO / LibreOffice pt-BR).

A ABL não disponibiliza o VOLP para download. Para funcionar sem internet, o
BR-voc baixa a lista de palavras pt-BR do projeto VERO, a mesma usada pelo
corretor do LibreOffice (licença LGPLv3 / MPL), que segue o Acordo Ortográfico.

A lista vem no formato do hunspell: ~312 mil radicais (.dic) + regras de
flexão (.aff). Expandir tudo gera ~10,5 milhões de formas, o que ocuparia
centenas de MB. Então o banco guarda só os radicais, e a consulta faz a
análise inversa dos afixos (como o hunspell): tira um sufixo/prefixo
possível, recompõe o radical e confere se ele aceita aquela regra.

Arquivos em $XDG_DATA_HOME/br-voc/offline/:
    radicais.sqlite3   radicais, chave sem acento e flags
    pt_BR.aff          regras de flexão
    LEIAME-VERO.txt    créditos e licença da lista
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .history import data_dir
from .text import fold, rank_suggestions

SOURCE_BASE = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/"
SOURCE_FILES = {"dic": "pt_BR.dic", "aff": "pt_BR.aff", "readme": "README_pt_BR.txt"}
SOURCE_NAME = "VERO – Verificador Ortográfico do LibreOffice (pt-BR)"

DB_NAME = "radicais.sqlite3"
AFF_NAME = "pt_BR.aff"
README_NAME = "LEIAME-VERO.txt"

MAX_POOL = 4000         # radicais expandidos para achar variantes/sugestões
MAX_WIDE_POOL = 30000   # radicais comparados quando o erro está no começo
MAX_RELATED = 100


def offline_dir() -> Path:
    return data_dir() / "offline"



class OfflineCancelled(Exception):
    pass


# --------------------------------------------------------------------------
# Regras de afixos (.aff)
# --------------------------------------------------------------------------

class AffixRules:
    """Subconjunto do formato .aff usado pelo VERO (FLAG UTF-8, sem compostos)."""

    def __init__(self, text: str) -> None:
        self.forbidden = ""
        self.nosuggest = ""
        # flag -> (tipo, cross_product, [(strip, add, regex)])
        self.rules: dict[str, tuple[str, bool, list]] = {}
        # índices para a análise inversa: add -> [(flag, strip, regex, cross)]
        self.sfx_by_add: dict[str, list] = {}
        self.pfx_by_add: dict[str, list] = {}

        for line in text.splitlines():
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            if parts[0] == "FORBIDDENWORD" and len(parts) > 1:
                self.forbidden = parts[1]
            elif parts[0] == "NOSUGGEST" and len(parts) > 1:
                self.nosuggest = parts[1]
            elif parts[0] in ("SFX", "PFX") and len(parts) >= 4:
                kind, flag = parts[0], parts[1]
                if flag not in self.rules:
                    self.rules[flag] = (kind, parts[2] == "Y", [])
                    continue
                strip = "" if parts[2] == "0" else parts[2]
                add = "" if parts[3] == "0" else parts[3].split("/")[0]
                cond = parts[4] if len(parts) > 4 else "."
                try:
                    rx = re.compile(("^" + cond) if kind == "PFX" else (cond + "$"))
                except re.error:
                    continue
                cross = self.rules[flag][1]
                self.rules[flag][2].append((strip, add, rx))
                index = self.pfx_by_add if kind == "PFX" else self.sfx_by_add
                index.setdefault(add, []).append((flag, strip, rx, cross))

    def expand(self, stem: str, flags: str) -> set[str]:
        """Todas as formas de um radical (usado só em lotes pequenos)."""
        forms = {stem}
        suffixed: list[tuple[str, bool]] = []
        for flag in flags:
            rule = self.rules.get(flag)
            if not rule or rule[0] != "SFX":
                continue
            for strip, add, rx in rule[2]:
                if rx.search(stem) and stem.endswith(strip):
                    suffixed.append((stem[: len(stem) - len(strip)] + add, rule[1]))
        forms.update(word for word, _ in suffixed)
        for flag in flags:
            rule = self.rules.get(flag)
            if not rule or rule[0] != "PFX":
                continue
            bases = [stem] + ([w for w, cross in suffixed if cross] if rule[1] else [])
            for strip, add, rx in rule[2]:
                for base in bases:
                    if rx.search(base) and base.startswith(strip):
                        forms.add(add + base[len(strip):])
        return forms


# --------------------------------------------------------------------------
# Banco local
# --------------------------------------------------------------------------

class OfflineList:
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = Path(folder) if folder else offline_dir()
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        self._rules: AffixRules | None = None

    # -- estado ------------------------------------------------------------

    @property
    def installed(self) -> bool:
        return (self.folder / DB_NAME).is_file() and (self.folder / AFF_NAME).is_file()

    def info(self) -> dict:
        """{'radicais': int, 'construido_em': float, 'tamanho': int} ou {}."""
        if not self.installed:
            return {}
        try:
            with self._lock:
                db = self._connection()
                meta = dict(db.execute("SELECT chave, valor FROM meta").fetchall())
            size = sum(f.stat().st_size for f in self.folder.iterdir() if f.is_file())
            return {
                "radicais": int(meta.get("radicais", 0)),
                "construido_em": float(meta.get("construido_em", 0)),
                "tamanho": size,
            }
        except (sqlite3.Error, OSError, ValueError):
            return {}

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
            self._db = None
            self._rules = None
            self._flags_for.cache_clear()

    def remove(self) -> None:
        self.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    # -- acesso ------------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            uri = f"file:{self.folder / DB_NAME}?mode=ro"
            self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)
        return self._db

    def _affixes(self) -> AffixRules:
        if self._rules is None:
            text = (self.folder / AFF_NAME).read_text(encoding="utf-8-sig")
            self._rules = AffixRules(text)
        return self._rules

    @lru_cache(maxsize=50_000)
    def _flags_for(self, stem: str) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT flags FROM radicais WHERE radical = ?", (stem,)
            ).fetchall()
        return tuple(r[0] for r in rows)

    def _stem_has(self, stem: str, flag: str | None, also: str | None = None) -> bool:
        forbidden = self._affixes().forbidden
        for flags in self._flags_for(stem):
            if forbidden and forbidden in flags:
                continue
            if flag is None or (flag in flags and (also is None or also in flags)):
                return True
        return False

    def _stems_by_key(self, start: str, limit: int) -> list[tuple[str, str]]:
        with self._lock:
            return self._connection().execute(
                "SELECT radical, flags FROM radicais WHERE chave >= ? AND chave < ? "
                "ORDER BY chave, radical LIMIT ?",
                (start, start + "￿", limit),
            ).fetchall()

    # -- análise -----------------------------------------------------------

    def _suffix_analyses(self, word: str):
        """Gera (radical, flag, cross) para cada sufixo que pode ter formado `word`."""
        rules = self._affixes()
        for i in range(1, len(word) + 1):
            for flag, strip, rx, cross in rules.sfx_by_add.get(word[i:], ()):
                base = word[:i] + strip
                if rx.search(base):
                    yield base, flag, cross

    def contains(self, word: str) -> bool:
        """A forma exata está na lista? (respeita acentos e maiúsculas)"""
        word = word.strip()
        if not word or not self.installed:
            return False
        with self._lock:
            if self._stem_has(word, None):
                return True
            for base, flag, _ in self._suffix_analyses(word):
                if self._stem_has(base, flag):
                    return True
            rules = self._affixes()
            for j in range(0, len(word)):
                for flag, strip, rx, cross in rules.pfx_by_add.get(word[:j], ()):
                    base = strip + word[j:]
                    if not rx.search(base):
                        continue
                    if self._stem_has(base, flag):
                        return True
                    if cross:
                        for stem, sflag, scross in self._suffix_analyses(base):
                            if scross and self._stem_has(stem, sflag, also=flag):
                                return True
        return False

    def lookup(self, query: str, want_suggestions: bool = True) -> dict:
        """Consulta completa: exata, variantes (acento/maiúscula), relacionadas, sugestões."""
        query = " ".join(query.split())
        folded = fold(query)
        result = {"exact": False, "variants": [], "related": [], "suggestions": []}
        if not query or not self.installed:
            return result

        with self._lock:
            rules = self._affixes()
            result["exact"] = self.contains(query)

            related = [
                stem for stem, flags in self._stems_by_key(folded, MAX_RELATED + 20)
                if stem != query and fold(stem) != folded
                and not (rules.forbidden and rules.forbidden in flags)
            ]
            result["related"] = related[:MAX_RELATED]

            # Radicais com o mesmo começo, expandidos, servem para achar
            # variantes de acentuação e grafias parecidas.
            pool = self._stems_by_key(folded[:4], MAX_POOL)
            forms = self._expand_pool(pool, rules)
            result["variants"] = sorted(f for f in forms if f != query and fold(f) == folded)

            if want_suggestions and not result["exact"] and not result["variants"]:
                # O erro pode estar antes da 4ª letra ("previlégio", "exeção"):
                # inclui radicais parecidos que só compartilham as 2 primeiras.
                forms |= self._expand_pool(self._similar_stems(folded), rules)
                result["suggestions"] = rank_suggestions(query, forms)
        return result

    @staticmethod
    def _expand_pool(pool, rules: AffixRules) -> set[str]:
        forms: set[str] = set()
        for stem, flags in pool:
            if rules.forbidden and rules.forbidden in flags:
                continue
            if rules.nosuggest and rules.nosuggest in flags:
                continue
            forms.update(rules.expand(stem, flags))
        return forms

    def _similar_stems(self, folded: str, keep: int = 250) -> list[tuple[str, str]]:
        if len(folded) < 3:
            return []
        matcher = difflib.SequenceMatcher(None, "", folded)
        scored = []
        for stem, flags in self._stems_by_key(folded[:2], MAX_WIDE_POOL):
            fs = fold(stem)
            # radicais costumam ser iguais ou um pouco menores que a forma
            if not (len(folded) - 6 <= len(fs) <= len(folded) + 3):
                continue
            matcher.set_seq1(fs)
            if matcher.real_quick_ratio() < 0.6 or matcher.quick_ratio() < 0.6:
                continue
            scored.append((matcher.ratio(), stem, flags))
        scored.sort(reverse=True)
        return [(stem, flags) for _, stem, flags in scored[:keep]]

    def suggest(self, query: str) -> list[str]:
        return self.lookup(query)["suggestions"]


# --------------------------------------------------------------------------
# Download e construção do banco
# --------------------------------------------------------------------------

Progress = Callable[[float, str], None]


def _download(url: str, target: Path, cancel: threading.Event,
              progress: Progress, start: float, span: float, label: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "BR-voc"})
    with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel.is_set():
                raise OfflineCancelled()
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total:
                progress(start + span * done / total,
                         f"{label} ({done / 1e6:.1f} de {total / 1e6:.1f} MB)")


def build_database(dic_path: Path, db_path: Path,
                   cancel: threading.Event | None = None,
                   progress: Progress | None = None) -> int:
    if db_path.exists():
        db_path.unlink()
    db = sqlite3.connect(db_path)
    count = 0
    try:
        db.execute("CREATE TABLE radicais (radical TEXT NOT NULL, chave TEXT NOT NULL, "
                   "flags TEXT NOT NULL)")
        db.execute("CREATE TABLE meta (chave TEXT PRIMARY KEY, valor TEXT)")
        with open(dic_path, encoding="utf-8-sig") as fh:
            header = fh.readline().strip()
            expected = int(header) if header.isdigit() else 0
            batch = []
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                stem, _, flags = line.partition("/")
                batch.append((stem, fold(stem), flags))
                if len(batch) >= 20_000:
                    if cancel and cancel.is_set():
                        raise OfflineCancelled()
                    db.executemany("INSERT INTO radicais VALUES (?, ?, ?)", batch)
                    count += len(batch)
                    batch.clear()
                    if progress and expected:
                        progress(0.75 + 0.2 * count / expected, "Montando o banco de palavras…")
            db.executemany("INSERT INTO radicais VALUES (?, ?, ?)", batch)
            count += len(batch)
        if progress:
            progress(0.96, "Criando índices…")
        db.execute("CREATE INDEX idx_radical ON radicais (radical)")
        db.execute("CREATE INDEX idx_chave ON radicais (chave)")
        db.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("radicais", str(count)),
            ("construido_em", str(time.time())),
            ("fonte", SOURCE_BASE),
        ])
        db.commit()
    finally:
        db.close()
    return count


def download_and_install(offline: OfflineList, cancel: threading.Event,
                         progress: Progress) -> int:
    """Baixa a lista do VERO e (re)constrói o banco. Devolve o nº de radicais."""
    staging = offline.folder.with_name(offline.folder.name + ".novo")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        dic = staging / SOURCE_FILES["dic"]
        aff = staging / AFF_NAME
        progress(0.0, "Baixando a lista de palavras…")
        _download(SOURCE_BASE + SOURCE_FILES["dic"], dic, cancel, progress,
                  0.0, 0.6, "Baixando a lista de palavras")
        _download(SOURCE_BASE + SOURCE_FILES["aff"], aff, cancel, progress,
                  0.6, 0.12, "Baixando as regras de flexão")
        try:
            _download(SOURCE_BASE + SOURCE_FILES["readme"], staging / README_NAME, cancel,
                      progress, 0.72, 0.03, "Baixando créditos e licença")
        except OSError:
            pass

        rules = AffixRules(aff.read_text(encoding="utf-8-sig"))
        if not rules.rules:
            raise ValueError("O arquivo de regras baixado está vazio ou corrompido.")
        count = build_database(dic, staging / DB_NAME, cancel, progress)
        if count < 100_000:
            raise ValueError("A lista baixada parece incompleta.")
        dic.unlink()

        offline.close()
        old = offline.folder.with_name(offline.folder.name + ".antigo")
        shutil.rmtree(old, ignore_errors=True)
        if offline.folder.exists():
            os.replace(offline.folder, old)
        os.replace(staging, offline.folder)
        shutil.rmtree(old, ignore_errors=True)
        progress(1.0, "Pronto")
        return count
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
