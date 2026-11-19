"""Janela principal do BR-voc (GTK 4 + libadwaita)."""

from __future__ import annotations

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from core import APP_ID, APP_NAME  # noqa: E402
from core.history import History  # noqa: E402
from core.parser import (ORIGIN_FOREIGN, ORIGIN_PLACE, ORIGIN_VOLP, Entry,  # noqa: E402
                         explain_description)
from core.volp import PAGE_URL  # noqa: E402
from core.vocabulary import SearchResult, Source, Status, Vocabulary  # noqa: E402
from ui.offline_dialog import OfflineDialog, OfflineManager  # noqa: E402

MIN_CHARS = 2

_VERDICT = {
    Status.FOUND: ("object-select-symbolic", "success", "Registrada no VOLP"),
    Status.VARIANT: ("dialog-warning-symbolic", "warning", "Registrada com outra grafia"),
    Status.NOT_FOUND: ("window-close-symbolic", "error", "Não consta no VOLP"),
}

_OFFLINE_HEADLINE = {
    Status.FOUND: "Consta na lista offline",
    Status.VARIANT: "Na lista offline com outra grafia",
    Status.NOT_FOUND: "Não consta na lista offline",
}


def _fmt_date(timestamp: float | None) -> str:
    if not timestamp:
        return ""
    return time.strftime("%d/%m/%Y", time.localtime(timestamp))


class BrVocWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, vocabulary: Vocabulary, history: History):
        super().__init__(application=app, title=APP_NAME, default_width=720, default_height=680)
        self.set_size_request(360, 420)
        self.vocabulary = vocabulary
        self.history = history
        self._generation = 0
        self._current_query = ""
        self._banner_action = self._retry
        self.offline_manager = OfflineManager(vocabulary.offline)
        self.offline_manager.connect(self._on_offline_event)

        self._build_actions()
        self._build_ui()
        self._show_home()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Uso offline…", "win.offline")
        section.append("Abrir busca no site da ABL", "win.open-site")
        section.append("Limpar histórico", "win.clear-history")
        section.append("Limpar cache", "win.clear-cache")
        menu.append_section(None, section)
        about = Gio.Menu()
        about.append("Atalhos do teclado", "win.show-shortcuts")
        about.append(f"Sobre o {APP_NAME}", "app.about")
        menu.append_section(None, about)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu principal", primary=True))
        self.home_button = Gtk.Button(icon_name="go-home-symbolic", tooltip_text="Início",
                                      visible=False)
        self.home_button.connect("clicked", lambda *_: self._go_home())
        header.pack_start(self.home_button)
        self.offline_indicator = Gtk.Button(icon_name="network-offline-symbolic",
                                            tooltip_text="Modo offline: usando só a lista local",
                                            css_classes=["flat"])
        self.offline_indicator.connect("clicked", lambda *_: self._open_offline())
        header.pack_end(self.offline_indicator)
        toolbar.add_top_bar(header)

        self.search = Gtk.SearchEntry(placeholder_text="Digite uma palavra…", hexpand=True)
        self.search.set_search_delay(450)
        self.search.update_property([Gtk.AccessibleProperty.LABEL], ["Buscar no vocabulário"])
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("activate", self._on_search_activate)
        self.search.connect("stop-search", lambda *_: self._go_home())
        self.search.set_key_capture_widget(self)
        search_clamp = Adw.Clamp(maximum_size=640, child=self.search)
        search_clamp.set_margin_start(12)
        search_clamp.set_margin_end(12)
        search_clamp.set_margin_bottom(8)
        toolbar.add_top_bar(search_clamp)

        self.banner = Adw.Banner(button_label="Tentar de novo")
        self.banner.connect("button-clicked", lambda *_: self._banner_action())
        toolbar.add_top_bar(self.banner)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=150)

        # início
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        self.home_page = Adw.StatusPage(
            icon_name=APP_ID if theme.has_icon(APP_ID) else "accessories-dictionary-symbolic",
            title="Vocabulário Ortográfico",
        )
        self.home_page.add_css_class("compact")
        self.history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.home_page.set_child(Adw.Clamp(maximum_size=520, child=self.history_box))
        self.stack.add_named(self.home_page, "home")

        # carregando
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                              valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.spinner = Gtk.Spinner(width_request=32, height_request=32)
        spinner_box.append(self.spinner)
        spinner_box.append(Gtk.Label(label="Consultando…", css_classes=["dim-label"]))
        self.stack.add_named(spinner_box, "loading")

        # resultado
        self.result_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.stack.add_named(self.result_scroller, "result")

        # erro
        self.error_page = Adw.StatusPage(icon_name="network-offline-symbolic",
                                         title="Não foi possível consultar")
        error_buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                halign=Gtk.Align.CENTER)
        retry = Gtk.Button(label="Tentar de novo", css_classes=["pill", "suggested-action"])
        retry.connect("clicked", lambda *_: self._retry())
        self.error_offline_button = Gtk.Button(label="Baixar lista para uso offline",
                                               css_classes=["pill"])
        self.error_offline_button.connect("clicked", lambda *_: self._open_offline())
        error_buttons.append(retry)
        error_buttons.append(self.error_offline_button)
        self.error_page.set_child(error_buttons)
        self.stack.add_named(self.error_page, "error")

        toolbar.set_content(self.stack)
        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)
        self._update_offline_state()

    def _build_actions(self) -> None:
        actions = {
            "focus-search": self._focus_search,
            "open-site": self._open_site,
            "clear-history": self._clear_history,
            "clear-cache": self._clear_cache,
            "show-shortcuts": self._show_shortcuts,
            "offline": self._open_offline,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

    # ------------------------------------------------------------ páginas

    def _show_home(self) -> None:
        self._rebuild_history()
        self.stack.set_visible_child_name("home")
        self.banner.set_revealed(False)
        self.home_button.set_visible(False)

    def _go_home(self) -> None:
        self._generation += 1  # descarta buscas em andamento
        self._current_query = ""
        if self.search.get_text():
            self.search.set_text("")  # dispara search-changed, que volta ao início
        self._show_home()

    def _rebuild_history(self) -> None:
        child = self.history_box.get_first_child()
        while child:
            self.history_box.remove(child)
            child = self.history_box.get_first_child()

        items = self.history.items()
        if not items:
            return
        group = Adw.PreferencesGroup(title="Buscas recentes")
        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Limpar histórico",
                           css_classes=["flat"], valign=Gtk.Align.CENTER)
        clear.connect("clicked", lambda *_: self._clear_history())
        group.set_header_suffix(clear)
        for query in items[:10]:
            row = Adw.ActionRow(title=query, use_markup=False, activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, q=query: self.search_for(q))
            group.add(row)
        self.history_box.append(group)

    # -------------------------------------------------------------- busca

    def search_for(self, query: str) -> None:
        """Preenche o campo e busca imediatamente."""
        self.search.set_text(query)
        self.search.set_position(-1)
        self._start_search(query)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().strip()
        if not text:
            self._generation += 1
            self._current_query = ""
            self._show_home()
        elif len(text) >= MIN_CHARS and text != self._current_query:
            self._start_search(text)

    def _on_search_activate(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().strip()
        if text and text != self._current_query:
            self._start_search(text)

    def _retry(self) -> None:
        query = self.search.get_text().strip()
        if query:
            self._start_search(query, force=True)

    def _start_search(self, query: str, force: bool = False) -> None:
        if not force and query == self._current_query:
            return
        self._current_query = query
        self._generation += 1
        generation = self._generation

        self.home_button.set_visible(True)
        self.spinner.start()
        # só mostra o "carregando" se a consulta demorar (evita piscar com o cache)
        GLib.timeout_add(180, self._show_loading_if_pending, generation)

        def worker():
            try:
                result, error = self.vocabulary.lookup(query), None
            except Exception as exc:  # noqa: BLE001 - mostrado ao usuário
                result, error = None, exc
            GLib.idle_add(self._on_search_done, generation, result, error)

        threading.Thread(target=worker, daemon=True).start()

    def _show_loading_if_pending(self, generation: int) -> bool:
        if generation == self._generation and self.spinner.get_spinning():
            self.stack.set_visible_child_name("loading")
        return GLib.SOURCE_REMOVE

    def _on_search_done(self, generation, result: SearchResult | None, error) -> bool:
        if generation != self._generation:
            return GLib.SOURCE_REMOVE
        self.spinner.stop()
        if error is not None or result is None:
            self._show_error(error)
        else:
            self.history.add(result.query)
            self._show_result(result)
        return GLib.SOURCE_REMOVE

    def _show_error(self, error) -> None:
        self.banner.set_revealed(False)
        hint = ("Verifique sua conexão. Palavras já consultadas continuam disponíveis "
                "sem internet.")
        if not self.vocabulary.offline.installed:
            hint += " Para consultar qualquer palavra offline, baixe a lista de palavras."
        self.error_page.set_description(f"{error}\n\n{hint}")
        self.error_offline_button.set_visible(not self.vocabulary.offline.installed)
        self.stack.set_visible_child_name("error")

    # ----------------------------------------------------------- resultado

    def _show_result(self, result: SearchResult) -> None:
        if result.source is Source.STALE:
            self._set_banner(
                f"Sem conexão. Mostrando resultado salvo em {_fmt_date(result.saved_at)}.",
                "Tentar de novo", self._retry)
        elif result.source is Source.OFFLINE and result.offline_only:
            self._set_banner("Modo offline: resultado da lista livre (VERO), não do VOLP.",
                             "Consultar o VOLP", self._leave_offline_only)
        elif result.source is Source.OFFLINE:
            self._set_banner("Sem conexão com a ABL. Resultado da lista offline, não do VOLP.",
                             "Tentar de novo", self._retry)
        else:
            self.banner.set_revealed(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content.set_margin_top(18)
        content.set_margin_bottom(24)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.append(self._verdict_card(result))

        if result.exact:
            content.append(self._entries_group("Registro" if len(result.exact) == 1
                                               else "Registros", result.exact, clickable=False))
        if result.variants:
            title = ("Outras grafias parecidas" if result.status is Status.FOUND
                     else "Grafia registrada")
            content.append(self._entries_group(title, result.variants, clickable=False))
        if result.suggestions:
            content.append(self._suggestions_group(result.suggestions))
        if result.related:
            content.append(self._related_group(result))

        self.result_scroller.set_child(Adw.Clamp(maximum_size=640, child=content))
        self.result_scroller.get_vadjustment().set_value(0)
        self.stack.set_visible_child_name("result")

    def _verdict_card(self, result: SearchResult) -> Gtk.Widget:
        icon_name, style, headline = _VERDICT[result.status]
        if result.source is Source.OFFLINE:
            headline = _OFFLINE_HEADLINE[result.status]
        elif result.status is Status.FOUND and result.exact[0].origem in (ORIGIN_FOREIGN,
                                                                           ORIGIN_PLACE):
            headline = f"Registrada como {result.exact[0].origem_label.lower()}"
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       css_classes=["card", "verdict", f"verdict-{result.status.value}"])

        icon = Gtk.Image(icon_name=icon_name, pixel_size=40, css_classes=[style])
        icon.set_margin_top(18)
        card.append(icon)

        word = Gtk.Label(label=result.query, wrap=True, selectable=True,
                         justify=Gtk.Justification.CENTER, css_classes=["title-1"])
        card.append(word)
        card.append(Gtk.Label(label=headline, css_classes=["heading", style]))

        detail = self._verdict_detail(result)
        if detail:
            label = Gtk.Label(label=detail, wrap=True, justify=Gtk.Justification.CENTER,
                              css_classes=["dim-label"])
            label.set_margin_start(12)
            label.set_margin_end(12)
            card.append(label)

        if result.status in (Status.FOUND, Status.VARIANT):
            target = (result.exact or result.variants)[0].termo
            copy = Gtk.Button(label="Copiar grafia", halign=Gtk.Align.CENTER,
                              css_classes=["pill"])
            copy.connect("clicked", lambda *_: self._copy(target))
            copy.set_margin_top(6)
            card.append(copy)

        card.get_last_child().set_margin_bottom(18)
        return card

    @staticmethod
    def _verdict_detail(result: SearchResult) -> str:
        if result.source is Source.OFFLINE:
            if result.status is Status.FOUND:
                return ("A lista inclui plurais, femininos e conjugações. "
                        "Ela não traz a classificação gramatical do VOLP.")
            if result.status is Status.VARIANT:
                forms = ", ".join(f"“{e.termo}”" for e in result.variants)
                return f"A lista registra: {forms}"
        if result.status is Status.FOUND:
            entry = result.exact[0]
            extra = f" ({len(result.exact)} registros)" if len(result.exact) > 1 else ""
            origin = "" if entry.origem == ORIGIN_VOLP else f" · {entry.origem_label}"
            return f"{entry.descricao}{origin}{extra}".strip(" ·")
        if result.status is Status.VARIANT:
            forms = ", ".join(f"“{e.termo}”" for e in result.variants)
            return f"O vocabulário registra: {forms}"
        if result.status is Status.NOT_FOUND:
            if result.suggestions:
                return "Confira a grafia ou veja as sugestões abaixo."
            return "Nenhuma grafia parecida foi encontrada."
        return ""

    def _entry_row(self, entry: Entry, clickable: bool) -> Adw.ActionRow:
        row = Adw.ActionRow(title=entry.termo, subtitle=entry.descricao, use_markup=False)
        legend = explain_description(entry.descricao)
        if legend:
            row.set_tooltip_text(legend)
        if entry.origem in (ORIGIN_FOREIGN, ORIGIN_PLACE):
            row.add_suffix(Gtk.Label(label=entry.origem_label, valign=Gtk.Align.CENTER,
                                     css_classes=["tag", f"tag-{entry.origem}"]))
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copiar",
                          valign=Gtk.Align.CENTER, css_classes=["flat"])
        copy.connect("clicked", lambda *_: self._copy(entry.termo))
        row.add_suffix(copy)
        if clickable:
            row.set_activatable(True)
            row.connect("activated", lambda *_: self.search_for(entry.termo))
        return row

    def _entries_group(self, title: str, entries: list[Entry], clickable: bool):
        group = Adw.PreferencesGroup(title=title)
        for entry in entries:
            group.add(self._entry_row(entry, clickable))
        return group

    def _suggestions_group(self, suggestions: list[str]):
        group = Adw.PreferencesGroup(title="Você quis dizer?")
        for word in suggestions:
            row = Adw.ActionRow(title=word, use_markup=False, activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, w=word: self.search_for(w))
            group.add(row)
        return group

    def _related_group(self, result: SearchResult):
        group = Adw.PreferencesGroup()
        count = len(result.related)
        if result.source is Source.OFFLINE:
            capped = " (primeiras 100)" if count >= 100 else ""
            title = f"Palavras que começam com “{result.query}”"
        else:
            total = count + len(result.exact) + len(result.variants)
            capped = " (limite do site)" if total >= 100 else ""
            title = f"Verbetes que começam com “{result.query}”"
        expander = Adw.ExpanderRow(
            title=title,
            subtitle=f"{count} resultado{'s' if count != 1 else ''}{capped}",
            use_markup=False,
        )
        expander.set_expanded(result.status is Status.NOT_FOUND and not result.suggestions)
        for entry in result.related:
            expander.add_row(self._entry_row(entry, clickable=True))
        group.add(expander)
        return group

    # ------------------------------------------------------------ offline

    def _set_banner(self, title: str, button: str | None, action) -> None:
        self.banner.set_title(title)
        self.banner.set_button_label(button)
        self._banner_action = action
        self.banner.set_revealed(True)

    def _open_offline(self) -> None:
        OfflineDialog(self, self.offline_manager, self.vocabulary.settings,
                      self._on_offline_changed).present()

    def _on_offline_changed(self) -> None:
        self._update_offline_state()
        if self._current_query:
            self._retry()

    def _on_offline_event(self, event: str, value) -> None:
        if event == "finished":
            self._update_offline_state()
            # avisa também quando o diálogo já foi fechado
            if not any(isinstance(w, OfflineDialog) for w in Gtk.Window.list_toplevels()):
                self.toasts.add_toast(Adw.Toast(title="Lista offline pronta", timeout=3))
        elif event == "failed" and not any(
                isinstance(w, OfflineDialog) for w in Gtk.Window.list_toplevels()):
            self.toasts.add_toast(Adw.Toast(title=f"Falha ao baixar a lista: {value}",
                                            timeout=6))

    def _leave_offline_only(self) -> None:
        self.vocabulary.settings.offline_only = False
        self._on_offline_changed()

    def _update_offline_state(self) -> None:
        only = self.vocabulary.settings.offline_only and self.vocabulary.offline.installed
        self.offline_indicator.set_visible(only)
        if only:
            self.home_page.set_description(
                "Modo offline: as consultas usam a lista de palavras livre (VERO), "
                "não o VOLP. Desative em Menu → Uso offline.")
        else:
            self.home_page.set_description(
                "Confira a grafia oficial das palavras no Vocabulário Ortográfico "
                "da Língua Portuguesa (VOLP), da Academia Brasileira de Letras.")

    # ------------------------------------------------------------ ações

    def _copy(self, text: str) -> None:
        clipboard = self.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
        self.toasts.add_toast(Adw.Toast(title=f"“{text}” copiado", timeout=2))

    def _focus_search(self) -> None:
        self.search.grab_focus()
        self.search.select_region(0, -1)

    def _open_site(self) -> None:
        Gtk.UriLauncher(uri=PAGE_URL).launch(self, None, None, None)

    def _clear_history(self) -> None:
        self.history.clear()
        self._rebuild_history()
        self.toasts.add_toast(Adw.Toast(title="Histórico apagado", timeout=2))

    def _clear_cache(self) -> None:
        count = self.vocabulary.cache.count()
        self.vocabulary.cache.clear()
        self.toasts.add_toast(Adw.Toast(
            title=f"Cache limpo ({count} consulta{'s' if count != 1 else ''})", timeout=2))

    def _show_shortcuts(self) -> None:
        body = (
            "Ctrl+F ou Ctrl+L — ir para a busca\n"
            "Enter — buscar agora\n"
            "Esc — limpar e voltar ao início\n"
            "Ctrl+Q — sair"
        )
        if hasattr(Adw, "AlertDialog"):  # libadwaita >= 1.5
            dialog = Adw.AlertDialog(heading="Atalhos do teclado", body=body)
            dialog.add_response("ok", "OK")
            dialog.present(self)
        else:
            dialog = Adw.MessageDialog(transient_for=self, heading="Atalhos do teclado",
                                       body=body)
            dialog.add_response("ok", "OK")
            dialog.present()
