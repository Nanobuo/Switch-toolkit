"""'Avanzamento' page: the job queue, and the background worker that runs one job.

Jobs can be added while another one runs: they wait in the queue, can be moved or removed,
and start one after the other. A failed or cancelled job does not stop the queue.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon, IconWidget, InfoBar,
                            InfoBarIcon, InfoBarPosition, LargeTitleLabel, ProgressBar,
                            ProgressRing, PushButton, ScrollArea, SimpleCardWidget, TitleLabel,
                            ToolTipFilter, TransparentToolButton)

from ..fs import Cancelled, FormatError
from ..i18n import tr
from ..jobs import Job
from ..keys import KeySet, KeysError
from .widgets import elastic, fmt_size, secondary

WAITING, RUNNING, DONE, FAILED, CANCELLED = "waiting", "running", "done", "failed", "cancelled"


class JobWorker(QThread):
    logged = Signal(str)
    progressed = Signal(object, object, str)  # done bytes, total bytes, file
    succeeded = Signal()
    failed = Signal(str, bool)                 # message, cancelled by the user

    def __init__(self, job: Job, keys: Optional[KeySet], parent=None):
        super().__init__(parent)
        self.job = job
        self.keys = keys
        self.cancel_event = threading.Event()
        self._last_emit = 0.0

    def cancel(self) -> None:
        self.cancel_event.set()

    def _progress(self, done, total, name) -> None:
        now = time.monotonic()
        if name and now - self._last_emit < 0.1:
            return
        self._last_emit = now
        self.progressed.emit(done, total, name)

    def run(self) -> None:
        job = self.job
        prefix = {"xci": "XCI: ", "nup": "NUP: "}.get(job.kind, tr("Estrazione: "))
        self.logged.emit(prefix + job.target)
        try:
            job.run(self.keys, self._progress, self.cancel_event.is_set, self.logged.emit)
            self.succeeded.emit()
        except Cancelled:
            self.failed.emit(tr("Operazione annullata."), True)
        except (FormatError, KeysError, OSError) as ex:
            self.failed.emit(str(ex), False)
        except Exception as ex:  # keep the UI alive and tell the user
            self.failed.emit(tr("Errore inatteso: {error}", error=repr(ex)), False)


class QueueRow(SimpleCardWidget):
    removeRequested = Signal(object)
    moveRequested = Signal(object, int)

    def __init__(self, job: Job, keys: Optional[KeySet], parent=None):
        super().__init__(parent)
        self.job = job
        self.keys = keys
        self.state = WAITING
        self.message = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 10, 10)
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
        kind = {"xci": tr("Crea XCI"), "nup": tr("Crea NUP")}.get(job.kind,
                                                                 tr("Estrai ExeFS/RomFS"))
        col.addWidget(BodyLabel(f"{kind} · {job.label}", self))
        sources = sorted({os.path.basename(t.path) for t in job.titles}) if job.titles else \
            sorted({os.path.basename(t.path) for t in job.game.all_titles()})
        col.addWidget(elastic(secondary(CaptionLabel(
            f"{', '.join(sources)} · {fmt_size(job.weight)} → {job.target}", self)), job.target))
        lay.addLayout(col, 1)
        self.status = secondary(BodyLabel(tr("In attesa"), self))
        lay.addWidget(self.status)
        self.buttons = []
        actions = ((FluentIcon.UP, tr("Sposta su"), lambda: self.moveRequested.emit(self, -1)),
                   (FluentIcon.DOWN, tr("Sposta giù"), lambda: self.moveRequested.emit(self, 1)),
                   (FluentIcon.CLOSE, tr("Togli dalla coda"),
                    lambda: self.removeRequested.emit(self)))
        for icon, tip, slot in actions:
            b = TransparentToolButton(icon, self)
            b.setFixedSize(28, 28)
            b.setToolTip(tip)
            b.installEventFilter(ToolTipFilter(b))
            b.clicked.connect(slot)
            lay.addWidget(b)
            self.buttons.append(b)

    def _finished(self, icon, text: str) -> None:
        self.ring.hide()
        self.icon.setIcon(icon)
        self.icon.show()
        self.status.setText(text)
        for b in self.buttons:
            b.hide()

    def set_running(self, frac: float) -> None:
        self.state = RUNNING
        for b in self.buttons:
            b.hide()
        self.icon.hide()
        self.ring.show()
        self.ring.setValue(int(frac * 100))
        self.status.setText(f"{int(frac * 100)}%")

    def set_done(self) -> None:
        self.state = DONE
        self._finished(InfoBarIcon.SUCCESS, tr("Completato"))

    def set_failed(self, message: str, cancelled: bool) -> None:
        self.state = CANCELLED if cancelled else FAILED
        self.message = message
        self._finished(InfoBarIcon.WARNING if cancelled else InfoBarIcon.ERROR,
                       tr("Annullato") if cancelled else tr("Errore"))
        if not cancelled:
            self.status.setToolTip(message)
            self.status.installEventFilter(ToolTipFilter(self.status))

    def set_movable(self, up: bool, down: bool) -> None:
        if self.state == WAITING:
            self.buttons[0].setEnabled(up)
            self.buttons[1].setEnabled(down)


class ProgressPage(QWidget):
    cancelRequested = Signal()
    fractionChanged = Signal(float)
    queueChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("progressPage")
        self.rows: list[QueueRow] = []
        self.current: Optional[QueueRow] = None
        self.result_bar = None
        self.out_dir = ""
        self.started = time.monotonic()
        self.written = 0
        self.item_bytes = 0

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
                                               "«Crea XCI», «Crea NUP» o «Estrai ExeFS/RomFS». "
                                               "Puoi aggiungere altre operazioni anche mentre "
                                               "una è in corso: si mettono in coda."), view))
        self.subtitle.setWordWrap(True)
        head.addWidget(self.title)
        head.addWidget(self.subtitle)
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
        self.clearButton = PushButton(FluentIcon.BROOM, tr("Svuota coda"), self.card)
        self.clearButton.setToolTip(tr("Togli le operazioni in attesa"))
        self.clearButton.installEventFilter(ToolTipFilter(self.clearButton))
        self.clearButton.clicked.connect(self.clear_waiting)
        top.addWidget(self.clearButton, 0, Qt.AlignVCenter)
        self.cancelButton = PushButton(FluentIcon.CLOSE, tr("Annulla"), self.card)
        self.cancelButton.setToolTip(tr("Interrompe l'operazione in corso; la coda prosegue"))
        self.cancelButton.installEventFilter(ToolTipFilter(self.cancelButton))
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

    # -- queue -----------------------------------------------------------------------
    def waiting(self) -> list[QueueRow]:
        return [r for r in self.rows if r.state == WAITING]

    def busy(self) -> bool:
        return self.current is not None or bool(self.waiting())

    def queued_targets(self) -> set[str]:
        return {os.path.normcase(r.job.target) for r in self.rows if r.state in (WAITING, RUNNING)}

    def enqueue(self, jobs: list[Job], keys: Optional[KeySet], out_dir: str) -> None:
        if not self.busy():  # a new run: forget the previous one
            for r in self.rows:
                self.queue.removeWidget(r)
                r.deleteLater()
            self.rows = []
            self.written = 0
            self.item_bytes = 0
            self.started = time.monotonic()
            self.stats.setText(tr("Preparazione…"))
            self.fileLabel.setText("")
            self._set_fraction(0.0)
        if self.result_bar:
            self.result_bar.deleteLater()
            self.result_bar = None
        for job in jobs:
            row = QueueRow(job, keys, self)
            row.removeRequested.connect(self.remove)
            row.moveRequested.connect(self.move)
            self.queue.addWidget(row)
            self.rows.append(row)
        self.out_dir = out_dir
        self.card.show()
        self.cancelButton.show()
        self.clearButton.show()
        self._refresh()

    def take_next(self) -> Optional[QueueRow]:
        nxt = next(iter(self.waiting()), None)
        self.current = nxt
        if nxt is not None:
            self.item_bytes = 0
            nxt.set_running(0.0)
            self.cancelButton.setEnabled(True)
            self.stats.setText(tr("Preparazione…"))
            self.fileLabel.setText("")
        self._refresh()
        return nxt

    def remove(self, row: QueueRow) -> None:
        if row.state != WAITING:
            return
        self.rows.remove(row)
        self.queue.removeWidget(row)
        row.deleteLater()
        self._refresh()

    def move(self, row: QueueRow, step: int) -> None:
        if row.state != WAITING:
            return
        i = self.rows.index(row)
        j = i + step
        if not (0 <= j < len(self.rows)) or self.rows[j].state != WAITING:
            return
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        self.queue.removeWidget(row)
        self.queue.insertWidget(j, row)
        self._refresh()

    def clear_waiting(self) -> None:
        for row in self.waiting():
            self.remove(row)

    def _refresh(self) -> None:
        waiting = self.waiting()
        for k, row in enumerate(waiting):
            row.set_movable(k > 0, k < len(waiting) - 1)
        self.clearButton.setEnabled(bool(waiting))
        if self.current is not None:
            text = (tr("Operazione in corso") if not waiting else
                    tr("Operazione in corso · {n} in coda", n=len(waiting)))
            self.title.setText(text)
            self.subtitle.setText(tr("Destinazione: {dest}", dest=self.out_dir))
        self._update()
        self.queueChanged.emit()

    # -- progress --------------------------------------------------------------------
    def _set_fraction(self, frac: float) -> None:
        self.percent.setText(f"{int(frac * 100)}%")
        self.bar.setValue(int(frac * 1000))
        self.fractionChanged.emit(frac)

    def on_progress(self, done, total, name: str) -> None:
        if self.current is None:
            return
        frac_item = done / total if total else 1.0
        self.item_bytes = done
        self.current.set_running(frac_item)
        if name:
            self.fileLabel.setText(tr("Sto scrivendo: {name}", name=name))
        self._update(frac_item)

    def _update(self, frac_item: float = 0.0) -> None:
        weights = {r: max(r.job.weight, 1) for r in self.rows}
        total_w = sum(weights.values())
        if not total_w:
            return
        done_w = sum(w for r, w in weights.items() if r.state in (DONE, FAILED, CANCELLED))
        if self.current is not None:
            done_w += weights[self.current] * frac_item
        frac = done_w / total_w
        self._set_fraction(frac)
        elapsed = time.monotonic() - self.started
        written = self.written + self.item_bytes
        parts = [tr("{size} scritti", size=fmt_size(written))]
        if elapsed > 1:
            parts.append(f"{fmt_size(written / elapsed)}/s")
        if 0.02 < frac < 1 and elapsed > 3:
            left = elapsed * (1 - frac) / frac
            parts.append(tr("circa {min} min {sec} s rimanenti", min=int(left // 60),
                            sec=int(left % 60)) if left >= 60
                         else tr("circa {sec} s rimanenti", sec=int(left)))
        if self.current is not None:
            self.stats.setText(" · ".join(parts))

    def job_finished(self, ok: bool, message: str = "", cancelled: bool = False) -> None:
        row, self.current = self.current, None
        if row is None:
            return
        self.written += self.item_bytes
        self.item_bytes = 0
        if ok:
            row.set_done()
        else:
            row.set_failed(message, cancelled)
        self._refresh()

    def finish(self) -> tuple[int, int]:
        """The queue is empty: show the outcome. Returns (completed, failed)."""
        self.cancelButton.hide()
        self.clearButton.hide()
        self.fileLabel.setText("")
        ok = [r for r in self.rows if r.state == DONE]
        bad = [r for r in self.rows if r.state == FAILED]
        stopped = [r for r in self.rows if r.state == CANCELLED]
        self._set_fraction(1.0 if self.rows else 0.0)
        self.stats.setText(tr("Tempo impiegato: {sec} s", sec=int(time.monotonic() - self.started)))
        if bad or stopped:
            self.title.setText(tr("Coda terminata: {ok} completate, {bad} non riuscite",
                                  ok=len(ok), bad=len(bad) + len(stopped)))
            messages = [f"{r.job.label}: {r.message}" for r in bad + stopped]
            bar = InfoBar(InfoBarIcon.ERROR if bad else InfoBarIcon.WARNING,
                          tr("Errore") if bad else tr("Annullato"), "\n".join(messages),
                          Qt.Horizontal, isClosable=False, duration=-1,
                          position=InfoBarPosition.NONE, parent=self)
        else:
            self.title.setText(tr("Operazione completata"))
            kinds = {r.job.kind for r in ok}
            text = {"xci": tr("Gli XCI si aprono con gli emulatori o si installano sulla console "
                              "con un installer di homebrew."),
                    "nup": tr("I NUP si aprono con gli emulatori che supportano il formato.")}.get(
                next(iter(kinds)) if len(kinds) == 1 else "",
                tr("ExeFS e RomFS sono pronte: usale per mod e traduzioni (LayeredFS).")
                if kinds == {"extract"} else tr("Tutte le operazioni sono state completate."))
            bar = InfoBar(InfoBarIcon.SUCCESS, tr("Fatto"), text, Qt.Horizontal,
                          isClosable=False, duration=-1, position=InfoBarPosition.NONE,
                          parent=self)
        if ok:
            btn = PushButton(FluentIcon.FOLDER, tr("Apri cartella"), bar)
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir)))
            bar.addWidget(btn)
        self.result_bar = bar
        self.resultLayout.addWidget(bar)
        return len(ok), len(bad) + len(stopped)
