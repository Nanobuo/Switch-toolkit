"""Smoke test of the Fluent GUI (skipped when PySide6 is not installed)."""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["SWITCHTK_LANG"] = "it"

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    import qfluentwidgets  # noqa: F401
except Exception as ex:  # pragma: no cover - depends on the environment
    QApplication = None
    SKIP_REASON = f"GUI non disponibile: {ex}"

import fake_switch as fk  # noqa: E402

TID = 0x0100ABCD00020000


@unittest.skipIf(QApplication is None, globals().get("SKIP_REASON", ""))
class GuiSmoke(unittest.TestCase):
    def run_jobs(self, app, w, kind):
        w.start(kind)
        loop = QEventLoop()
        w.worker.finished.connect(loop.quit)
        QTimer.singleShot(30000, loop.quit)
        if w.worker.isRunning():
            loop.exec()
        app.processEvents()

    def test_add_convert_extract(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["APPDATA"] = os.path.join(d, "appdata")
            app = QApplication.instance() or QApplication([])
            from qfluentwidgets import Theme, setTheme
            from switchtk.gui.config import cfg, load_config
            load_config()
            cfg.set(cfg.checkUpdates, False)
            cfg.set(cfg.outputDir, os.path.join(d, "out"))
            raw = fk.make_keys(os.path.join(d, "prod.keys"))
            cfg.set(cfg.keysFile, os.path.join(d, "prod.keys"))
            files, info = fk.build_title(raw, "game", TID, 0, name="Gui Test",
                                         display_version="1.0", romfs={"a.txt": b"a" * 40000})
            fk.write_nsp(os.path.join(d, "base.nsp"), files)
            files, _ = fk.build_title(raw, "update", TID | 0x800, 65536, name="Gui Test",
                                      display_version="1.1", romfs={"a.txt": b"b" * 40000},
                                      base_romfs_section=info["romfs_section"])
            fk.write_nsp(os.path.join(d, "upd.nsp"), files)

            from switchtk.gui.window import MainWindow
            w = MainWindow()
            w.show()
            w.gamesPage.add_paths([d])
            self.assertEqual(len(w.gamesPage.cards), 1)
            self.assertEqual(len(w.gamesPage.titles), 2)
            cfg.set(cfg.themeMode, Theme.DARK)
            setTheme(Theme.DARK)

            self.run_jobs(app, w, "xci")
            self.assertEqual(w.progressPage.title.text(), "Operazione completata")
            out = pathlib.Path(d, "out")
            (xci_file,) = out.glob("*.xci")
            self.assertEqual(xci_file.name, "Gui Test [0100ABCD00020000][v65536].xci")

            self.run_jobs(app, w, "extract")
            self.assertEqual(w.progressPage.title.text(), "Operazione completata")
            game = out / "Gui Test [0100ABCD00020000]"
            self.assertEqual((game / "romfs" / "a.txt").read_bytes(), b"b" * 40000)
            self.assertTrue((game / "exefs" / "main.npdm").exists())

            self.run_jobs(app, w, "nup")
            self.assertEqual(w.progressPage.title.text(), "Operazione completata")
            (nup_file,) = out.glob("*.nup")
            self.assertEqual(nup_file.name, "Gui Test [0100ABCD00020000][v65536].nup")
            w.gamesPage.clear()
            w.gamesPage.add_paths([str(nup_file)])
            (card_titles,) = w.gamesPage.games().values()
            self.assertEqual(card_titles.name, "Gui Test")
            w.close()
            w.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
