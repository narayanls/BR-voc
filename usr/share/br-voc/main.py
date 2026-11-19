#!/usr/bin/env python3
"""BR-voc: consulta ao Vocabulário Ortográfico da Língua Portuguesa (VOLP)."""

import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from core import APP_ID, APP_NAME, updater  # noqa: E402
from core.cache import Cache  # noqa: E402
from core.history import History  # noqa: E402
from core.offline import OfflineList  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.vocabulary import Vocabulary  # noqa: E402
from ui.window import BrVocWindow  # noqa: E402

CSS = """
.verdict { padding: 0 12px; }
.verdict-found      { background: alpha(@success_color, 0.08); }
.verdict-variant    { background: alpha(@warning_color, 0.10); }
.verdict-not_found  { background: alpha(@error_color, 0.08); }
.tag {
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 0.8em;
  font-weight: bold;
  background: alpha(@accent_bg_color, 0.15);
  color: @accent_color;
}
.tag-toponimo { background: alpha(@success_color, 0.15); color: @success_color; }
"""


class BrVocApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.vocabulary = None
        self.history = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)

        provider = Gtk.CssProvider()
        try:
            provider.load_from_string(CSS)  # GTK >= 4.12
        except AttributeError:
            provider.load_from_data(CSS, -1)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Em desenvolvimento (rodando do repositório), acha o ícone em ../icons
        icons = os.path.join(APP_DIR, "..", "icons")
        if os.path.isdir(icons):
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(icons)

        self.vocabulary = Vocabulary(Cache(), OfflineList(), Settings())
        self.history = History()

        for name, callback, accels in (
            ("about", self._on_about, []),
            ("quit", lambda *_: self.quit(), ["<Control>q"]),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.set_accels_for_action("win.focus-search", ["<Control>f", "<Control>l"])

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            window = BrVocWindow(self, self.vocabulary, self.history)
        window.present()
        window.search.grab_focus()

    def _on_about(self, *_args):
        legal = (
            "Os verbetes exibidos vêm da busca pública do Vocabulário Ortográfico da "
            "Língua Portuguesa, mantida pela Academia Brasileira de Letras "
            "(academia.org.br). O BR-voc não tem vínculo com a ABL.\n\n"
            "A lista de palavras para uso offline é a do VERO, verificador "
            "ortográfico pt-BR do LibreOffice, distribuída sob LGPLv3 / MPL. "
            "Ela não é o VOLP."
        )
        props = dict(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=updater.display_version(updater.installed_version()),
            developer_name="narayanls",
            comments="Consulte a grafia oficial das palavras no VOLP.",
            license_type=Gtk.License.GPL_3_0,
        )
        if hasattr(Adw, "AboutDialog"):  # libadwaita >= 1.5
            dialog = Adw.AboutDialog(**props)
            dialog.add_legal_section("Dados do VOLP", None, Gtk.License.CUSTOM, legal)
            dialog.present(self.get_active_window())
        else:
            dialog = Adw.AboutWindow(transient_for=self.get_active_window(), **props)
            dialog.add_legal_section("Dados do VOLP", None, Gtk.License.CUSTOM, legal)
            dialog.present()


def main() -> int:
    return BrVocApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
