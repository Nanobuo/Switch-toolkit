"""Main window: Fluent navigation with Giochi, Avanzamento, Registro, Impostazioni."""

from __future__ import annotations

import os
import shutil
import sys

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from qfluentwidgets import (FluentIcon, FluentWindow, InfoBar, InfoBarIcon, InfoBarPosition,
                            MessageBox, NavigationItemPosition, PushButton, Theme, isDarkTheme)

from .. import __version__, jobs, updater
from ..i18n import tr
from ..keys import KeySet, KeysError
from .config import cfg
from .games_page import GamesPage
from .progress_page import JobWorker, ProgressPage
from .settings_page import LogPage, SettingsPage
from .update_ui import UpdateCheckWorker, UpdateDialog
from .widgets import fmt_size

APP_NAME = "Switch Toolkit"


def app_icon() -> QIcon:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base = getattr(sys, "_MEIPASS", here)  # PyInstaller unpacks the assets there
    path = os.path.join(base, "assets", "switchtk.png")
    return QIcon(path) if os.path.isfile(path) else FluentIcon.GAME.icon()


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.worker: JobWorker | None = None
        self.tray: QSystemTrayIcon | None = None
        self.updateCheck: UpdateCheckWorker | None = None
        self.updateBar: InfoBar | None = None
        self._keys: KeySet | None = None
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1120, 780)
        self.setMinimumSize(QSize(900, 620))

        self.gamesPage = GamesPage(self.keys, self)
        self.progressPage = ProgressPage(self)
        self.logPage = LogPage(self)
        self.settingsPage = SettingsPage(self)

        self.addSubInterface(self.gamesPage, FluentIcon.GAME, tr("Giochi"))
        self.addSubInterface(self.progressPage, FluentIcon.SPEED_HIGH, tr("Avanzamento"))
        self.addSubInterface(self.logPage, FluentIcon.DOCUMENT, tr("Registro"))
        self.navigationInterface.addItem(
            "themeToggle", FluentIcon.CONSTRACT, tr("Cambia tema"), onClick=self.toggle_theme,
            selectable=False, position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settingsPage, FluentIcon.SETTING, tr("Impostazioni"),
                             NavigationItemPosition.BOTTOM)

        self.gamesPage.xciRequested.connect(lambda: self.start("xci"))
        self.gamesPage.nupRequested.connect(lambda: self.start("nup"))
        self.gamesPage.extractRequested.connect(lambda: self.start("extract"))
        self.gamesPage.keysNeeded.connect(lambda: self.switchTo(self.settingsPage))
        self.progressPage.cancelRequested.connect(self.cancel)
        self.progressPage.fractionChanged.connect(self._show_fraction)
        self.settingsPage.checkUpdatesRequested.connect(lambda: self.check_updates(manual=True))
        cfg.keysFile.valueChanged.connect(self._reset_keys)
        cfg.titleKeysFile.valueChanged.connect(self._reset_keys)
        cfg.appRestartSig.connect(self._restart_needed)
        self.logPage.append(f"Switch Toolkit {__version__}")
        updater.cleanup_previous()
        QTimer.singleShot(300, self._announce_keys)
        if cfg.get(cfg.checkUpdates):
            QTimer.singleShot(2000, lambda: self.check_updates(manual=False))

    # -- keys ------------------------------------------------------------------------
    def keys(self) -> KeySet:
        if self._keys is None:
            self._keys = KeySet.load(cfg.get(cfg.keysFile) or None,
                                     cfg.get(cfg.titleKeysFile) or None)
            self.logPage.append(tr("Chiavi: {path}", path=self._keys.path))
        return self._keys

    def _reset_keys(self, *_args) -> None:
        self._keys = None
        self._announce_keys()

    def _announce_keys(self) -> None:
        try:
            self.keys()
        except KeysError as ex:
            self.logPage.append(str(ex))
            bar = InfoBar(InfoBarIcon.WARNING, tr("Chiavi mancanti"), str(ex),
                          isClosable=True, duration=-1, position=InfoBarPosition.TOP_RIGHT,
                          parent=self.stackedWidget)
            button = PushButton(tr("Scegli prod.keys…"), bar)
            button.clicked.connect(lambda: (bar.close(), self.settingsPage._choose_keys()))
            bar.addWidget(button)
            bar.show()

    # -- updates -----------------------------------------------------------------------
    def check_updates(self, manual: bool) -> None:
        if self.updateCheck is not None and self.updateCheck.isRunning():
            return
        self.settingsPage.set_update_status(tr("Controllo in corso…"))
        self.updateCheck = UpdateCheckWorker(self)
        self.updateCheck.found.connect(self._update_found)
        self.updateCheck.upToDate.connect(lambda rel: self._update_none(rel, manual))
        self.updateCheck.failed.connect(lambda msg: self._update_failed(msg, manual))
        self.updateCheck.start()

    def _update_found(self, release) -> None:
        self.settingsPage.set_update_status(tr(
            "Switch Toolkit {current} · disponibile la versione {version}", current=__version__,
            version=release.version))
        self.logPage.append(tr("Disponibile Switch Toolkit {version}", version=release.version))
        if self.updateBar is not None:
            self.updateBar.close()
        # Built in full before show(): the bar sizes itself when shown.
        self.updateBar = InfoBar(
            InfoBarIcon.INFORMATION, tr("Aggiornamento disponibile"),
            f"Switch Toolkit {release.version}", isClosable=True, duration=-1,
            position=InfoBarPosition.TOP_RIGHT, parent=self.stackedWidget)
        button = PushButton(tr("Dettagli"), self.updateBar)
        button.clicked.connect(lambda: self.show_update(release))
        self.updateBar.addWidget(button)
        self.updateBar.show()

    def _update_none(self, release, manual: bool) -> None:
        self.settingsPage.set_update_status(
            tr("Switch Toolkit {current} · è l'ultima versione", current=__version__))
        if manual:
            InfoBar.success(tr("Nessun aggiornamento"),
                            tr("Stai già usando l'ultima versione ({version}).",
                               version=release.version), duration=4000,
                            position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _update_failed(self, message: str, manual: bool) -> None:
        self.settingsPage.set_update_status(
            tr("Switch Toolkit {current} · controllo non riuscito", current=__version__))
        self.logPage.append(tr("Controllo aggiornamenti: {message}", message=message))
        if manual:
            InfoBar.warning(tr("Controllo non riuscito"), message, duration=6000,
                            position=InfoBarPosition.TOP_RIGHT, parent=self)

    def show_update(self, release) -> None:
        if self.worker and self.worker.isRunning():
            InfoBar.warning(tr("Operazione in corso"), tr("Aggiorna al termine dell'operazione."),
                            duration=4000, position=InfoBarPosition.TOP_RIGHT, parent=self)
            return
        UpdateDialog(release, self).exec()

    def _restart_needed(self) -> None:
        InfoBar.info(tr("Riavvio necessario"),
                     tr("La nuova lingua verrà usata al prossimo avvio."),
                     duration=5000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    # -- theme -----------------------------------------------------------------------
    def toggle_theme(self) -> None:
        cfg.set(cfg.themeMode, Theme.LIGHT if isDarkTheme() else Theme.DARK)

    # -- jobs --------------------------------------------------------------------------
    def start(self, kind: str) -> None:
        if not cfg.get(cfg.outputDir) and not self.gamesPage.choose_output_dir():
            return
        out_dir = os.path.abspath(cfg.get(cfg.outputDir))
        games = list(self.gamesPage.games().values())
        all_nup = all(t.container == "nup" for g in games for t in g.all_titles())
        try:
            keys = self.keys()
        except KeysError as ex:
            if not all_nup:  # NUP files are plain: no keys needed
                InfoBar.error(tr("Chiavi mancanti"), str(ex), duration=8000,
                              position=InfoBarPosition.TOP_RIGHT, parent=self)
                return
            keys = None
        if kind == "xci" and jobs.has_nup(games):
            InfoBar.warning(tr("NUP esclusi"),
                            tr("I file NUP non possono diventare XCI: contengono i dati già "
                               "decifrati, senza gli NCA originali."),
                            duration=7000, position=InfoBarPosition.TOP_RIGHT, parent=self)
        if kind == "xci":
            todo = jobs.xci_jobs(games, out_dir, cfg.get(cfg.mergeXci),
                                 cfg.get(cfg.includeUpdate), cfg.get(cfg.includeDlc))
        elif kind == "nup":
            todo = jobs.nup_jobs(games, out_dir, cfg.get(cfg.mergeXci),
                                 cfg.get(cfg.includeUpdate), cfg.get(cfg.includeDlc))
        else:
            if not (cfg.get(cfg.extractExefs) or cfg.get(cfg.extractRomfs)):
                InfoBar.warning(tr("Niente da estrarre"),
                                tr("Attiva ExeFS o RomFS in Impostazioni → Estrazione."),
                                duration=5000, position=InfoBarPosition.TOP_RIGHT, parent=self)
                return
            todo = jobs.extract_jobs(games, out_dir, cfg.get(cfg.extractExefs),
                                     cfg.get(cfg.extractRomfs), cfg.get(cfg.romfsBin),
                                     cfg.get(cfg.applyUpdate))
        if not todo:
            if kind != "xci":
                InfoBar.warning(tr("Niente da fare"),
                                tr("Nessun gioco base tra i file aggiunti: aggiungilo per "
                                   "estrarre."),
                                duration=5000, position=InfoBarPosition.TOP_RIGHT, parent=self)
            return
        existing = [j.label for j in todo if os.path.exists(j.target)]
        if existing:
            box = MessageBox(tr("Esiste già"),
                             tr("Verranno sovrascritti:\n{names}\n\nContinuare?",
                                names="\n".join(existing)), self)
            box.yesButton.setText(tr("Sovrascrivi"))
            box.cancelButton.setText(tr("Annulla"))
            if not box.exec():
                return
        if kind in ("xci", "nup") and not self._space_ok(todo, out_dir):
            return
        self.worker = JobWorker(todo, keys, self)
        self.worker.logged.connect(self.logPage.append)
        self.worker.progressed.connect(self.progressPage.on_progress)
        self.worker.itemStatus.connect(self.progressPage.on_status)
        self.worker.succeeded.connect(lambda: self._finished(True, ""))
        self.worker.failed.connect(lambda msg: self._finished(False, msg))
        self.progressPage.start(todo, out_dir)
        self.gamesPage.set_busy(True)
        self.switchTo(self.progressPage)
        self.worker.start()

    def _space_ok(self, todo, out_dir: str) -> bool:
        """Warn before starting when the destination disk is too small."""
        os.makedirs(out_dir, exist_ok=True)
        needed = sum(j.weight for j in todo)
        free = shutil.disk_usage(out_dir).free
        if free >= needed:
            return True
        box = MessageBox(tr("Spazio su disco insufficiente"),
                         tr("Servono circa {needed}, liberi {free}. Libera spazio oppure "
                            "scegli un'altra cartella.", needed=fmt_size(needed),
                            free=fmt_size(free)), self)
        box.yesButton.setText(tr("Continua comunque"))
        box.cancelButton.setText(tr("Annulla"))
        return bool(box.exec())

    def _show_fraction(self, frac: float) -> None:
        running = self.worker is not None and self.worker.isRunning()
        self.setWindowTitle(f"{int(frac * 100)}% · {APP_NAME}" if running and frac < 1
                            else APP_NAME)

    def _notify(self, ok: bool, message: str) -> None:
        """Tell the user a job ended while they were doing something else."""
        if self.isActiveWindow():
            return
        QApplication.alert(self)
        if not cfg.get(cfg.notifyDone) or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.tray is None:
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.messageClicked.connect(self._raise)
            self.tray.activated.connect(lambda _reason: self._raise())
        self.tray.show()
        if ok:
            self.tray.showMessage(APP_NAME, tr("Operazione completata."),
                                  QSystemTrayIcon.Information, 8000)
        else:
            self.tray.showMessage(APP_NAME, message, QSystemTrayIcon.Warning, 8000)
        QTimer.singleShot(15000, self.tray.hide)

    def _raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def cancel(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.progressPage.cancelButton.setEnabled(False)
            self.progressPage.stats.setText(tr("Annullamento…"))

    def _finished(self, ok: bool, message: str) -> None:
        self.setWindowTitle(APP_NAME)
        self._notify(ok, message)
        self.progressPage.finish(ok, message)
        self.gamesPage.set_busy(False)
        if not ok:
            self.logPage.append(tr("ERRORE: {message}", message=message))

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            box = MessageBox(tr("Operazione in corso"),
                             tr("Vuoi interrompere l'operazione e uscire?"), self)
            box.yesButton.setText(tr("Interrompi ed esci"))
            box.cancelButton.setText(tr("Continua"))
            if not box.exec():
                e.ignore()
                return
            self.worker.cancel()
            self.worker.wait(10000)
        super().closeEvent(e)
