"""Interface do atualizador: verificação, diálogo de novidades, download e instalação."""

from __future__ import annotations

import shutil
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from core import APP_NAME, updater  # noqa: E402

AUTO_CHECK_DELAY = 3  # segundos após abrir o app

# Adw.AlertDialog só existe a partir da libadwaita 1.5; antes, Adw.MessageDialog.
_HAS_ALERT = hasattr(Adw, "AlertDialog")


def _alert(parent: Gtk.Window, heading: str, body: str):
    if _HAS_ALERT:
        return Adw.AlertDialog(heading=heading, body=body)
    return Adw.MessageDialog(transient_for=parent, modal=True, heading=heading, body=body)


def _present(dialog, parent: Gtk.Window) -> None:
    if _HAS_ALERT:
        dialog.present(parent)
    else:
        dialog.present()


def _on_closed(dialog, callback) -> None:
    """Chama callback quando o diálogo terminar de fechar."""
    dialog.connect("closed" if _HAS_ALERT else "destroy", callback)


class UpdateController:
    def __init__(self, window):
        self.win = window
        self.checking = False
        self.installing = False
        self.restart_on_close = False
        self._checking_toast = None

    def _toast(self, title: str, timeout: int = 3) -> None:
        self.win.toasts.add_toast(Adw.Toast(title=title, timeout=timeout))

    # ------------------------------------------------------------ verificação
    def check(self, manual: bool) -> None:
        if self.checking or self.installing:
            return
        self.checking = True
        if manual:
            self._checking_toast = Adw.Toast(title="Procurando atualizações…", timeout=0)
            self.win.toasts.add_toast(self._checking_toast)

        def worker():
            installed = updater.installed_version()
            try:
                release, error = updater.fetch_latest(), None
            except updater.UpdateError as exc:
                release, error = None, str(exc)
            GLib.idle_add(self._on_checked, release, installed, error, manual)

        threading.Thread(target=worker, daemon=True).start()

    def schedule_auto_check(self) -> None:
        def run():
            self.check(manual=False)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add_seconds(AUTO_CHECK_DELAY, run)

    def _on_checked(self, release, installed, error, manual) -> bool:
        self.checking = False
        if self._checking_toast is not None:
            self._checking_toast.dismiss()
            self._checking_toast = None
        if error:
            if manual:  # na verificação automática, falhas ficam em silêncio
                self._toast(error, 6)
            return GLib.SOURCE_REMOVE
        if not updater.is_newer(release.full_version, installed):
            if manual:
                self._toast(f"Você já tem a versão mais recente "
                            f"({updater.display_version(installed)}).")
            return GLib.SOURCE_REMOVE
        self.show_release(release, installed)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------- diálogo do release
    def show_release(self, release: updater.Release, installed: str) -> None:
        support = updater.install_support()
        if support == "ok" and not release.asset_url:
            support = "asset"
        reasons = {
            "source": f"O {APP_NAME} está rodando a partir do código-fonte, então não "
                      "consegue se atualizar sozinho. Baixe a nova versão no GitHub.",
            "pacman": "A instalação automática precisa do pacman (Arch Linux). "
                      "Baixe a nova versão no GitHub.",
            "pkexec": "A instalação automática precisa do pkexec (pacote polkit). "
                      "Baixe a nova versão no GitHub.",
            "asset": "Esta versão não tem pacote para instalar. Baixe-a no GitHub.",
        }
        new, current = release.version, updater.display_version(installed)
        if new == current:  # só o pkgrel mudou: mostra a versão completa
            new, current = release.full_version, installed
        body = f"A versão {new} está disponível. Você tem a versão {current}."
        if support != "ok":
            body += "\n\n" + reasons[support]

        dialog = _alert(self.win, "Atualização disponível", body)
        if release.notes:
            dialog.set_extra_child(self._notes_widget(release.notes))
        dialog.add_response("later", "Depois")
        dialog.add_response("github", "Ver no GitHub")
        if support == "ok":
            dialog.add_response("update", "Atualizar")
            dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("update")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_release_response, release)
        _present(dialog, self.win)

    @staticmethod
    def _notes_widget(notes: str) -> Gtk.Widget:
        label = Gtk.Label(label=notes, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          xalign=0, yalign=0)
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        label.set_margin_start(10)
        label.set_margin_end(10)
        scrolled = Gtk.ScrolledWindow(child=label, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      min_content_height=60, max_content_height=220,
                                      propagate_natural_height=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        title = Gtk.Label(label="Novidades", xalign=0, css_classes=["heading"])
        box.append(title)
        box.append(Gtk.Frame(child=scrolled))
        return box

    def _on_release_response(self, _dialog, response, release) -> None:
        if response == "github":
            Gtk.UriLauncher(uri=release.page_url).launch(self.win, None, None, None)
        elif response == "update":
            self._install(release)

    # ---------------------------------------------------- download e instalação
    def _install(self, release: updater.Release) -> None:
        if self.installing:
            return
        self.installing = True
        cancel = threading.Event()
        closed = threading.Event()

        bar = Gtk.ProgressBar(show_text=True)
        dialog = _alert(self.win, f"Atualizando o {APP_NAME}",
                        f"Baixando a versão {release.version}…")
        dialog.set_extra_child(bar)
        dialog.add_response("cancel", "Cancelar")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda *_args: cancel.set())
        _on_closed(dialog, lambda *_args: closed.set())
        _present(dialog, self.win)

        last = {"pct": -1}

        def on_progress(fraction):
            pct = -2 if fraction is None else int(fraction * 100)
            if pct != last["pct"]:
                last["pct"] = pct
                GLib.idle_add(self._set_progress, bar, fraction)

        def worker():
            try:
                package = updater.download(release, on_progress, cancel)
                GLib.idle_add(self._installing_phase, dialog, bar)
                updater.install(package)
                result = None
            except updater.UpdateCancelled:
                result = "cancelled"
            except updater.UpdateError as exc:
                result = str(exc)
            except Exception as exc:  # noqa: BLE001 - mostrado ao usuário
                result = f"Erro inesperado: {exc}"
            GLib.idle_add(self._finished, dialog, closed, release, result)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _set_progress(bar, fraction) -> bool:
        if fraction is None:
            bar.pulse()
        else:
            bar.set_fraction(fraction)
            bar.set_text(f"{int(fraction * 100)}%")
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _installing_phase(dialog, bar) -> bool:
        dialog.set_body("Instalando… Digite sua senha se o sistema pedir.")
        dialog.set_response_enabled("cancel", False)
        if _HAS_ALERT:
            dialog.set_can_close(False)
        else:
            # bloqueia Esc/Alt+F4; destroy() continua funcionando no fim
            dialog.connect("close-request", lambda *_args: True)
        bar.set_fraction(1.0)
        bar.set_text("")
        return GLib.SOURCE_REMOVE

    def _finished(self, progress_dialog, closed, release, error) -> bool:
        self.installing = False
        shown = {"done": False}

        def show_result(*_args):
            if not shown["done"]:
                shown["done"] = True
                self._show_result(release, error)
            return GLib.SOURCE_REMOVE

        if closed.is_set():
            return show_result()
        _on_closed(progress_dialog, show_result)

        if not _HAS_ALERT:
            progress_dialog.destroy()
            return GLib.SOURCE_REMOVE

        # Se a instalação terminar enquanto o diálogo ainda está abrindo, o
        # fechamento pode ser ignorado; tenta de novo até ele fechar de fato.
        def try_close():
            if closed.is_set():
                return GLib.SOURCE_REMOVE
            progress_dialog.set_can_close(True)
            progress_dialog.force_close()
            return GLib.SOURCE_CONTINUE

        try_close()
        GLib.timeout_add(250, try_close)
        return GLib.SOURCE_REMOVE

    def _show_result(self, release, error) -> None:
        if error == "cancelled":
            self._toast("Atualização cancelada")
            return
        if error:
            dialog = _alert(self.win, "A atualização falhou", error)
            dialog.add_response("close", "Fechar")
            dialog.add_response("github", "Ver no GitHub")
            dialog.set_close_response("close")
            dialog.connect("response", self._on_release_response, release)
            _present(dialog, self.win)
            return

        dialog = _alert(
            self.win, "Atualização instalada",
            f"O {APP_NAME} {release.version} foi instalado. "
            "Reinicie o app para usar a nova versão.")
        dialog.add_response("later", "Depois")
        dialog.add_response("restart", "Reiniciar agora")
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("restart")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_installed_response)
        _present(dialog, self.win)

    def _on_installed_response(self, dialog, response) -> None:
        if response == "restart":
            self.restart_on_close = True
            # A janela só fecha depois que o diálogo terminar de fechar.
            _on_closed(dialog, lambda *_args: self.win.close())

    # ---------------------------------------------------------------- reinício
    def relaunch_if_requested(self) -> None:
        """Chamado quando a janela realmente fecha."""
        if not self.restart_on_close:
            return
        launcher = shutil.which("br-voc") or "/usr/bin/br-voc"
        # A pausa garante que esta instância já saiu; senão o GTK apenas
        # reativaria a janela antiga em vez de abrir o app novo.
        subprocess.Popen(["sh", "-c", 'sleep 1.5; exec "$0"', launcher],
                         start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
