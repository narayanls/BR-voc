"""Atualizações a partir dos releases do GitHub (sem dependência de GTK).

Fluxo:
  1. fetch_latest() consulta a API do GitHub e devolve o último release
     (rascunhos e pré-lançamentos são ignorados pela própria API).
  2. A versão do release vem do nome do pacote anexado
     (br-voc-<pkgver>-<pkgrel>-any.pkg.tar.zst); se não houver pacote, usa a
     tag (v1.2.0 ou 1.2.0). is_newer() compara com installed_version(), que
     pergunta ao pacman o que está de fato instalado.
  3. download() baixa o pacote para ~/.cache/br-voc/atualizacoes e confere o
     tamanho e o SHA-256 informados pelo GitHub.
  4. install() instala com "pkexec pacman -U", que pede a senha numa janela
     do sistema (polkit).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .cache import cache_dir

PKGNAME = "br-voc"
REPO = "narayanls/BR-voc"
API_LATEST = os.environ.get("BR_VOC_UPDATE_URL",  # permite testar com outro servidor
                            f"https://api.github.com/repos/{REPO}/releases/latest")
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
INSTALL_DIR = Path("/usr/share/br-voc")
TIMEOUT = 20
CHUNK = 64 * 1024

PACKAGE_RE = re.compile(
    rf"^{re.escape(PKGNAME)}-(?P<pkgver>[^-]+)-(?P<pkgrel>[^-]+)-any\.pkg\.tar\.(?:zst|xz)$")


class UpdateError(Exception):
    """Falha com mensagem pronta para mostrar ao usuário."""


class UpdateCancelled(Exception):
    pass


@dataclass
class Release:
    version: str          # pkgver, para exibir (ex.: 1.2.0)
    full_version: str     # pkgver-pkgrel quando há pacote, para comparar (ex.: 1.2.0-1)
    page_url: str
    notes: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_size: int = 0
    sha256: str | None = None


# ------------------------------------------------------------------ versões
def parse_version(value: str) -> tuple[tuple[int, ...], int | None]:
    """'v1.10.2' -> ((1, 10, 2), None); '1.10.2-3' -> ((1, 10, 2), 3)."""
    value = value.strip().lstrip("vV").split(":")[-1]  # ignora a época do pacman
    ver, _sep, rel = value.partition("-")
    numbers = tuple(int(n) for n in re.findall(r"\d+", ver))
    rel_match = re.match(r"\d+", rel)
    return numbers, int(rel_match.group()) if rel_match else None


def is_newer(remote: str, local: str) -> bool:
    """O pkgrel só desempata quando as duas versões o informam."""
    (r_ver, r_rel), (l_ver, l_rel) = parse_version(remote), parse_version(local)
    if r_ver != l_ver:
        return r_ver > l_ver
    return r_rel is not None and l_rel is not None and r_rel > l_rel


def installed_version() -> str:
    """Versão instalada segundo o pacman (ex.: '1.2.0-1').

    É a fonte confiável: não depende do código já carregado na memória.
    Rodando pelo código-fonte (ou sem pacman), usa __version__.
    """
    if install_support() != "source" and shutil.which("pacman"):
        try:
            result = subprocess.run(["pacman", "-Q", PKGNAME],
                                    capture_output=True, text=True, timeout=10)
            parts = result.stdout.split()
            if result.returncode == 0 and len(parts) == 2:
                return parts[1]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return __version__


def display_version(full: str) -> str:
    """'1.2.0-1' -> '1.2.0' (o pkgrel só interessa para comparar)."""
    return full.split(":")[-1].split("-")[0]


# ------------------------------------------------------------------ consulta
def _open(url: str, accept: str):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": f"BR-voc/{__version__}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def fetch_latest() -> Release:
    try:
        with _open(API_LATEST, "application/vnd.github+json") as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Nenhuma versão foi publicada ainda.") from exc
        if exc.code in (403, 429):
            raise UpdateError(
                "O GitHub está limitando as consultas agora. Tente mais tarde.") from exc
        raise UpdateError(
            f"Não foi possível procurar atualizações (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(
            "Não foi possível procurar atualizações. Verifique sua conexão.") from exc

    tag_version = str(data.get("tag_name") or "").lstrip("vV")
    release = Release(
        version=tag_version,
        full_version=tag_version,
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        notes=str(data.get("body") or "").strip(),
    )
    for asset in data.get("assets") or []:
        match = PACKAGE_RE.match(str(asset.get("name", "")))
        if not match:
            continue
        digest = str(asset.get("digest") or "")
        release.version = match["pkgver"]
        release.full_version = f'{match["pkgver"]}-{match["pkgrel"]}'
        release.asset_name = asset["name"]
        release.asset_url = asset.get("browser_download_url")
        release.asset_size = int(asset.get("size") or 0)
        release.sha256 = digest[7:].lower() if digest.startswith("sha256:") else None
        break
    if not release.version:
        raise UpdateError("A versão mais recente não tem número de versão.")
    return release


# --------------------------------------------------------------- instalação
def install_support() -> str:
    """'ok' ou o motivo de não poder instalar: 'source', 'pacman', 'pkexec'."""
    here = Path(__file__).resolve().parent.parent
    if here != INSTALL_DIR:
        return "source"
    if not shutil.which("pacman"):
        return "pacman"
    if not shutil.which("pkexec"):
        return "pkexec"
    return "ok"


def _download_dir() -> Path:
    return cache_dir() / "atualizacoes"


def download(release: Release, progress: Callable[[float | None], None] | None = None,
             cancel: threading.Event | None = None) -> Path:
    """Baixa o pacote. progress(fração 0..1 ou None) é chamado na thread atual."""
    if not release.asset_url or not release.asset_name:
        raise UpdateError("Esta versão não tem pacote para instalar.")

    directory = _download_dir()
    shutil.rmtree(directory, ignore_errors=True)  # sobras de tentativas antigas
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / release.asset_name
    partial = target.with_name(target.name + ".part")

    sha = hashlib.sha256()
    received = 0
    try:
        with _open(release.asset_url, "application/octet-stream") as resp, \
                open(partial, "wb") as fh:
            total = release.asset_size or int(resp.headers.get("Content-Length") or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateCancelled()
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                sha.update(chunk)
                received += len(chunk)
                if progress:
                    progress(received / total if total else None)
    except UpdateCancelled:
        partial.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError("O download falhou. Verifique sua conexão.") from exc

    if release.asset_size and received != release.asset_size:
        partial.unlink(missing_ok=True)
        raise UpdateError("O download ficou incompleto. Tente de novo.")
    if release.sha256 and sha.hexdigest() != release.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError(
            "O arquivo baixado está corrompido (soma de verificação não confere). "
            "Tente de novo.")
    partial.rename(target)
    return target


def install(package: Path) -> None:
    """Instala o pacote com pacman, pedindo a senha de administrador via polkit."""
    pkexec, pacman = shutil.which("pkexec"), shutil.which("pacman")
    if not pkexec or not pacman:
        raise UpdateError("A instalação automática precisa do pacman e do pkexec.")
    try:
        try:
            result = subprocess.run([pkexec, pacman, "-U", "--noconfirm", str(package)],
                                    capture_output=True, text=True)
        except OSError as exc:
            raise UpdateError(str(exc)) from exc
        if result.returncode in (126, 127):
            # 126: janela de senha cancelada; 127: não autorizado / sem agente polkit
            detail = result.stderr.strip()
            raise UpdateError("A autorização não foi concedida."
                              + (f"\n{detail}" if detail else ""))
        if result.returncode != 0:
            output = (result.stderr.strip() or result.stdout.strip()).splitlines()
            raise UpdateError("O pacman não conseguiu instalar a atualização:\n"
                              + "\n".join(output[-6:]))
    finally:
        package.unlink(missing_ok=True)
