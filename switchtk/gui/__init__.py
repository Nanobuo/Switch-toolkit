"""Fluent (Windows 11 style) GUI built with PySide6-Fluent-Widgets."""

import sys

ACCENT = "#E60012"


def main() -> int:
    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, setThemeColor

    from .. import i18n
    from .config import cfg, load_config

    app = QApplication.instance() or QApplication(sys.argv)
    load_config()
    language = cfg.get(cfg.language)
    if language == "auto":
        language = "it" if QLocale.system().name().lower().startswith("it") else "en"
    i18n.set_language(language)
    setThemeColor(ACCENT)
    setTheme(cfg.get(cfg.themeMode))
    cfg.themeChanged.connect(setTheme)

    from .window import MainWindow

    win = MainWindow()
    win.show()
    return app.exec()
