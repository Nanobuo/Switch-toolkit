"""Building an XCI from NSP/XCI titles: one title (NSP → XCI) or game + update + DLC merged.

Every NCA is copied as is except its 0xC00-byte header: distribution set to "game card" and,
for NCAs that need a ticket, the title key written into the key area (an XCI has no tickets).
The card header is unsigned: emulators and console loaders do not check it.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import struct
from dataclasses import dataclass
from typing import Callable, Optional

from .fs import CHUNK, Cancelled, FormatError, build_partition
from .i18n import tr
from .keys import KeySet
from .meta import tid_str
from .nca import HEADER_SIZE
from .titles import Container, Title

ROOT_OFFSET = 0xF000
PAGE = 0x200
HASHED = 0x200
# (capacity in bytes, card size code)
CART_SIZES = [(1 << 30, 0xFA), (2 << 30, 0xF8), (4 << 30, 0xF0), (8 << 30, 0xE0),
              (16 << 30, 0xE1), (32 << 30, 0xE2)]


@dataclass
class _Item:
    container: str
    name: str
    size: int
    header: bytes = b""


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip().rstrip(".")
    return text or "game"


def default_name(titles: list[Title]) -> str:
    """'<Name> [<TID>][v<ver>][+N DLC].xci' from the titles that go into the XCI."""
    base = next((t for t in titles if t.kind == "game"), None)
    upd = max((t for t in titles if t.kind == "update"), key=lambda t: t.version, default=None)
    named = next((t for t in (upd, base) if t is not None and t.name), titles[0])
    tid = tid_str(base.title_id if base else titles[0].base_id)
    parts = [safe_name(named.name or tid), f"[{tid}]"]
    if upd is not None:
        parts.append(f"[v{upd.version}]")
    elif base is not None:
        parts.append(f"[v{base.version}]")
    dlcs = sum(1 for t in titles if t.kind == "dlc")
    if dlcs and (base or upd):
        parts.append(f"[+{dlcs} DLC]")
    elif dlcs:
        parts.append("[DLC]")
    return " ".join([parts[0], "".join(parts[1:])]) + ".xci"


def cart_size(total: int) -> int:
    return next((code for cap, code in CART_SIZES if total <= cap), CART_SIZES[-1][1])


def collect(titles: list[Title], keys: KeySet) -> list[_Item]:
    """NCAs of the titles with their converted headers (reads only the headers)."""
    items: list[_Item] = []
    seen = set()
    by_path: dict[str, list[Title]] = {}
    for t in titles:
        by_path.setdefault(t.path, []).append(t)
    for path, group in by_path.items():
        with Container(path, keys) as c:
            for t in group:
                files = t.files(c)
                if t.missing:
                    raise FormatError(tr("{name}: mancano {n} NCA indicati nei metadati "
                                         "(file incompleto).", name=os.path.basename(path),
                                         n=len(t.missing)))
                for name, _rec in files:
                    if name in seen:
                        continue
                    seen.add(name)
                    header = c.nca(name).gamecard_header()
                    items.append(_Item(path, name, c.files[name].size, header))
    return items


def layout(items: list[_Item]) -> tuple[bytes, bytes, list[bytes], int]:
    """(card header, root HFS0 header, partition headers, total size)."""
    secure = build_partition("HFS0", [(i.name, i.size, HASHED, _sha(i.header[:HASHED]))
                                      for i in items])
    update = build_partition("HFS0", [])
    normal = build_partition("HFS0", [])
    data = sum(i.size for i in items)
    parts = [("update", update, len(update)), ("normal", normal, len(normal)),
             ("secure", secure, len(secure) + data)]
    root = build_partition("HFS0", [(name, size, len(hdr), _sha(hdr)) for name, hdr, size in parts])
    secure_offset = ROOT_OFFSET + len(root) + len(update) + len(normal)
    total = secure_offset + len(secure) + data
    total_pages = (total + PAGE - 1) // PAGE

    head = bytearray(0x200)
    head[0x100:0x104] = b"HEAD"
    struct.pack_into("<II", head, 0x104, secure_offset // PAGE, 0xFFFFFFFF)
    head[0x10C] = 0
    head[0x10D] = cart_size(total)
    head[0x10E] = 0
    head[0x10F] = 0
    struct.pack_into("<Q", head, 0x110, int.from_bytes(secrets.token_bytes(8), "little"))
    struct.pack_into("<I", head, 0x118, total_pages - 1)
    head[0x120:0x130] = secrets.token_bytes(16)
    struct.pack_into("<QQ", head, 0x130, ROOT_OFFSET, len(root))
    head[0x140:0x160] = _sha(root)
    head[0x160:0x180] = _sha(bytes(0x200))
    struct.pack_into("<IIII", head, 0x180, 1, 2, 0, secure_offset // PAGE)
    return bytes(head), root, [update, normal, secure], total


def build_xci(titles: list[Title], out_path: str, keys: KeySet,
              progress: Optional[Callable[[int, int, str], None]] = None,
              should_cancel: Optional[Callable[[], bool]] = None,
              log: Callable[[str], None] = print, check_space: bool = True) -> str:
    """Write the XCI to out_path (via a temporary .part file). Returns out_path."""
    if not titles:
        raise FormatError(tr("Nessun contenuto da convertire."))
    log(tr("Preparazione di {name}…", name=os.path.basename(out_path)))
    items = collect(titles, keys)
    card, root, parts, total = layout(items)
    data_total = sum(i.size for i in items)
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    if check_space:
        free = shutil.disk_usage(out_dir).free
        if free < total:
            raise OSError(tr("Spazio insufficiente in {path}: servono {needed:.1f} GB, liberi "
                             "{free:.1f} GB.", path=out_dir, needed=total / (1 << 30),
                             free=free / (1 << 30)))
    tmp = out_path + ".part"
    done = 0
    files: dict[str, Container] = {}
    try:
        with open(tmp, "wb") as out:
            out.write(card)
            out.write(bytes(ROOT_OFFSET - len(card)))
            out.write(root)
            for hdr in parts:
                out.write(hdr)
            for item in items:
                if should_cancel and should_cancel():
                    raise Cancelled()
                log(f"  {item.name} ({item.size / (1 << 20):.1f} MB)")
                f = files.get(item.container)
                if f is None:
                    f = files[item.container] = Container(item.container, keys)
                src = f.files[item.name]
                out.write(item.header)
                done += HEADER_SIZE
                for chunk in src.chunks(HEADER_SIZE, src.size - HEADER_SIZE, CHUNK):
                    if should_cancel and should_cancel():
                        raise Cancelled()
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, data_total, item.name)
            pad = -out.tell() % PAGE
            if pad:
                out.write(bytes(pad))
        os.replace(tmp, out_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    finally:
        for c in files.values():
            c.close()
    if progress:
        progress(data_total, data_total, "")
    return out_path
