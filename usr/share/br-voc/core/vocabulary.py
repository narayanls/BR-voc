"""Regras de consulta: junta VOLP online, cache e lista offline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from . import volp
from .cache import Cache
from .offline import OfflineList
from .parser import ORIGIN_OFFLINE, Entry, parse_payload
from .settings import Settings
from .text import fold, rank_suggestions

__all__ = ["Status", "Source", "SearchResult", "Vocabulary", "fold", "normalize_query"]


class Status(Enum):
    FOUND = "found"            # grafia exata registrada
    VARIANT = "variant"        # registrada com outra acentuação/maiúscula
    NOT_FOUND = "not_found"    # não consta


class Source(Enum):
    ONLINE = "online"
    CACHE = "cache"
    STALE = "stale"      # cache vencido usado por falta de conexão
    OFFLINE = "offline"  # lista offline (VERO), não o VOLP


@dataclass
class SearchResult:
    query: str
    status: Status
    source: Source
    saved_at: float | None = None
    exact: list[Entry] = field(default_factory=list)
    variants: list[Entry] = field(default_factory=list)
    related: list[Entry] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    error: str = ""          # motivo de ter caído no modo offline, se houver
    offline_only: bool = False


def normalize_query(text: str) -> str:
    return " ".join(text.split())



def _status(exact, variants) -> Status:
    if exact:
        return Status.FOUND
    return Status.VARIANT if variants else Status.NOT_FOUND


class Vocabulary:
    def __init__(
        self,
        cache: Cache,
        offline: OfflineList | None = None,
        settings: Settings | None = None,
        client: Callable[[str], dict] = volp.search_raw,
    ) -> None:
        self.cache = cache
        self.offline = offline or OfflineList()
        self.settings = settings or Settings()
        self._client = client

    # -- acesso aos dados -------------------------------------------------

    def _entries(self, query: str) -> tuple[list[Entry], Source, float | None]:
        cached = self.cache.get(query)
        if cached and Cache.is_fresh(cached[1]):
            return parse_payload(cached[0]), Source.CACHE, cached[1]
        try:
            payload = self._client(query)
        except volp.VolpError:
            if cached:
                return parse_payload(cached[0]), Source.STALE, cached[1]
            raise
        self.cache.put(query, payload)
        return parse_payload(payload), Source.ONLINE, None

    # -- consulta principal -------------------------------------------------

    def lookup(self, text: str) -> SearchResult:
        query = normalize_query(text)
        if not query:
            raise ValueError("consulta vazia")

        if self.settings.offline_only and self.offline.installed:
            result = self._offline_lookup(query)
            result.offline_only = True
            return result

        try:
            entries, source, saved_at = self._entries(query)
        except volp.VolpError as exc:
            if not self.offline.installed:
                raise
            result = self._offline_lookup(query)
            result.error = str(exc)
            return result

        folded = fold(query)
        exact = [e for e in entries if e.termo == query]
        variants = [e for e in entries if e.termo != query and fold(e.termo) == folded]
        related = [e for e in entries if fold(e.termo) != folded]

        result = SearchResult(
            query=query,
            status=_status(exact, variants),
            source=source,
            saved_at=saved_at,
            exact=exact,
            variants=variants,
            related=related,
        )
        if result.status is Status.NOT_FOUND:
            result.suggestions = self._suggest(query, related)
        return result

    def _offline_lookup(self, query: str) -> SearchResult:
        data = self.offline.lookup(query)
        exact = [Entry(query, "", ORIGIN_OFFLINE)] if data["exact"] else []
        variants = [Entry(w, "", ORIGIN_OFFLINE) for w in data["variants"]]
        return SearchResult(
            query=query,
            status=_status(exact, variants),
            source=Source.OFFLINE,
            exact=exact,
            variants=variants,
            related=[Entry(w, "", ORIGIN_OFFLINE) for w in data["related"]],
            suggestions=data["suggestions"],
        )

    def _suggest(self, query: str, already: list[Entry]) -> list[str]:
        """Grafias parecidas: prefixos mais curtos no VOLP + lista offline (se houver)."""
        candidates: set[str] = {e.termo for e in already}

        if self.offline.installed:
            candidates.update(self.offline.suggest(query))

        prefixes: list[str] = []
        n = len(query)
        if n - 2 >= 3:
            prefixes.append(query[: n - 2])
        if n >= 6 and query[:4] not in prefixes:
            prefixes.append(query[:4])
        for prefix in prefixes:
            try:
                entries, _, _ = self._entries(prefix)
            except volp.VolpError:
                break
            candidates.update(e.termo for e in entries)

        return rank_suggestions(query, candidates)
