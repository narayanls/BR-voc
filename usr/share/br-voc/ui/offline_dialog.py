"""Diálogo "Uso offline": baixar, atualizar ou remover a lista de palavras."""

from __future__ import annotations

import threading
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from core.offline import (SOURCE_NAME, OfflineCancelled, OfflineList,  # noqa: E402
                          download_and_install)
from core.settings import Settings  # noqa: E402


class OfflineManager:
    """Controla o download em segundo plano, mesmo com o diálogo fechado."""

    def __init__(self, offline: OfflineList) -> None:
        self.offline = offline
        self.running = False
        self.fraction = 0.0
        self.text = ""
        self._cancel = threading.Event()
        self._listeners: list[Callable[[str, object], None]] = []

    def connect(self, callback: Callable[[str, object], None]) -> None:
        self._listeners.append(callback)

    def disconnect(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _emit(self, event: str, value: object = None) -> bool:
        for callback in list(self._listeners):
            callback(event, value)
        return GLib.SOURCE_REMOVE

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.fraction, self.text = 0.0, "Preparando…"
        self._cancel.clear()
        self._emit("started")

        def progress(fraction: float, text: str) -> None:
            self.fraction, self.text = fraction, text
            GLib.idle_add(self._emit, "progress", (fraction, text))

        def worker():
            try:
                count = download_and_install(self.offline, self._cancel, progress)
                event, value = "finished", count
            except OfflineCancelled:
                event, value = "cancelled", None
            except Exception as exc:  # noqa: BLE001 - mostrado ao usuário
                event, value = "failed", exc
            GLib.idle_add(self._finish, event, value)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, event: str, value: object) -> bool:
        self.running = False
        return self._emit(event, value)

    def cancel(self) -> None:
        self._cancel.set()


class OfflineDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window, manager: OfflineManager, settings: Settings,
                 on_changed: Callable[[], None]):
        super().__init__(transient_for=parent, modal=True, title="Uso offline",
                         default_width=500, default_height=640)
        self.manager = manager
        self.offline = manager.offline
        self.settings = settings
        self.on_changed = on_changed

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toasts)

        page = Adw.PreferencesPage()
        toolbar.set_content(page)

        intro = Adw.PreferencesGroup(
            description=(
                "A Academia Brasileira de Letras não disponibiliza o VOLP para download. "
                "Para consultar sem internet, o BR-voc baixa a lista de palavras "
                "usada pelo corretor do LibreOffice, que é livre e segue o Acordo Ortográfico. "
                "Ela não é o VOLP: em alguns casos as duas podem divergir."
            )
        )
        page.add(intro)

        # --- lista ----------------------------------------------------------
        group = Adw.PreferencesGroup(title="Lista de palavras")
        self.list_row = Adw.ActionRow(title="Lista pt-BR (VERO / LibreOffice)", use_markup=False)
        self.list_row.add_prefix(Gtk.Image(icon_name="folder-download-symbolic"))

        self.remove_button = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remover",
                                        valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.remove_button.connect("clicked", lambda *_: self._confirm_remove())
        self.action_button = Gtk.Button(valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        self.action_button.connect("clicked", lambda *_: self.manager.start())
        self.cancel_button = Gtk.Button(label="Cancelar", valign=Gtk.Align.CENTER)
        self.cancel_button.connect("clicked", lambda *_: self.manager.cancel())
        for button in (self.remove_button, self.cancel_button, self.action_button):
            self.list_row.add_suffix(button)
        group.add(self.list_row)

        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_margin_top(12)
        group.add(self.progress)
        page.add(group)

        # --- preferências ---------------------------------------------------
        prefs = Adw.PreferencesGroup(title="Preferências")
        title = "Usar só a lista offline"
        subtitle = "Não consulta o site da ABL, mesmo com internet"
        if hasattr(Adw, "SwitchRow"):  # libadwaita >= 1.4
            self.only_row = Adw.SwitchRow(title=title, subtitle=subtitle)
            self.only_row.set_active(settings.offline_only)
            self.only_row.connect("notify::active", self._on_only_toggled)
            self._only_widget = self.only_row
        else:
            self.only_row = Adw.ActionRow(title=title, subtitle=subtitle)
            switch = Gtk.Switch(active=settings.offline_only, valign=Gtk.Align.CENTER)
            switch.connect("notify::active", self._on_only_toggled)
            self.only_row.add_suffix(switch)
            self.only_row.set_activatable_widget(switch)
            self._only_widget = switch
        prefs.add(self.only_row)
        page.add(prefs)

        about = Adw.PreferencesGroup(
            title="Sobre a lista",
            description=(
                f"{SOURCE_NAME}. Licença LGPLv3 / MPL. Cerca de 312 mil radicais, que "
                "geram mais de 10 milhões de formas (plurais, femininos, conjugações). "
                "Ocupa uns 25 MB. Os créditos completos ficam salvos junto com a lista."
            ),
        )
        page.add(about)

        controller = Gtk.ShortcutController()
        controller.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("Escape"),
            Gtk.CallbackAction.new(lambda *_: self.close() or True)))
        self.add_controller(controller)

        self.manager.connect(self._on_manager_event)
        self.connect("close-request", self._on_close)
        self._refresh()

    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        running = self.manager.running
        installed = self.offline.installed
        info = self.offline.info() if installed and not running else {}

        if running:
            self.list_row.set_subtitle("Baixando…")
        elif info:
            date = time.strftime("%d/%m/%Y", time.localtime(info["construido_em"]))
            self.list_row.set_subtitle(
                f"Baixada em {date} · {info['radicais']:,} radicais · "
                f"{info['tamanho'] / 1e6:.0f} MB".replace(",", ".")
            )
        else:
            self.list_row.set_subtitle("Não baixada · cerca de 5,5 MB para baixar")

        self.action_button.set_label("Atualizar" if installed else "Baixar")
        self.action_button.set_visible(not running)
        self.action_button.remove_css_class("suggested-action")
        if not installed:
            self.action_button.add_css_class("suggested-action")
        self.remove_button.set_visible(installed and not running)
        self.cancel_button.set_visible(running)

        self.progress.set_visible(running)
        self.progress.set_fraction(self.manager.fraction)
        self.progress.set_text(self.manager.text)

        self.only_row.set_sensitive(installed and not running)

    def _on_manager_event(self, event: str, value) -> None:
        if event == "progress":
            fraction, text = value
            self.progress.set_fraction(fraction)
            self.progress.set_text(text)
            return
        if event == "finished":
            self.toasts.add_toast(Adw.Toast(title="Lista offline pronta", timeout=3))
            self.on_changed()
        elif event == "failed":
            self.toasts.add_toast(Adw.Toast(title=f"Falha ao baixar: {value}", timeout=6))
        elif event == "cancelled":
            self.toasts.add_toast(Adw.Toast(title="Download cancelado", timeout=2))
        self._refresh()

    def _on_only_toggled(self, widget, _pspec) -> None:
        self.settings.offline_only = widget.get_active()
        self.on_changed()

    def _confirm_remove(self) -> None:
        heading = "Remover a lista offline?"
        body = "Sem ela, o BR-voc só funciona com internet (e com o que estiver no cache)."
        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog(heading=heading, body=body)
        else:
            dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("remove", "Remover")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_remove_response)
        if hasattr(Adw, "AlertDialog"):
            dialog.present(self)
        else:
            dialog.present()

    def _on_remove_response(self, _dialog, response: str) -> None:
        if response != "remove":
            return
        self.offline.remove()
        if self.settings.offline_only:
            self.settings.offline_only = False
            self._only_widget.set_active(False)
        self.on_changed()
        self._refresh()
        self.toasts.add_toast(Adw.Toast(title="Lista offline removida", timeout=2))

    def _on_close(self, *_args) -> bool:
        self.manager.disconnect(self._on_manager_event)
        return False
