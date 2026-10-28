"""'Avanzamento' page and the background worker that runs the jobs."""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon, IconWidget, InfoBar,
                            InfoBarIcon, InfoBarPosition, LargeTitleLabel, ProgressBar,
                            ProgressRing, PushButton, ScrollArea, SimpleCardWidget, TitleLabel)

from ..fs import Cancelled, FormatError
from ..i18n import tr
from ..jobs import Job
from ..keys import KeySet, KeysError
from .widgets import elastic, fmt_size, secondary


class JobWorker(QThread):
    logged = Signal(str)
    progressed = Signal(int, object, object, str)  # index, done bytes, total bytes, file
    itemStatus = Signal(int, str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, jobs: list[Job], keys: KeySet, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.keys = keys
        self.cancel_event = threading.Event()
        self._last_emit = 0.0

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            for idx, job in enumerate(self.jobs):
                self.itemStatus.emit(idx, "start")
                self.logged.emit(("XCI: " if job.kind == "xci" else tr("Estrazione: "))
                                 + job.target)

                def progress(done, total, name, idx=idx):
                    now = time.monotonic()
                    if name and now - self._last_emit < 0.1:
                        return
                    self._last_emit = now
                    self.progressed.emit(idx, done, total, name)

                job.run(self.keys, progress, self.cancel_event.is_set, self.logged.emit)
                self.itemStatus.emit(idx, "done")
            self.succeeded.emit()
        except Cancelled:
            self.failed.emit(tr("Operazione annullata."))
        except (FormatError, KeysError, OSError) as ex:
            self.failed.emit(str(ex))
        except Exception as ex:  # keep the UI alive and tell the user
            self.failed.emit(tr("Errore inatteso: {error}", error=repr(ex)))


class QueueRow(SimpleCardWidget):
    def __init__(self, job: Job, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(14)
        self.ring = ProgressRing(self)
        self.ring.setFixedSize(22, 22)
        self.ring.setStrokeWidth(3)
        self.ring.setTextVisible(False)
        self.icon = IconWidget(FluentIcon.STOP_WATCH, self)
        self.icon.setFixedSize(20, 20)
        self.ring.hide()
        lay.addWidget(self.ring)
        lay.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        kind = tr("Crea XCI") if job.kind == "xci" else tr("Estrai ExeFS/RomFS")
        col.addWidget(BodyLabel(f"{kind} · {job.label}", self))
        sources = sorted({os.path.basename(t.path) for t in job.titles}) if job.titles else \
            sorted({os.path.basename(t.path) for t in job.game.all_titles()})
        col.addWidget(elastic(secondary(CaptionLabel(
            f"{', '.join(sources)} · {fmt_size(job.weight)} → {job.target}", self)), job.target))
        lay.addLayout(col, 1)
        self.state = secondary(BodyLabel(tr("In attesa"), self))
        lay.addWidget(self.state)

    def set_running(self, frac: float) -> None:
        self.icon.hide()
        self.ring.show()
        self.ring.setValue(int(frac * 100))
        self.state.setText(f"{int(frac * 100)}%")

    def set_done(self) -> None:
        self.ring.hide()
        self.icon.setIcon(InfoBarIcon.SUCCESS)
        self.icon.show()
        self.state.setText(tr("Completato"))

    def set_failed(self, text: str) -> None:
        self.ring.hide()
        self.icon.setIcon(InfoBarIcon.ERROR)
        self.icon.show()
        self.state.setText(text)


class ProgressPage(QWidget):
    cancelRequested = Signal()
    fractionChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("progressPage")
        self.rows: list[QueueRow] = []
        self.weights: list[int] = []
        self.current = -1
        self.result_bar = None
        self.out_dir = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view = QWidget()
        view.setObjectName("progressView")
        view.setStyleSheet("#progressView{background: transparent}")
        scroll.setWidget(view)
        outer.addWidget(scroll)

        self.body = QVBoxLayout(view)
        self.body.setContentsMargins(36, 24, 36, 24)
        self.body.setSpacing(14)
        head = QVBoxLayout()
        head.setSpacing(2)
        self.title = TitleLabel(tr("Nessuna operazione in corso"), view)
        self.subtitle = secondary(BodyLabel(tr("Aggiungi i file nella pagina Giochi e premi "
                                               "«Crea XCI» o «Estrai ExeFS/RomFS»."), view))
        head.addWidget(self.title)
        head.addWidget(elastic(self.subtitle))
        self.body.addLayout(head)

        self.card = SimpleCardWidget(view)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(14)
        self.percent = LargeTitleLabel("0%", self.card)
        top.addWidget(self.percent)
        self.stats = secondary(BodyLabel("", self.card))
        top.addWidget(self.stats, 1, Qt.AlignVCenter)
        self.cancelButton = PushButton(FluentIcon.CLOSE, tr("Annulla"), self.card)
        self.cancelButton.clicked.connect(self.cancelRequested)
        top.addWidget(self.cancelButton, 0, Qt.AlignVCenter)
        cl.addLayout(top)
        self.bar = ProgressBar(self.card)
        self.bar.setRange(0, 1000)
        cl.addWidget(self.bar)
        self.fileLabel = secondary(CaptionLabel("", self.card))
        cl.addWidget(self.fileLabel)
        self.body.addWidget(self.card)
        self.card.hide()

        self.queue = QVBoxLayout()
        self.queue.setSpacing(4)
        self.body.addLayout(self.queue)
        self.resultLayout = QVBoxLayout()
        self.body.addLayout(self.resultLayout)
        self.body.addStretch(1)

    # -- lifecycle -----------------------------------------------------------------
    def start(self, jobs: list[Job], out_dir: str) -> None:
        for r in self.rows:
            self.queue.removeWidget(r)
            r.deleteLater()
        if self.result_bar:
            self.result_bar.deleteLater()
            self.result_bar = None
        self.rows = []
        for job in jobs:
            row = QueueRow(job, self)
            self.queue.addWidget(row)
            self.rows.append(row)
        self.weights = [max(j.weight, 1) for j in jobs]
        self.kinds = {j.kind for j in jobs}
        self.out_dir = out_dir
        self.current = -1
        self.base_bytes = 0
        self.item_bytes = 0
        self.started = time.monotonic()
        self.title.setText(tr("Creazione XCI in corso") if self.kinds == {"xci"}
                           else tr("Estrazione in corso"))
        self.subtitle.setText(tr("{n} operazioni → {dest}", n=len(jobs), dest=out_dir))
        self.subtitle.setToolTip(self.subtitle.text())
        self.card.show()
        self.cancelButton.setEnabled(True)
        self.cancelButton.show()
        self._set_fraction(0.0)
        self.stats.setText(tr("Preparazione…"))
        self.fileLabel.setText("")

    def _set_fraction(self, frac: float) -> None:
        self.percent.setText(f"{int(frac * 100)}%")
        self.bar.setValue(int(frac * 1000))
        self.fractionChanged.emit(frac)

    def on_status(self, idx: int, state: str) -> None:
        if state == "start":
            self.current = idx
            self.base_bytes += self.item_bytes
            self.item_bytes = 0
            self.rows[idx].set_running(0.0)
        elif state == "done":
            self.rows[idx].set_done()
            self._update(idx, 1.0)

    def on_progress(self, idx: int, done, total, name: str) -> None:
        frac_item = done / total if total else 1.0
        self.item_bytes = done
        self.rows[idx].set_running(frac_item)
        if name:
            self.fileLabel.setText(tr("Sto scrivendo: {name}", name=name))
        self._update(idx, frac_item)

    def _update(self, idx: int, frac_item: float) -> None:
        total_w = sum(self.weights)
        frac = (sum(self.weights[:idx]) + self.weights[idx] * frac_item) / total_w
        self._set_fraction(frac)
        elapsed = time.monotonic() - self.started
        written = self.base_bytes + self.item_bytes
        parts = [tr("{size} scritti", size=fmt_size(written))]
        if elapsed > 1:
            parts.append(f"{fmt_size(written / elapsed)}/s")
        if 0.02 < frac < 1 and elapsed > 3:
            left = elapsed * (1 - frac) / frac
            parts.append(tr("circa {min} min {sec} s rimanenti", min=int(left // 60),
                            sec=int(left % 60)) if left >= 60
                         else tr("circa {sec} s rimanenti", sec=int(left)))
        self.stats.setText(" · ".join(parts))

    def finish(self, ok: bool, message: str = "") -> None:
        self.cancelButton.hide()
        self.fileLabel.setText("")
        if ok:
            self._set_fraction(1.0)
            self.title.setText(tr("Operazione completata"))
            self.stats.setText(tr("Tempo impiegato: {sec} s",
                                   sec=int(time.monotonic() - self.started)))
            text = (tr("Gli XCI si aprono con gli emulatori o si installano sulla console con "
                       "un installer di homebrew.") if self.kinds == {"xci"}
                    else tr("ExeFS e RomFS sono pronte: usale per mod e traduzioni (LayeredFS)."))
            bar = InfoBar(InfoBarIcon.SUCCESS, tr("Fatto"), text, Qt.Horizontal,
                          isClosable=False, duration=-1, position=InfoBarPosition.NONE,
                          parent=self)
            btn = PushButton(FluentIcon.FOLDER, tr("Apri cartella"), bar)
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir)))
            bar.addWidget(btn)
        else:
            self.title.setText(tr("Operazione interrotta"))
            if 0 <= self.current < len(self.rows):
                self.rows[self.current].set_failed(tr("Interrotto"))
            bar = InfoBar(InfoBarIcon.ERROR, tr("Errore"), message, Qt.Horizontal, isClosable=False,
                          duration=-1, position=InfoBarPosition.NONE, parent=self)
        self.result_bar = bar
        self.resultLayout.addWidget(bar)
