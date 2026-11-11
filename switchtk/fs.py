"""Random-access storages and the Switch container formats: PFS0 (NSP), HFS0 (XCI), RomFS."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional

from .i18n import tr

CHUNK = 8 << 20


class FormatError(Exception):
    pass


def zstd():
    """The zstandard module (NUP and NX use it)."""
    try:
        import zstandard
    except ImportError as ex:  # pragma: no cover - dependency of the program
        raise FormatError(tr("Manca il modulo zstandard, necessario per i file NUP e NX.")) from ex
    return zstandard


class Cancelled(Exception):
    pass


# -- storages -------------------------------------------------------------------------

class Storage:
    size: int = 0

    def read(self, offset: int, size: int) -> bytes:
        raise NotImplementedError

    def chunks(self, offset: int = 0, size: Optional[int] = None,
               chunk: int = CHUNK) -> Iterator[bytes]:
        end = self.size if size is None else offset + size
        while offset < end:
            n = min(chunk, end - offset)
            yield self.read(offset, n)
            offset += n


class FileStorage(Storage):
    def __init__(self, f: BinaryIO, offset: int = 0, size: Optional[int] = None):
        self.f = f
        self.offset = offset
        if size is None:
            f.seek(0, 2)
            size = f.tell() - offset
        self.size = size

    def read(self, offset: int, size: int) -> bytes:
        size = max(0, min(size, self.size - offset))
        self.f.seek(self.offset + offset)
        data = self.f.read(size)
        if len(data) != size:
            raise FormatError(tr("File troncato: mancano {n} byte.", n=size - len(data)))
        return data


class SubStorage(Storage):
    def __init__(self, parent: Storage, offset: int, size: int):
        if offset + size > parent.size:
            raise FormatError(tr("Dati fuori dai limiti del contenitore (file troncato?)."))
        self.parent, self.offset, self.size = parent, offset, size

    def read(self, offset: int, size: int) -> bytes:
        size = max(0, min(size, self.size - offset))
        return self.parent.read(self.offset + offset, size)


class MemoryStorage(Storage):
    def __init__(self, data: bytes):
        self.data = data
        self.size = len(data)

    def read(self, offset: int, size: int) -> bytes:
        return self.data[offset:offset + size]


# -- PFS0 / HFS0 ----------------------------------------------------------------------

@dataclass
class Entry:
    name: str
    offset: int          # inside the partition storage
    size: int
    hashed_size: int = 0
    sha256: bytes = b""


class Partition:
    """PFS0 (NSP, ExeFS) or HFS0 (XCI): a flat list of named files."""

    def __init__(self, storage: Storage):
        self.storage = storage
        head = storage.read(0, 0x10)
        magic = head[:4]
        if magic not in (b"PFS0", b"HFS0"):
            raise FormatError(tr("Intestazione PFS0/HFS0 non valida."))
        self.magic = magic.decode()
        count, strsize = struct.unpack_from("<II", head, 4)
        esize = 0x18 if magic == b"PFS0" else 0x40
        table = storage.read(0x10, count * esize + strsize)
        strings = table[count * esize:]
        self.header_size = 0x10 + count * esize + strsize
        self.entries: list[Entry] = []
        for i in range(count):
            raw = table[i * esize:(i + 1) * esize]
            offset, size, name_off = struct.unpack_from("<QQI", raw)
            end = strings.find(b"\x00", name_off)
            name = strings[name_off:end if end >= 0 else None].decode("utf-8", "replace")
            e = Entry(name, self.header_size + offset, size)
            if esize == 0x40:
                e.hashed_size = struct.unpack_from("<I", raw, 0x14)[0]
                e.sha256 = raw[0x20:0x40]
            self.entries.append(e)
        self.by_name = {e.name: e for e in self.entries}

    def open(self, name: str) -> Storage:
        e = self.by_name[name]
        return SubStorage(self.storage, e.offset, e.size)


def build_partition(magic: str, files: list[tuple[str, int, int, bytes]],
                    align: int = 0x200) -> bytes:
    """Header for (name, size, hashed_size, sha256) files laid out back to back after it.

    The string table is padded so that the header length is a multiple of `align`.
    """
    hfs = magic == "HFS0"
    esize = 0x40 if hfs else 0x18
    names = b""
    name_offs = []
    for name, *_ in files:
        name_offs.append(len(names))
        names += name.encode() + b"\x00"
    base = 0x10 + esize * len(files)
    total = base + len(names)
    if align:
        total = (total + align - 1) // align * align
    names = names.ljust(total - base, b"\x00")
    out = bytearray(struct.pack("<4sIII", magic.encode(), len(files), len(names), 0))
    offset = 0
    for (name, size, hashed, digest), noff in zip(files, name_offs):
        if hfs:
            out += struct.pack("<QQIIQ", offset, size, noff, hashed, 0) + digest.ljust(32, b"\0")
        else:
            out += struct.pack("<QQII", offset, size, noff, 0)
        offset += size
    return bytes(out + names)


# -- RomFS ----------------------------------------------------------------------------

@dataclass
class RomFile:
    path: str            # "dir/sub/file.bin", forward slashes
    offset: int          # inside the RomFS storage
    size: int


class RomFS:
    NONE = 0xFFFFFFFF

    def __init__(self, storage: Storage):
        self.storage = storage
        head = storage.read(0, 0x50)
        if len(head) < 0x50 or struct.unpack_from("<Q", head)[0] != 0x50:
            raise FormatError(tr("Intestazione RomFS non valida (chiavi errate?)."))
        (_, _dh_off, _dh_size, dm_off, dm_size, _fh_off, _fh_size, fm_off, fm_size,
         self.data_offset) = struct.unpack_from("<10Q", head)
        self.dirs = storage.read(dm_off, dm_size)
        self.files_meta = storage.read(fm_off, fm_size)

    def files(self) -> list[RomFile]:
        out: list[RomFile] = []
        stack = [(0, "")]
        seen = set()
        while stack:
            d_off, prefix = stack.pop()
            if d_off in seen or d_off + 0x18 > len(self.dirs):
                continue
            seen.add(d_off)
            child, fil = struct.unpack_from("<II", self.dirs, d_off + 8)
            f_off = fil
            while f_off != self.NONE and f_off + 0x20 <= len(self.files_meta):
                _p, f_sib, data_off, size, _h, nlen = struct.unpack_from("<IIQQII",
                                                                       self.files_meta, f_off)
                name = self.files_meta[f_off + 0x20:f_off + 0x20 + nlen].decode("utf-8", "replace")
                out.append(RomFile(prefix + name, self.data_offset + data_off, size))
                f_off = f_sib
            c_off = child
            while c_off != self.NONE and c_off + 0x18 <= len(self.dirs):
                _p, c_sib, _c, _f, _h, nlen = struct.unpack_from("<6I", self.dirs, c_off)
                name = self.dirs[c_off + 0x18:c_off + 0x18 + nlen].decode("utf-8", "replace")
                stack.append((c_off, prefix + name + "/"))
                c_off = c_sib
        return out

    def open(self, f: RomFile) -> Storage:
        return SubStorage(self.storage, f.offset, f.size)

    def find(self, path: str) -> Optional[RomFile]:
        return next((f for f in self.files() if f.path == path), None)


def sha256_of(storage: Storage, offset: int = 0, size: Optional[int] = None) -> bytes:
    h = hashlib.sha256()
    for chunk in storage.chunks(offset, size):
        h.update(chunk)
    return h.digest()
