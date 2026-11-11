"""Smoke test of the Fluent GUI (skipped when PySide6 is not installed)."""

import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

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
    def wait_idle(self, app, w):
        """Run the event loop until the whole queue is done."""
        loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if not w.progressPage.busy() else None)
        poll.start(20)
        QTimer.singleShot(60000, loop.quit)
        loop.exec()
        poll.stop()
        app.processEvents()

    def test_queue_convert_extract(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["APPDATA"] = os.path.join(d, "appdata")
            app = QApplication.instance() or QApplication([])
            from qfluentwidgets import Theme, setTheme
            from switchtk.gui.config import cfg, load_config
            load_config()
            cfg.set(cfg.checkUpdates, False)
            out = pathlib.Path(d, "out")
            cfg.set(cfg.outputDir, str(out))
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
            page = w.progressPage

            # Three requests while the first one runs: two wait in the queue.
            w.start("xci")
            w.start("extract")
            w.start("nup")
            w.start("nup")  # already queued: not added twice
            w.gamesPage.archiveRequested.emit("nx")
            self.assertEqual([r.job.kind for r in page.waiting()], ["extract", "nup", "nx"])
            self.assertTrue(w.gamesPage.xciButton.isEnabled())  # the page stays usable
            page.move(page.waiting()[1], -1)
            self.assertEqual([r.job.kind for r in page.waiting()], ["nup", "extract", "nx"])
            self.wait_idle(app, w)
            self.assertEqual(page.title.text(), "Operazione completata")
            self.assertEqual([r.state for r in page.rows], ["done"] * 4)
            (nx_file,) = out.glob("*.nx")
            w.gamesPage.add_paths([str(nx_file)])  # opens without keys or errors
            (xci_file,) = out.glob("*.xci")
            self.assertEqual(xci_file.name, "Gui Test [0100ABCD00020000][v65536].xci")
            game = out / "Gui Test [0100ABCD00020000]"
            self.assertEqual((game / "romfs" / "a.txt").read_bytes(), b"b" * 40000)
            self.assertTrue((game / "exefs" / "main.npdm").exists())
            (nup_file,) = out.glob("*.nup")
            self.assertEqual(nup_file.name, "Gui Test [0100ABCD00020000][v65536].nup")

            # Removing a waiting job; cancelling the running one does not stop the queue.
            out2 = pathlib.Path(d, "out2")
            cfg.set(cfg.outputDir, str(out2))
            from switchtk.fs import Cancelled
            from switchtk.jobs import Job
            real_run = Job.run

            def stuck_extraction(job, keys, progress, should_cancel, log):
                if job.kind != "extract":
                    return real_run(job, keys, progress, should_cancel, log)
                while not should_cancel():  # runs until the user cancels it
                    time.sleep(0.01)
                raise Cancelled()

            with mock.patch.object(Job, "run", stuck_extraction):
                w.start("extract")
                w.start("xci")
                w.start("nup")
                self.assertEqual(len(page.rows), 3)  # a new run: the old rows are gone
                page.remove(page.waiting()[1])
                w.cancel()
                self.wait_idle(app, w)
            self.assertEqual([r.state for r in page.rows], ["cancelled", "done"])
            self.assertEqual(page.title.text(), "Coda terminata: 1 completate, 1 non riuscite")
            self.assertEqual([p.suffix for p in out2.glob("*.*")], [".xci"])

            w.gamesPage.clear()
            w.gamesPage.add_paths([str(nup_file)])
            (card_titles,) = w.gamesPage.games().values()
            self.assertEqual(card_titles.name, "Gui Test")
            w.close()
            w.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
