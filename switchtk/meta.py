"""Tickets (.tik), content metadata (.cnmt) and control.nacp."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from .fs import FormatError
from .i18n import tr

# signature type -> (signature size, padding)
SIG_SIZES = {0x10000: (0x200, 0x3C), 0x10001: (0x100, 0x3C), 0x10002: (0x3C, 0x40),
             0x10003: (0x200, 0x3C), 0x10004: (0x100, 0x3C), 0x10005: (0x3C, 0x40)}

META_TYPES = {0x80: "game", 0x81: "update", 0x82: "dlc", 0x83: "delta"}
CONTENT_META, CONTENT_PROGRAM, CONTENT_DELTA = 0, 1, 6


@dataclass
class Ticket:
    rights_id: bytes
    title_key: bytes      # encrypted with titlekek (common tickets)
    personalized: bool


def parse_ticket(data: bytes) -> Ticket:
    if len(data) < 4:
        raise FormatError(tr("Ticket non valido."))
    sig_type = struct.unpack_from("<I", data)[0]
    if sig_type not in SIG_SIZES:
        raise FormatError(tr("Ticket non valido."))
    sig, pad = SIG_SIZES[sig_type]
    body = 4 + sig + pad
    if len(data) < body + 0x180:
        raise FormatError(tr("Ticket non valido."))
    title_key = data[body + 0x40:body + 0x50]
    personalized = data[body + 0x141] == 1
    rights_id = data[body + 0x160:body + 0x170]
    return Ticket(rights_id, title_key, personalized)


@dataclass
class ContentRecord:
    nca_id: str           # 32 hex chars, lowercase
    size: int
    type: int
    sha256: bytes


@dataclass
class Cnmt:
    title_id: int
    version: int
    kind: str             # game, update, dlc, delta
    contents: list[ContentRecord] = field(default_factory=list)
    required_version: int = 0


def parse_cnmt(data: bytes) -> Cnmt:
    if len(data) < 0x20:
        raise FormatError(tr("Metadati CNMT non validi."))
    title_id, version, mtype = struct.unpack_from("<QIB", data)
    ext_size, count = struct.unpack_from("<HH", data, 0x0E)
    kind = META_TYPES.get(mtype)
    if kind is None:
        raise FormatError(tr("Tipo di contenuto non supportato (0x{kind:02X}): non è un gioco, "
                             "un aggiornamento o un DLC.", kind=mtype))
    cnmt = Cnmt(title_id, version, kind)
    if ext_size >= 0xC:
        cnmt.required_version = struct.unpack_from("<I", data, 0x20 + 8)[0]
    base = 0x20 + ext_size
    for i in range(count):
        off = base + i * 0x38
        rec = data[off:off + 0x38]
        if len(rec) < 0x38:
            break
        size = int.from_bytes(rec[0x30:0x36], "little")
        cnmt.contents.append(ContentRecord(rec[0x20:0x30].hex(), size, rec[0x36], rec[:0x20]))
    return cnmt


@dataclass
class Nacp:
    titles: dict[int, tuple[str, str]]   # language index -> (name, publisher)
    display_version: str

    ITALIAN = 7
    ICON_NAMES = ("AmericanEnglish", "BritishEnglish", "Japanese", "French", "German",
                  "LatinAmericanSpanish", "Spanish", "Italian", "Dutch", "CanadianFrench",
                  "Portuguese", "Russian", "Korean", "TraditionalChinese", "SimplifiedChinese",
                  "BrazilianPortuguese")

    def name(self, prefer: Optional[int] = None) -> str:
        order = ([prefer] if prefer is not None else []) + list(range(16))
        for lang in order:
            if lang in self.titles and self.titles[lang][0]:
                return self.titles[lang][0]
        return ""

    def publisher(self) -> str:
        return next((p for _n, p in self.titles.values() if p), "")


def parse_nacp(data: bytes) -> Nacp:
    titles = {}
    for lang in range(16):
        raw = data[lang * 0x300:(lang + 1) * 0x300]
        name = raw[:0x200].split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
        pub = raw[0x200:0x300].split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
        if name or pub:
            titles[lang] = (name, pub)
    version = data[0x3060:0x3070].split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
    return Nacp(titles, version)


def tid_str(title_id: int) -> str:
    return f"{title_id:016X}"


def base_title_id(title_id: int, kind: str) -> int:
    """Title id of the game an update or DLC belongs to."""
    if kind in ("update", "delta"):
        return title_id & ~0xFFF
    if kind == "dlc":
        return (title_id & ~0xFFF) - 0x1000
    return title_id
