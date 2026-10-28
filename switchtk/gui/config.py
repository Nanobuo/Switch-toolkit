"""Persistent settings (stored as JSON in %APPDATA%\\SwitchToolkit)."""

import os

from qfluentwidgets import (BoolValidator, ConfigItem, EnumSerializer, OptionsConfigItem,
                            OptionsValidator, QConfig, Theme, qconfig)

from ..keys import config_dir


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


class Config(QConfig):
    themeMode = OptionsConfigItem(
        "QFluentWidgets", "ThemeMode", Theme.AUTO, OptionsValidator(Theme), EnumSerializer(Theme))
    keysFile = ConfigItem("Keys", "ProdKeys", "")
    titleKeysFile = ConfigItem("Keys", "TitleKeys", "")
    outputDir = ConfigItem("Paths", "OutputDir", "")
    lastDir = ConfigItem("Paths", "LastDir", "")
    mergeXci = ConfigItem("Xci", "Merge", True, BoolValidator())
    includeUpdate = ConfigItem("Xci", "IncludeUpdate", True, BoolValidator())
    includeDlc = ConfigItem("Xci", "IncludeDlc", True, BoolValidator())
    extractExefs = ConfigItem("Extract", "ExeFS", True, BoolValidator())
    extractRomfs = ConfigItem("Extract", "RomFS", True, BoolValidator())
    romfsBin = ConfigItem("Extract", "RomFSBin", False, BoolValidator())
    applyUpdate = ConfigItem("Extract", "ApplyUpdate", True, BoolValidator())
    notifyDone = ConfigItem("General", "NotifyDone", True, BoolValidator())
    checkUpdates = ConfigItem("Updates", "CheckAtStartup", True, BoolValidator())
    language = OptionsConfigItem("General", "Language", "auto",
                                 OptionsValidator(["auto", "it", "en"]), restart=True)


cfg = Config()


def load_config() -> None:
    qconfig.load(config_path(), cfg)
