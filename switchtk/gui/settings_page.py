"""'Impostazioni' and 'Registro' pages."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFileDialog, QVBoxLayout, QWidget
from qfluentwidgets import (FluentIcon, HyperlinkCard, OptionsSettingCard, PlainTextEdit,
                            PushSettingCard, ScrollArea, SettingCardGroup, SwitchSettingCard,
                            TitleLabel)

from .. import __version__, keys
from ..i18n import tr
from .config import cfg

REPO_URL = "https://github.com/Nanobuo/Switch-Toolkit"


class LocalizedSwitchSettingCard(SwitchSettingCard):
    """SwitchSettingCard writes "On"/"Off" on every change; show translated labels instead."""

    def setValue(self, isChecked: bool):
        super().setValue(isChecked)
        self.switchButton.setText(tr("Attivo") if isChecked else tr("Disattivo"))


class SettingsPage(QWidget):
    checkUpdatesRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.enableTransparentBackground()
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view = QWidget()
        view.setObjectName("settingsView")
        view.setStyleSheet("#settingsView{background: transparent}")
        self.scroll.setWidget(view)
        outer.addWidget(self.scroll)

        lay = QVBoxLayout(view)
        lay.setContentsMargins(36, 24, 36, 24)
        lay.setSpacing(24)
        lay.addWidget(TitleLabel(tr("Impostazioni"), view))

        keygroup = SettingCardGroup(tr("Chiavi"), view)
        self.keysCard = PushSettingCard(tr("Scegli file"), FluentIcon.FINGERPRINT, "prod.keys",
                                        "", keygroup)
        self.keysCard.clicked.connect(self._choose_keys)
        self.titleKeysCard = PushSettingCard(tr("Scegli file"), FluentIcon.CERTIFICATE,
                                             tr("title.keys (facoltativo)"), "", keygroup)
        self.titleKeysCard.clicked.connect(self._choose_title_keys)
        keygroup.addSettingCard(self.keysCard)
        keygroup.addSettingCard(self.titleKeysCard)
        lay.addWidget(keygroup)

        paths = SettingCardGroup(tr("Cartelle"), view)
        self.outputCard = PushSettingCard(tr("Scegli cartella"), FluentIcon.FOLDER,
                                          tr("Cartella di destinazione"), "", paths)
        self.outputCard.clicked.connect(self._choose_output)
        paths.addSettingCard(self.outputCard)
        lay.addWidget(paths)

        xcigroup = SettingCardGroup(tr("Creazione XCI e NUP"), view)
        self._switch(xcigroup, cfg.mergeXci, FluentIcon.ALBUM, tr("Unisci in un unico file"),
                     tr("Gioco base, ultimo aggiornamento e DLC dello stesso gioco in un solo "
                        "XCI o NUP. Se disattivato ogni file diventa un XCI o NUP separato."))
        self._switch(xcigroup, cfg.includeUpdate, FluentIcon.SYNC, tr("Includi l'aggiornamento"),
                     tr("Solo il più recente: gli aggiornamenti sono cumulativi."))
        self._switch(xcigroup, cfg.includeDlc, FluentIcon.ADD_TO, tr("Includi i DLC"), "")
        lay.addWidget(xcigroup)

        extract = SettingCardGroup(tr("Estrazione"), view)
        self._switch(extract, cfg.extractExefs, FluentIcon.CODE, tr("Estrai la ExeFS"),
                     tr("Eseguibili del gioco (main, main.npdm, subsdk…) nella cartella exefs."))
        self._switch(extract, cfg.extractRomfs, FluentIcon.PHOTO, tr("Estrai la RomFS"),
                     tr("Dati del gioco (grafica, testi, audio) nella cartella romfs."))
        self._switch(extract, cfg.romfsBin, FluentIcon.ZIP_FOLDER, tr("RomFS come unico file"),
                     tr("Salva romfs.bin invece della cartella con i singoli file."))
        self._switch(extract, cfg.applyUpdate, FluentIcon.UPDATE, tr("Applica l'aggiornamento"),
                     tr("Estrae la RomFS e la ExeFS della versione aggiornata (serve anche il "
                        "gioco base)."))
        self._switch(extract, cfg.notifyDone, FluentIcon.RINGER, tr("Notifica a fine operazione"),
                     tr("Mostra una notifica di sistema se nel frattempo stai usando un'altra "
                        "finestra."))
        lay.addWidget(extract)

        look = SettingCardGroup(tr("Aspetto"), view)
        self.themeCard = OptionsSettingCard(
            cfg.themeMode, FluentIcon.BRUSH, tr("Tema"), tr("Chiaro, scuro o come Windows"),
            texts=[tr("Chiaro"), tr("Scuro"), tr("Usa impostazione di sistema")], parent=look)
        self.languageCard = OptionsSettingCard(
            cfg.language, FluentIcon.LANGUAGE, tr("Lingua"), tr("Si applica al prossimo avvio"),
            texts=[tr("Automatica (lingua del sistema)"), "Italiano", "English"], parent=look)
        look.addSettingCard(self.themeCard)
        look.addSettingCard(self.languageCard)
        lay.addWidget(look)

        updates = SettingCardGroup(tr("Aggiornamenti"), view)
        self._switch(updates, cfg.checkUpdates, FluentIcon.UPDATE,
                     tr("Controlla gli aggiornamenti all'avvio"),
                     tr("Cerca una nuova versione su GitHub quando apri il programma."))
        self.updateCard = PushSettingCard(tr("Controlla ora"), FluentIcon.SYNC,
                                          tr("Versione installata"),
                                          f"Switch Toolkit {__version__}", updates)
        self.updateCard.clicked.connect(self.checkUpdatesRequested)
        updates.addSettingCard(self.updateCard)
        lay.addWidget(updates)

        about = SettingCardGroup(tr("Informazioni"), view)
        about.addSettingCard(HyperlinkCard(
            REPO_URL, tr("Codice sorgente"), FluentIcon.INFO, "Switch Toolkit",
            tr("Versione {version} · GPL-3.0 · usa le chiavi della tua console",
               version=__version__), about))
        lay.addWidget(about)
        lay.addStretch(1)

        for item in (cfg.keysFile, cfg.titleKeysFile, cfg.outputDir):
            item.valueChanged.connect(lambda _: self.refresh())
        self.refresh()

    @staticmethod
    def _switch(group, item, icon, title, content):
        card = LocalizedSwitchSettingCard(icon, title, content, configItem=item, parent=group)
        card.setValue(cfg.get(item))
        group.addSettingCard(card)
        return card

    def set_update_status(self, text: str) -> None:
        self.updateCard.setContent(text)

    def refresh(self) -> None:
        chosen = cfg.get(cfg.keysFile)
        found = keys.find_keyfile("prod.keys")
        if chosen:
            self.keysCard.setContent(chosen if os.path.isfile(chosen)
                                     else tr("File non trovato: {path}", path=chosen))
        elif found:
            self.keysCard.setContent(tr("Trovato automaticamente: {path}", path=found))
        else:
            self.keysCard.setContent(tr("Non trovato: ricavalo dalla tua console con "
                                        "Lockpick_RCM"))
        tk = cfg.get(cfg.titleKeysFile) or keys.find_keyfile("title.keys")
        self.titleKeysCard.setContent(tk or tr("Serve solo per NSP senza ticket"))
        out = cfg.get(cfg.outputDir)
        self.outputCard.setContent(out or tr("Non impostata"))

    def _pick_file(self, title: str, start: str) -> str:
        path, _ = QFileDialog.getOpenFileName(self, title, start,
                                              tr("File di chiavi (*.keys *.txt);;Tutti i file (*)"))
        return os.path.normpath(path) if path else ""

    def _choose_keys(self) -> None:
        path = self._pick_file(tr("Scegli prod.keys"), cfg.get(cfg.keysFile))
        if path:
            cfg.set(cfg.keysFile, path)

    def _choose_title_keys(self) -> None:
        path = self._pick_file(tr("Scegli title.keys"), cfg.get(cfg.titleKeysFile))
        if path:
            cfg.set(cfg.titleKeysFile, path)

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Cartella di destinazione"),
                                                  cfg.get(cfg.outputDir))
        if folder:
            cfg.set(cfg.outputDir, os.path.normpath(folder))


class LogPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logPage")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 24, 36, 24)
        lay.setSpacing(14)
        lay.addWidget(TitleLabel(tr("Registro"), self))
        self.text = PlainTextEdit(self)
        self.text.setReadOnly(True)
        font = QFont()
        font.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono"])
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.text.setFont(font)
        lay.addWidget(self.text, 1)

    def append(self, line: str) -> None:
        self.text.appendPlainText(line)
