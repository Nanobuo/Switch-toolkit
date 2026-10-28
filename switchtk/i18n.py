"""Tiny translation layer: Italian source strings, English translations in i18n_en.

    tr("Versione {v}", v="1.0")  ->  "Versione 1.0" or "Version 1.0"
"""

from __future__ import annotations

import locale
import os

_language = "it"
SUPPORTED = ("it", "en")


def set_language(lang: str) -> None:
    global _language
    _language = lang if lang in SUPPORTED else "en"


def get_language() -> str:
    return _language


def system_language() -> str:
    """'it' on an Italian system, 'en' otherwise."""
    candidates = [os.environ.get(v, "") for v in ("LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    for value in candidates:
        if value and value.lower() not in ("c", "posix", "c.utf-8"):
            return "it" if value.lower().startswith("it") else "en"
    return "en"


def resolve(setting: str) -> str:
    """Language for a setting value: 'it', 'en' or 'auto' (system language)."""
    return setting if setting in SUPPORTED else system_language()


def tr(text: str, **kwargs) -> str:
    if _language == "en":
        from .i18n_en import EN
        text = EN.get(text, text)
    return text.format(**kwargs) if kwargs else text
