"""'Giochi' page: NSP/XCI/NUP files grouped by game, with the XCI, NUP and extraction
actions."""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, FlowLayout, FluentIcon, IconWidget,
                            ImageLabel, InfoBar, InfoBarIcon, InfoBarPosition, PrimaryPushButton,
                            PushButton, ScrollArea, SimpleCardWidget, StrongBodyLabel,
                            SubtitleLabel, TitleLabel, ToolTipFilter, TransparentPushButton,
                            TransparentToolButton, setCustomStyleSheet)

from .. import nup, titles
from ..fs import FormatError
from ..i18n import tr
from ..keys import KeySet, KeysError
from ..meta import tid_str
from ..titles import Game, Title
from .config import cfg
from .widgets import DropZone, StepBadge, elide, fmt_size, secondary


class TitleChip(SimpleCardWidget):
    removeRequested = Signal(str)

    def __init__(self, number: int, title: Title, parent=None):
        super().__init__(parent)
        self.setFixedWidth(250)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 6, 10)
        lay.setSpacing(10)
        lay.addWidget(StepBadge(number, title.kind == "dlc", self), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(StrongBodyLabel(titles.kind_label(title.kind), self))
        if title.kind == "dlc":
            detail = tid_str(title.title_id)
        else:
            detail = titles.version_label(title)
        cap = secondary(CaptionLabel(f"{detail} · {title.container.upper()}", self))
        cap.setToolTip(os.path.basename(title.path))
        cap.installEventFilter(ToolTipFilter(cap))
        col.addWidget(cap)
        lay.addLayout(col, 1)
        btn = TransparentToolButton(FluentIcon.CLOSE, self)
        btn.setFixedSize(24, 24)
        btn.setIconSize(btn.iconSize() * 0.7)
        btn.setToolTip(tr("Rimuovi questo file"))
        btn.installEventFilter(ToolTipFilter(btn))
        btn.clicked.connect(lambda: self.removeRequested.emit(title.path))
        lay.addWidget(btn, 0, Qt.AlignTop)


class GameCard(SimpleCardWidget):
    removeRequested = Signal(list)

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        items = game.all_titles()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 16, 18)
        lay.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(16)
        pix = QPixmap()
        if game.icon and pix.loadFromData(game.icon):
            img = ImageLabel(pix, self)
            img.scaledToHeight(72)
            img.setBorderRadius(6, 6, 6, 6)
            head.addWidget(img)
        else:
            box = IconWidget(FluentIcon.GAME, self)
            box.setFixedSize(48, 48)
            head.addSpacing(12)
            head.addWidget(box)
            head.addSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(SubtitleLabel(game.name, self))
        paths = sorted({t.path for t in items})
        total = sum(os.path.getsize(p) for p in paths)
        col.addWidget(secondary(CaptionLabel(
            tr("{tid} · {files} file · {size}", tid=tid_str(game.title_id), files=len(paths),
               size=fmt_size(total)), self)))
        publisher = next((t.nacp.publisher() for t in items if t.nacp), "")
        if publisher:
            col.addWidget(secondary(CaptionLabel(publisher, self)))
        head.addLayout(col, 1)

        if game.display_version:
            ver = QVBoxLayout()
            ver.setSpacing(0)
            ver.addWidget(secondary(CaptionLabel(tr("Versione"), self)), 0, Qt.AlignRight)
            ver.addWidget(SubtitleLabel(game.display_version.split(" (")[0], self), 0,
                          Qt.AlignRight)
            head.addLayout(ver)
        rm = TransparentToolButton(FluentIcon.DELETE, self)
        rm.setToolTip(tr("Rimuovi questo gioco dall'elenco"))
        rm.installEventFilter(ToolTipFilter(rm))
        rm.clicked.connect(lambda: self.removeRequested.emit(paths))
        head.addWidget(rm, 0, Qt.AlignTop)
        lay.addLayout(head)

        lay.addWidget(secondary(CaptionLabel(tr("Contenuti"), self)))
        steps = QWidget(self)
        flow = FlowLayout(steps, needAni=False)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setHorizontalSpacing(6)
        flow.setVerticalSpacing(8)
        for n, title in enumerate(items, 1):
            chip = TitleChip(n, title, steps)
            chip.removeRequested.connect(lambda p: self.removeRequested.emit([p]))
            flow.addWidget(chip)
        lay.addWidget(steps)

        for text in game.warnings():
            bar = InfoBar(InfoBarIcon.WARNING, tr("Attenzione"), text, Qt.Horizontal,
                          isClosable=False, duration=-1, position=InfoBarPosition.NONE,
                          parent=self)
            lay.addWidget(bar)


