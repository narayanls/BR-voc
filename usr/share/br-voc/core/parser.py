"""Converte as linhas HTML devolvidas pelo VOLP em objetos Python.

Exemplos de linhas reais:

    <span class="item-palavra"><strong>manga</strong> de alpaca</span>
    <span class="descricao"> s.m.</span>

    <span class="item-palavra">Manga</span>
    <span class="descricao"> top. "município de Minas Gerais"; cf. manguense</span>
    <span class="palavra-toponimo">Topônimo</span>
    <span class="frase-toponimo">Este verbete faz parte do Vocabulário de ...</span>

    <span class="item-palavra"><strong>sport</strong>sman</span>
    <span class="descricao"> s.m. ing. </span>
    <span class="palavra-estrangeira">Estrangeira</span> ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

ORIGIN_VOLP = "volp"
ORIGIN_FOREIGN = "estrangeirismo"
ORIGIN_PLACE = "toponimo"
ORIGIN_OFFLINE = "offline"

_ORIGIN_BY_CLASS = {
    "palavra-estrangeira": ORIGIN_FOREIGN,
    "palavra-toponimo": ORIGIN_PLACE,
}

ORIGIN_LABELS = {
    ORIGIN_VOLP: "VOLP",
    ORIGIN_FOREIGN: "Estrangeirismo",
    ORIGIN_PLACE: "Topônimo",
    ORIGIN_OFFLINE: "Lista offline",
}


@dataclass(frozen=True)
class Entry:
    termo: str
    descricao: str = ""
    origem: str = ORIGIN_VOLP

    @property
    def origem_label(self) -> str:
        return ORIGIN_LABELS.get(self.origem, self.origem.capitalize())

    def to_dict(self) -> dict:
        return {"termo": self.termo, "descricao": self.descricao, "origem": self.origem}


def _squash(text: str) -> str:
    return " ".join(text.split())


class _RowParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[str | None] = []
        self.parts: dict[str, list[str]] = {}
        self.origin = ORIGIN_VOLP

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        classes = (dict(attrs).get("class") or "").split()
        name = classes[0] if classes else None
        self._stack.append(name)
        if name in _ORIGIN_BY_CLASS:
            self.origin = _ORIGIN_BY_CLASS[name]

    def handle_endtag(self, tag):
        if tag == "span" and self._stack:
            self._stack.pop()

    def handle_data(self, data):
        current = next((c for c in reversed(self._stack) if c), None)
        if current:
            self.parts.setdefault(current, []).append(data)


def parse_row(html: str) -> Entry | None:
    parser = _RowParser()
    parser.feed(html)
    parser.close()
    termo = _squash("".join(parser.parts.get("item-palavra", [])))
    if not termo:
        return None
    descricao = _squash("".join(parser.parts.get("descricao", [])))
    return Entry(termo=termo, descricao=descricao, origem=parser.origin)


def parse_payload(payload: dict) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[Entry] = set()
    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        entry = parse_row(str(row.get("palavra", "")))
        if entry and entry not in seen:
            seen.add(entry)
            entries.append(entry)
    return entries


# --- Abreviaturas usadas nas classificações do VOLP ------------------------

ABBREVIATIONS = {
    "s.m.": "substantivo masculino",
    "s.f.": "substantivo feminino",
    "s.2g.": "substantivo de dois gêneros",
    "s.": "substantivo",
    "adj.": "adjetivo",
    "adv.": "advérbio",
    "v.": "verbo",
    "prep.": "preposição",
    "conj.": "conjunção",
    "interj.": "interjeição",
    "pron.": "pronome",
    "art.": "artigo",
    "num.": "numeral",
    "loc.": "locução",
    "2g.": "de dois gêneros",
    "2n.": "de dois números",
    "pl.": "plural",
    "fem.": "feminino",
    "top.": "topônimo",
    "gent.": "gentílico",
    "el.": "elemento de composição",
    "pref.": "prefixo",
    "suf.": "sufixo",
    "red.": "redução",
    "sigla": "sigla",
    "cf.": "confronte (compare com)",
    "ing.": "inglês",
    "fr.": "francês",
    "it.": "italiano",
    "esp.": "espanhol",
    "al.": "alemão",
    "lat.": "latim",
    "jap.": "japonês",
}

_ABBR_RE = re.compile(
    r"(?<!\w)("
    + "|".join(re.escape(a) for a in sorted(ABBREVIATIONS, key=len, reverse=True))
    + r")"
)


def explain_description(descricao: str) -> str:
    """Devolve uma legenda das abreviaturas presentes, uma por linha."""
    found: dict[str, str] = {}
    for match in _ABBR_RE.finditer(descricao):
        abbr = match.group(1)
        found.setdefault(abbr, ABBREVIATIONS[abbr])
    return "\n".join(f"{abbr}  {meaning}" for abbr, meaning in found.items())
