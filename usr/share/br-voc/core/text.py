"""Utilitários de texto: comparação sem acentos e ordenação de sugestões."""

from __future__ import annotations

import difflib
import unicodedata
from typing import Iterable

MAX_SUGGESTIONS = 8


def fold(text: str) -> str:
    """Remove acentos e diferenças de maiúsculas/minúsculas."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def edit_distance(a: str, b: str, limit: int) -> int:
    """Distância de Damerau-Levenshtein (transposição simples), com corte em `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            value = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                value = min(value, prev2[j - 2] + 1)
            cur[j] = value
            best = min(best, value)
        if best > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def rank_suggestions(query: str, candidates: Iterable[str],
                     limit: int = MAX_SUGGESTIONS) -> list[str]:
    """Ordena grafias parecidas: menor distância de edição primeiro.

    Formas com pronome enclítico (ex.: "excelsa-o") só aparecem se a consulta
    também tiver hífen, para não empurrar as palavras comuns para baixo.
    """
    folded = fold(query)
    max_dist = 1 if len(folded) <= 4 else 2 if len(folded) <= 7 else 3
    query_has_hyphen = "-" in query
    matcher = difflib.SequenceMatcher(None, "", folded)

    scored = []
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        fc = fold(cand)
        if fc == folded or abs(len(fc) - len(folded)) > max_dist:
            continue
        if "-" in cand and not query_has_hyphen:
            continue
        matcher.set_seq1(fc)
        if matcher.real_quick_ratio() < 0.5 or matcher.quick_ratio() < 0.5:
            continue
        dist = edit_distance(fc, folded, max_dist)
        if dist > max_dist:
            continue
        # empate: mais parecida, mesma inicial, mais curta, alfabética
        scored.append((dist, -matcher.ratio(), fc[:1] != folded[:1], len(cand), cand))
    scored.sort()
    return [item[-1] for item in scored[:limit]]