class GamesPage(QWidget):
    xciRequested = Signal()
    nupRequested = Signal()
    extractRequested = Signal()
    keysNeeded = Signal()

    def __init__(self, keys: Callable[[], KeySet], parent=None):
        super().__init__(parent)
        self.setObjectName("gamesPage")
        self.setAcceptDrops(True)
        self.keys = keys
        self.titles: dict[str, list[Title]] = {}
        self.cards: list[QWidget] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.enableTransparentBackground()
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view = QWidget()
        view.setObjectName("gamesView")
        view.setStyleSheet("#gamesView{background: transparent}")
        self.scroll.setWidget(view)
        self.body = QVBoxLayout(view)
        self.body.setContentsMargins(36, 24, 36, 24)
        self.body.setSpacing(14)
        self.body.addWidget(TitleLabel(tr("I tuoi giochi"), view))
        self.drop = DropZone(view)
        self.drop.addFilesClicked.connect(self.choose_files)
        self.drop.addFolderClicked.connect(self.choose_folder)
        self.body.addWidget(self.drop)
        self.empty = secondary(BodyLabel(tr(
            "Nessun file aggiunto. Aggiungi insieme gioco base, aggiornamenti e DLC: vengono "
            "raggruppati per gioco. «Crea XCI» li unisce in un unico XCI (o converte un "
            "singolo NSP), «Crea NUP» in un unico NUP con i file già decifrati, «Estrai» salva "
            "ExeFS e RomFS con l'aggiornamento già applicato."), view))
        self.empty.setWordWrap(True)
        self.body.addWidget(self.empty)
        self.cardsLayout = QVBoxLayout()
        self.cardsLayout.setSpacing(12)
        self.body.addLayout(self.cardsLayout)
        self.body.addStretch(1)
        outer.addWidget(self.scroll, 1)

        bar = QFrame(self)
        bar.setObjectName("bottomBar")
        setCustomStyleSheet(
            bar,
            "#bottomBar{background: rgba(255,255,255,0.6); border-top: 1px solid rgba(0,0,0,0.08)}",
            "#bottomBar{background: rgba(255,255,255,0.04); border-top: 1px solid rgba(255,255,255,0.08)}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(36, 12, 36, 14)
        bl.setSpacing(10)
        info = QVBoxLayout()
        info.setSpacing(0)
        self.summary = secondary(CaptionLabel("", bar))
        info.addWidget(self.summary)
        self.destButton = TransparentPushButton(FluentIcon.FOLDER, "", bar)
        self.destButton.setToolTip(tr("Cartella in cui salvare XCI ed estrazioni"))
        self.destButton.installEventFilter(ToolTipFilter(self.destButton))
        self.destButton.clicked.connect(self.choose_output_dir)
        info.addWidget(self.destButton, 0, Qt.AlignLeft)
        bl.addLayout(info, 1)
        self.clearButton = PushButton(FluentIcon.BROOM, tr("Svuota"), bar)
        self.clearButton.clicked.connect(self.clear)
        self.extractButton = PushButton(FluentIcon.FOLDER, tr("Estrai ExeFS/RomFS"), bar)
        self.extractButton.clicked.connect(self.extractRequested)
        self.nupButton = PushButton(FluentIcon.ZIP_FOLDER, tr("Crea NUP"), bar)
        self.nupButton.setToolTip(tr("File unico con i dati già decifrati e compressi, per gli "
                                     "emulatori che supportano il formato NUP"))
        self.nupButton.installEventFilter(ToolTipFilter(self.nupButton))
        self.nupButton.clicked.connect(self.nupRequested)
        self.xciButton = PrimaryPushButton(FluentIcon.SAVE, tr("Crea XCI"), bar)
        self.xciButton.clicked.connect(self.xciRequested)
        bl.addWidget(self.clearButton)
        bl.addWidget(self.extractButton)
        bl.addWidget(self.nupButton)
        bl.addWidget(self.xciButton)
        outer.addWidget(bar)

        cfg.outputDir.valueChanged.connect(lambda _: self.refresh())
        self.refresh()

    # -- data --------------------------------------------------------------------
    def games(self) -> dict[int, Game]:
        return titles.group_titles([t for ts in self.titles.values() for t in ts])

    def add_paths(self, paths) -> None:
        files = titles.collect_files(paths)
        if not files:
            if paths:
                InfoBar.info(tr("Nessun file"), tr("Nessun file NSP, XCI o NUP tra quelli scelti."),
                             duration=4000, position=InfoBarPosition.TOP_RIGHT,
                             parent=self.window())
            return
        try:
            keys = self.keys()
        except KeysError as ex:
            if not all(nup.is_nup(p) for p in files):  # NUP files need no keys
                InfoBar.error(tr("Chiavi mancanti"), str(ex), duration=8000,
                              position=InfoBarPosition.TOP_RIGHT, parent=self.window())
                self.keysNeeded.emit()
                return
            keys = None
        errors = []
        for path in files:
            path = os.path.abspath(path)
            if path in self.titles:
                continue
            try:
                self.titles[path] = titles.read_titles(path, keys)
            except (FormatError, KeysError, OSError) as ex:
                errors.append(f"{os.path.basename(path)}: {ex}")
                continue
            cfg.set(cfg.lastDir, os.path.dirname(path))
        if errors:
            InfoBar.error(tr("Alcuni file non sono stati aggiunti"), "\n".join(errors),
                          duration=8000, position=InfoBarPosition.TOP_RIGHT, parent=self.window())
        self.refresh()

    def remove_paths(self, paths) -> None:
        for p in paths:
            self.titles.pop(p, None)
        self.refresh()

    def clear(self) -> None:
        self.titles.clear()
        self.refresh()

    def refresh(self) -> None:
        for c in self.cards:
            self.cardsLayout.removeWidget(c)
            c.deleteLater()
        self.cards = []
        games = self.games()
        for game in sorted(games.values(), key=lambda g: g.name.lower()):
            card = GameCard(game, self)
            card.removeRequested.connect(self.remove_paths)
            self.cardsLayout.addWidget(card)
            self.cards.append(card)
        self.empty.setVisible(not games)
        n_files = len(self.titles)
        total = sum(os.path.getsize(p) for p in self.titles if os.path.exists(p))
        self.summary.setText(
            tr("{games} giochi · {files} file · {size}", games=len(games), files=n_files,
               size=fmt_size(total)) if n_files else tr("Nessun file"))
        self._show_dest()
        for b in (self.xciButton, self.nupButton, self.extractButton, self.clearButton):
            b.setEnabled(bool(n_files))

    def _show_dest(self) -> None:
        """Output folder, shortened to the room left by the buttons."""
        out = cfg.get(cfg.outputDir)
        room = max(120, min(480, self.width() - 700))
        self.destButton.setText(elide(self.destButton, out, room) if out
                                else tr("Scegli la cartella di destinazione…"))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._show_dest()

    # -- dialogs -------------------------------------------------------------------
    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Seleziona i file NSP/XCI/NUP"), cfg.get(cfg.lastDir),
            tr("File Switch (*.nsp *.xci *.nup);;Tutti i file (*)"))
        if paths:
            self.add_paths(paths)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Cartella con file NSP/XCI/NUP"),
                                                  cfg.get(cfg.lastDir))
        if folder:
            self.add_paths([folder])

    def choose_output_dir(self) -> bool:
        folder = QFileDialog.getExistingDirectory(self, tr("Cartella di destinazione"),
                                                  cfg.get(cfg.outputDir))
        if folder:
            cfg.set(cfg.outputDir, os.path.normpath(folder))
            return True
        return False

    # -- drag & drop -------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.drop.isEnabled():
            e.acceptProposedAction()
            self.drop.set_highlight(True)

    def dragLeaveEvent(self, e):
        self.drop.set_highlight(False)

    def dropEvent(self, e):
        self.drop.set_highlight(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            e.acceptProposedAction()
