"""Cliente HTTP da busca no Vocabulário Ortográfico (VOLP) da ABL.

A página https://www.academia.org.br/nossa-lingua/busca-no-vocabulario usa
AngularJS e, ao buscar, faz um GET para:

    /ajax/abl/buscar-palavras?form=vocabulario&palavra=<termo>

A resposta é JSON no formato:

    {"header": ["Palavra", " "],
     "rows": [{"palavra": "<span class=\"item-palavra\">...</span>..."}]}

A busca é por prefixo, ignora acentos e devolve no máximo 100 linhas.
Só a biblioteca padrão é usada, para não exigir dependências extras.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

BASE_URL = "https://www.academia.org.br/ajax/abl/buscar-palavras"
PAGE_URL = "https://www.academia.org.br/nossa-lingua/busca-no-vocabulario"
TIMEOUT = 12  # segundos

_HEADERS = {
    "User-Agent": (
        f"Mozilla/5.0 (X11; Linux x86_64) BR-voc/{__version__}"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": PAGE_URL,
    "X-Requested-With": "XMLHttpRequest",
}


class VolpError(Exception):
    """Falha genérica ao consultar o VOLP."""


class VolpOffline(VolpError):
    """Sem conexão, DNS falhou ou o servidor não respondeu a tempo."""


class VolpRefused(VolpError):
    """O servidor respondeu, mas recusou a consulta ou mudou o formato."""


def build_url(term: str) -> str:
    query = urllib.parse.urlencode({"form": "vocabulario", "palavra": term})
    return f"{BASE_URL}?{query}"


def search_raw(term: str, timeout: float = TIMEOUT) -> dict:
    """Consulta o VOLP e devolve o JSON bruto (dict com 'header' e 'rows')."""
    request = urllib.request.Request(build_url(term), headers=_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429, 503):
            raise VolpRefused(
                f"O site da ABL recusou a consulta (HTTP {exc.code}). "
                "Tente de novo em alguns instantes."
            ) from exc
        raise VolpRefused(f"O site da ABL respondeu com erro HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VolpOffline("Não foi possível conectar ao site da ABL.") from exc

    try:
        payload = json.loads(body.decode(charset, errors="replace"))
    except ValueError as exc:
        # Normalmente acontece quando o Cloudflare devolve uma página de verificação.
        raise VolpRefused(
            "O site da ABL devolveu uma resposta inesperada "
            "(possível verificação anti-robô)."
        ) from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("rows", []), list):
        raise VolpRefused("O formato da resposta do VOLP mudou.")
    payload.setdefault("rows", [])
    return payload
