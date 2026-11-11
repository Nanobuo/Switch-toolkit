"""NX (NXFormat 1.0.2, header versions 0.2 and 0.3): archive of decrypted Switch content.

Data blocks (64 KiB-1 MiB chunks, RAW or one independent zstd frame each, deduplicated
across files and snapshots) come first; a metadata tail follows: Media (icons, NACP),
Catalog (applications, programs, snapshots, display metadata), Manifest (directory tree,
files, block references), hot block index and a CRC-32C per 16 KiB slice. Two header copies
at 0 and 4096. Every snapshot is a complete file tree.

The reader validates the structure the way the specification requires (CRC/digests,
ranges, order, references, tree topology, snapshot identities) and checks every slice it
returns. The writer streams blocks into the final file and writes a compact
generation-0 archive.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import struct
import threading
from array import array
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from .fs import CHUNK, Cancelled, FileStorage, FormatError, Storage, zstd
from .i18n import tr
from .meta import LANGUAGE_TAGS

MAGIC = b"NXAR"
PAGE = 4096
HEADER_SIZE = 256
DATA_START = 8192
CHECK = 16384
NONE32 = 0xFFFFFFFF
NONE64 = 0xFFFFFFFFFFFFFFFF
FEATURE_SEPARATE_PAGES = 1
MAX_CATALOG = 16 << 20
MAX_BLOCK_STORED = (1 << 20) + (64 << 10)
MAX_BLOCK = 1 << 20
PROBE = 8192

SNAP_BASE, SNAP_UPDATE, SNAP_DLC, SNAP_STANDALONE, SNAP_UNKNOWN = 0, 1, 2, 3, 4
PLATFORM_UNKNOWN, PLATFORM_SWITCH1, PLATFORM_SWITCH2 = 0, 1, 2
MEDIA_PNG, MEDIA_JPEG, MEDIA_NPDM, MEDIA_NACP, MEDIA_OPAQUE = 1, 2, 3, 4, 5
EXT_HISTORY, EXT_NATIVE, EXT_RECIPES, EXT_RESOURCES = 0x00010004, 0x00010005, 0x00010006, 0x00010008
KNOWN_OPTIONAL = {0x00010001, 0x00010002, 0x00010003, EXT_RECIPES}

NACP_LANGS = LANGUAGE_TAGS

_header = struct.Struct("<4sHHIHHQQQQQQQQQQQQQ32s32s32sQI24sI")
_block = struct.Struct("<QIIBBHI")
_file = struct.Struct("<QIIBBH32s12s")
_entry = struct.Struct("<QIIBBHI")
_snapshot = struct.Struct("<IBBHIIQII16sII32s32s")
_application = struct.Struct("<QBBH15I8s")
_program = struct.Struct("<QIBBH16sIIII16s")
_media_record = struct.Struct("<HBBQQQ32sI")
assert _header.size == HEADER_SIZE and _block.size == 24 and _file.size == 64
assert _entry.size == 24 and _snapshot.size == 120 and _application.size == 80
assert _program.size == 64 and _media_record.size == 64


class NxError(FormatError):
    pass


def _align(n: int, a: int = 16) -> int:
    return (n + a - 1) // a * a


# -- CRC-32C --------------------------------------------------------------------------

def _crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        table.append(c)
    return table


try:
    from crc32c import crc32c as _fast_crc

    CRC_BACKEND = "C"

    def crc32c(data) -> int:
        return _fast_crc(data)
except ImportError:  # pragma: no cover - the C module ships with the program
    CRC_BACKEND = "Python"
    _TABLE = _crc_table()

    def crc32c(data) -> int:
        c = 0xFFFFFFFF
        for b in bytes(data):
            c = _TABLE[(c ^ b) & 0xFF] ^ (c >> 8)
        return c ^ 0xFFFFFFFF


def slice_crcs(data) -> list[int]:
    view = memoryview(data)
    return [crc32c(view[i:i + CHECK]) for i in range(0, len(view), CHECK)]


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _rechunk(pieces, size: int) -> Iterator[bytes]:
    """Fixed-size pieces (the last may be shorter) from a stream of arbitrary chunks."""
    buf = bytearray()
    for data in pieces:
        if not buf and len(data) == size:
            yield bytes(data)
            continue
        buf += data
        while len(buf) >= size:
            yield bytes(buf[:size])
            del buf[:size]
    if buf:
        yield bytes(buf)


# -- snapshot identity (spec section 12) -------------------------------------------------

def snapshot_identity(platform: int, application_id: int, program_id: int, kind: int,
                      version: int, content_id: bytes,
                      nodes: list[tuple[bytes, Optional[tuple[int, bytes]]]]) -> bytes:
    """nodes: (full path bytes, None for a directory or (size, sha256) for a file)."""
    h = hashlib.sha256(b"NX-SNAPSHOT-ID\x00")
    h.update(struct.pack("<HBQQBI", 3, platform, application_id, program_id, kind, version))
    h.update(content_id)
    h.update(struct.pack("<I", len(nodes)))
    for path, info in sorted(nodes, key=lambda n: n[0]):
        h.update(struct.pack("<BI", 1 if info is None else 2, len(path)) + path)
        if info is not None:
            h.update(struct.pack("<Q", info[0]) + info[1])
    return h.digest()


def valid_name(name: bytes) -> bool:
    return (0 < len(name) <= 4096 and b"\x00" not in name and b"/" not in name
            and b"\\" not in name and name not in (b".", b".."))


def valid_language(tag: str) -> bool:
    if not 1 <= len(tag) <= 63:
        return False
    for part in tag.split("-"):
        if not 1 <= len(part) <= 8 or not all(c.isascii() and (c.islower() or c.isdigit())
                                               for c in part):
            return False
    return True


# =====================================================================================
# Reader
# =====================================================================================

@dataclass
class Snapshot:
    index: int
    application: "Application"
    program_id: Optional[int]
    kind: int
    version: int
    root: int
    logical_size: int
    display_version: str
    content_id: bytes
    addon_title_id: int
    identity: bytes
    build_id: bytes


@dataclass
class SnapshotMeta:
    """Display metadata a snapshot carries (the application's selected one, or history)."""
    title: str = ""
    publisher: str = ""
    localized: dict[str, str] = field(default_factory=dict)
    localized_icons: dict[str, int] = field(default_factory=dict)  # tag -> media index
    icon_media: Optional[int] = None
    nacp_media: Optional[int] = None


@dataclass
class Application:
    index: int
    application_id: int
    platform: int
    title: str = ""
    publisher: str = ""
    localized: dict[str, str] = field(default_factory=dict)
    icon_media: Optional[int] = None
    localized_icons: dict[str, int] = field(default_factory=dict)
    nacp_media: Optional[int] = None
    metadata_snapshot: Optional[int] = None
    snapshots: list[Snapshot] = field(default_factory=list)
    history: dict[int, SnapshotMeta] = field(default_factory=dict)  # other snapshots

    def meta_of(self, snapshot: int) -> Optional[SnapshotMeta]:
        if snapshot == self.metadata_snapshot:
            return SnapshotMeta(self.title, self.publisher, self.localized,
                                self.localized_icons, self.icon_media, self.nacp_media)
        return self.history.get(snapshot)


class _Bytes:
    """Bounds-checked little-endian field access on one region."""

    def __init__(self, data: bytes, error: NxError):
        self.data, self.error = data, error

    def unpack(self, st: struct.Struct, off: int) -> tuple:
        if off < 0 or off + st.size > len(self.data):
            raise self.error
        return st.unpack_from(self.data, off)

    def u32(self, off: int) -> int:
        return self.unpack(_U32, off)[0]

    def u16(self, off: int) -> int:
        return self.unpack(_U16, off)[0]

    def u64(self, off: int) -> int:
        return self.unpack(_U64, off)[0]

    def zero(self, start: int, end: int) -> bool:
        return not any(self.data[start:end])


_U16, _U32, _U64 = struct.Struct("<H"), struct.Struct("<I"), struct.Struct("<Q")


def _table_layout(tables: list[tuple[int, int, int, int]], header: int,
                  pool: tuple[int, int], bad: NxError) -> int:
    """Check consecutive tables [(offset, count, record size, alignment)] and the pool
    that follows them. Returns the end of the pool."""
    cursor = header
    for off, count, size, align in tables:
        if (not count and off) or (count and off != _align(cursor, align)):
            raise bad
        if count:
            cursor = off + count * size
    pool_off, pool_size = pool
    if pool_off != _align(cursor, 8):
        raise bad
    return pool_off + pool_size


class NxArchive:
    def __init__(self, path: str, verify_reads: bool = True):
        self.path = path
        self.verify_reads = verify_reads
        self.f = open(path, "rb")
        self._cache: OrderedDict[int, bytes] = OrderedDict()   # a few decoded zstd blocks
        self._dctx = zstd().ZstdDecompressor()
        try:
            self._mount()
        except (struct.error, IndexError, UnicodeDecodeError, ValueError) as ex:
            self.f.close()
            raise NxError(tr("{name}: archivio NX non valido.",
                             name=os.path.basename(path))) from ex
        except BaseException:
            self.f.close()
            raise

    def close(self) -> None:
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, off: int, size: int) -> bytes:
        return self.raw.read(off, size)

    # -- header slots ----------------------------------------------------------------
    @staticmethod
    def _slot(page: bytes) -> Optional[dict]:
        if len(page) < PAGE or page[:4] != MAGIC:
            return None
        v = _header.unpack_from(page)
        h = dict(zip(("magic", "major", "minor", "header_size", "required", "optional",
                      "archive_size", "catalog_offset", "catalog_size", "media_offset",
                      "media_size", "data_offset", "data_size", "manifest_offset",
                      "manifest_size", "index_offset", "block_count", "integrity_offset",
                      "integrity_size", "catalog_sha", "manifest_sha", "index_sha",
                      "generation", "check_size", "reserved", "crc"), v))
        if crc32c(page[:252]) != h["crc"]:
            return None
        h["raw"] = page[:HEADER_SIZE]
        h["padding_ok"] = not any(page[HEADER_SIZE:PAGE])
        return h

    def _mount(self) -> None:
        name = os.path.basename(self.path)
        self.raw = FileStorage(self.f)
        size = self.raw.size
        if size < DATA_START:
            raise NxError(tr("{name}: archivio NX troncato.", name=name))
        pages = [self._read(0, PAGE), self._read(PAGE, PAGE)]
        slots = [self._slot(p) for p in pages]
        problems = []
        for h in slots:
            if h is None:
                continue
            if h["major"] != 0 or h["minor"] not in (2, 3) or h["header_size"] != HEADER_SIZE \
                    or not h["padding_ok"]:
                problems.append(tr("{name}: versione NX non supportata.", name=name))
                h["bad"] = True
            elif h["required"] != FEATURE_SEPARATE_PAGES or h["optional"] or any(h["reserved"]) \
                    or h["check_size"] != CHECK:
                problems.append(tr("{name}: funzione NX non supportata.", name=name))
                h["bad"] = True
        valid = [h for h in slots if h is not None and not h.get("bad")]
        if not valid:
            raise NxError(problems[0] if problems else
                          tr("{name}: nessuna intestazione NX valida.", name=name))
        if len(valid) == 2 and valid[0]["generation"] == valid[1]["generation"] \
                and valid[0]["raw"] != valid[1]["raw"]:
            raise NxError(tr("{name}: intestazioni NX in conflitto.", name=name))
        valid.sort(key=lambda h: h["generation"], reverse=True)
        error = None
        for h in valid:  # newest generation first; an older intact one is a fallback
            try:
                self._mount_generation(h)
                return
            except NxError as ex:
                error = error or ex
        raise error

    def _mount_generation(self, h: dict) -> None:
        name = os.path.basename(self.path)
        self.header = h
        size = self.raw.size
        if h["archive_size"] > size or h["archive_size"] > (1 << 63) - 1:
            raise NxError(tr("{name}: archivio NX troncato.", name=name))
        self.minor = h["minor"]
        nblocks = h["block_count"]
        if nblocks > 1 << 32:
            raise NxError(tr("{name}: troppi blocchi.", name=name))
        bad = NxError(tr("{name}: regioni dell'archivio NX non valide.", name=name))
        if bool(nblocks) != bool(h["data_offset"]) or (nblocks and h["data_offset"] != DATA_START):
            raise bad
        if not nblocks and (h["data_size"] or h["index_offset"] or h["integrity_offset"]
                            or h["integrity_size"]):
            raise bad
        if bool(h["media_offset"]) != bool(h["media_size"]):
            raise bad
        end = DATA_START + h["data_size"] if nblocks else DATA_START
        regions = []
        if h["media_size"]:
            regions.append(("media_offset", "media_size"))
        regions += [("catalog_offset", "catalog_size"), ("manifest_offset", "manifest_size")]
        if nblocks:
            regions += [("index_offset", None), ("integrity_offset", "integrity_size")]
        first = True
        for off_key, size_key in regions:
            off = h[off_key]
            length = h[size_key] if size_key else nblocks * 24
            if off % 16 or (first and off < _align(end)) or (not first and off != _align(end)):
                raise bad
            first = False
            end = off + length
        if end != h["archive_size"] or end > size:
            raise bad
        if not 128 <= h["catalog_size"] <= MAX_CATALOG or not 96 <= h["manifest_size"] <= 1 << 32:
            raise bad

        catalog = self._read(h["catalog_offset"], h["catalog_size"])
        if hashlib.sha256(catalog).digest() != h["catalog_sha"]:
            raise NxError(tr("{name}: catalogo NX danneggiato.", name=name))
        manifest = self._read(h["manifest_offset"], h["manifest_size"])
        if hashlib.sha256(manifest).digest() != h["manifest_sha"]:
            raise NxError(tr("{name}: manifest NX danneggiato.", name=name))
        index = self._read(h["index_offset"], nblocks * 24) if nblocks else b""
        integrity = self._read(h["integrity_offset"], h["integrity_size"]) if nblocks else b""
        if hashlib.sha256(index + integrity).digest() != h["index_sha"]:
            raise NxError(tr("{name}: indice dei blocchi NX danneggiato.", name=name))
        self._parse_index(index, integrity, h)
        self._parse_media(h)
        self._parse_manifest(_Bytes(manifest, NxError(tr("{name}: manifest NX non valido.",
                                                         name=name))), h["manifest_size"])
        self._parse_catalog(_Bytes(catalog, NxError(tr("{name}: catalogo NX non valido.",
                                                       name=name))), h["catalog_size"])

    # -- block index -------------------------------------------------------------------
    def _parse_index(self, index: bytes, integrity: bytes, h: dict) -> None:
        bad = NxError(tr("{name}: indice dei blocchi NX non valido.",
                         name=os.path.basename(self.path)))
        n = h["block_count"]
        self.blocks: list[tuple[int, int, int, int, int]] = []
        checks = 0
        cursor = h["data_offset"]
        for i in range(n):
            off, stored, logical, codec, flags, res, first = _block.unpack_from(index, i * 24)
            if (off % 16 or off < cursor or not stored or stored > MAX_BLOCK_STORED
                    or not 1 <= logical <= MAX_BLOCK or codec > 1 or flags or res
                    or first != checks or (codec == 0 and stored != logical)):
                raise bad
            cursor = off + stored
            checks += (logical + CHECK - 1) // CHECK
            self.blocks.append((off, stored, logical, codec, first))
        if n and cursor != h["data_offset"] + h["data_size"]:
            raise bad
        if h["integrity_size"] != checks * 4:
            raise bad
        self.crcs = array("I")
        self.crcs.frombytes(integrity)
        if self.crcs.itemsize != 4:  # pragma: no cover - every supported platform
            raise bad
        if struct.pack("<I", 1) != struct.pack("=I", 1):  # pragma: no cover - big endian
            self.crcs.byteswap()

    # -- media -------------------------------------------------------------------------
    def _parse_media(self, h: dict) -> None:
        self.media: list[tuple[int, int, int, bytes]] = []  # (type, offset, size, sha)
        if not h["media_size"]:
            return
        base, size = h["media_offset"], h["media_size"]
        bad = NxError(tr("{name}: regione media NX non valida.",
                         name=os.path.basename(self.path)))
        head = self._read(base, 32)
        magic, ver, hsize, total, count, rsize, res, table, payload = \
            struct.unpack("<4sHHQIHHII", head)
        if (magic != b"NXMD" or ver != 1 or hsize != 32 or total != size or not
                1 <= count <= 65535 or rsize != 64 or res or table != 32
                or payload != _align(32 + count * 64)):
            raise bad
        records = self._read(base + 32, count * 64)
        cursor = payload
        for i in range(count):
            mtype, codec, flags, stored, logical, off, sha, r = \
                _media_record.unpack_from(records, i * 64)
            if (not 1 <= mtype <= 5 or codec or flags or r or not 1 <= stored <= 64 << 20
                    or logical != stored or off != _align(cursor)):
                raise bad
            cursor = off + stored
            self.media.append((mtype, base + off, stored, sha))
        if cursor != size:
            raise bad

    def media_bytes(self, index: int) -> bytes:
        mtype, off, size, sha = self.media[index]
        data = self._read(off, size)
        if hashlib.sha256(data).digest() != sha:
            raise NxError(tr("{name}: dati media NX danneggiati.",
                             name=os.path.basename(self.path)))
        return data

    # -- manifest ----------------------------------------------------------------------
    def _parse_manifest(self, m: _Bytes, total: int) -> None:
        bad = NxError(tr("{name}: manifest NX non valido.", name=os.path.basename(self.path)))
        (magic, ver, hsize, tsize, ndirs, nfiles, nentries, nrefs, dir_off, file_off,
         entry_off, ref_off, pool_off, pool_size) = m.unpack(
            struct.Struct("<4sHHQIIIIQQQQQQ"), 0)
        if (magic != b"NXMF" or ver != 1 or hsize != 96 or tsize != total or not m.zero(80, 96)
                or not 1 <= ndirs <= 1 << 24 or nfiles > 1 << 25 or nentries > 50331648
                or pool_size > 1 << 31):
            raise bad
        end = _table_layout([(dir_off, ndirs, 16, 8), (file_off, nfiles, 64, 8),
                             (entry_off, nentries, 24, 8), (ref_off, nrefs, 4, 4)], 96,
                            (pool_off, pool_size), bad)
        if end != total:
            raise bad
        data = m.data
        self.pool = data[pool_off:pool_off + pool_size]
        self.dirs = [struct.unpack_from("<IIII", data, dir_off + i * 16) for i in range(ndirs)]
        cursor = 0
        for first, count, flags, res in self.dirs:
            if first != cursor or flags or res:
                raise bad
            cursor += count
        if cursor != nentries:
            raise bad
        self.entries = []
        for i in range(nentries):
            noff, nlen, target, kind, flags, r0, r1 = _entry.unpack_from(data, entry_off + i * 24)
            if (kind not in (1, 2) or flags or r0 or r1 or noff < pool_off
                    or noff + nlen > pool_off + pool_size):
                raise bad
            name = data[noff:noff + nlen]
            if not valid_name(name):
                raise NxError(tr("{name}: percorso non valido nell'archivio NX.",
                                 name=os.path.basename(self.path)))
            name.decode("utf-8")
            if target >= (ndirs if kind == 1 else nfiles):
                raise bad
            self.entries.append((name, kind, target))
        for first, count, _f, _r in self.dirs:
            names = [self.entries[i][0] for i in range(first, first + count)]
            if any(a >= b for a, b in zip(names, names[1:])):
                raise bad
        self.refs = array("I")
        self.refs.frombytes(data[ref_off:ref_off + nrefs * 4] if nrefs else b"")
        if struct.pack("<I", 1) != struct.pack("=I", 1):  # pragma: no cover
            self.refs.byteswap()
        self.files = []
        cursor = 0
        empty = hashlib.sha256(b"").digest()
        for i in range(nfiles):
            size, first, count, log2, flags, r0, sha, r1 = _file.unpack_from(data,
                                                                            file_off + i * 64)
            if (first != cursor or not 16 <= log2 <= 20 or flags or r0 or any(r1)
                    or size > (1 << 63) - 1):
                raise bad
            chunk = 1 << log2
            if count != (size + chunk - 1) // chunk or (not size and sha != empty):
                raise bad
            for k in range(count):
                block = self.refs[first + k] if first + k < nrefs else NONE32
                if block >= len(self.blocks):
                    raise bad
                want = chunk if k < count - 1 else size - chunk * (count - 1)
                if self.blocks[block][2] != want:
                    raise bad
            cursor += count
            self.files.append((size, first, count, log2, sha))
        if cursor != nrefs:
            raise bad

    def walk(self, root: int) -> Iterator[tuple[str, int]]:
        """(relative path, file id) of every file below a directory, validating topology."""
        for path, kind, target in self._nodes(root):
            if kind == 2:
                yield path, target

    def _nodes(self, root: int) -> list[tuple[str, int, int]]:
        bad = NxError(tr("{name}: albero delle cartelle NX non valido.",
                         name=os.path.basename(self.path)))
        out = []
        seen_dirs, seen_files = {root}, set()
        stack = [(root, "", 0)]
        while stack:
            d, prefix, depth = stack.pop()
            if depth > 256:
                raise bad
            first, count, _f, _r = self.dirs[d]
            for i in range(first, first + count):
                name, kind, target = self.entries[i]
                path = prefix + name.decode("utf-8")
                if kind == 1:
                    if target in seen_dirs:
                        raise bad
                    seen_dirs.add(target)
                    out.append((path, 1, target))
                    stack.append((target, path + "/", depth + 1))
                else:
                    if target in seen_files:
                        raise bad
                    seen_files.add(target)
                    out.append((path, 2, target))
        return out

    # -- catalog -----------------------------------------------------------------------
    def _parse_catalog(self, c: _Bytes, total: int) -> None:
        name = os.path.basename(self.path)
        bad = NxError(tr("{name}: catalogo NX non valido.", name=name))
        (magic, ver, hsize, tsize, flags, napps, nprogs, nsnaps, nloc, ndeps, nexts, r0,
         app_off, prog_off, snap_off, loc_off, dep_off, ext_off, pool_off, pool_size) = \
            c.unpack(struct.Struct("<4sHHQIIIIIIIIQQQQQQQQ"), 0)
        if (magic != b"NXCT" or ver != 1 or hsize != 128 or tsize != total or flags or r0
                or not c.zero(112, 128) or not 1 <= napps <= 4096 or nprogs > 16384
                or not 1 <= nsnaps <= 65536 or nloc > 16384 or ndeps > 32768 or nexts > 4096):
            raise bad
        tables = [(app_off, napps, 80, 8), (prog_off, nprogs, 64, 8), (snap_off, nsnaps, 120, 8),
                  (loc_off, nloc, 24, 8), (dep_off, ndeps, 56, 8), (ext_off, nexts, 32, 8)]
        pool_end = _table_layout(tables, 128, (pool_off, pool_size), bad)
        data = c.data
        pool = data[pool_off:pool_off + pool_size]

        def string(off: int, length: int, limit: int, required: bool = False) -> Optional[str]:
            if off == NONE32 and length == 0 and not required:
                return None
            if not 1 <= length <= limit or off + length > len(pool):
                raise NxError(tr("{name}: stringa non valida nel catalogo NX.", name=name))
            raw = pool[off:off + length]
            if b"\x00" in raw:
                raise NxError(tr("{name}: stringa non valida nel catalogo NX.", name=name))
            return raw.decode("utf-8")

        media_use: dict[int, set[str]] = {}

        def media(index: int, kinds: tuple[int, ...], role: str) -> Optional[int]:
            if index == NONE32:
                return None
            if index >= len(self.media) or self.media[index][0] not in kinds:
                raise bad
            media_use.setdefault(index, set()).add(role)
            return index

        icon = (MEDIA_PNG, MEDIA_JPEG)
        # programs
        programs = []
        for i in range(nprogs):
            (pid, app, kind, pflags, r0, content, first, count, npdm, r1,
             r2) = _program.unpack_from(data, prog_off + i * 64)
            if (not pid or app >= napps or kind > 2 or pflags & ~1 or r0 or r1 or any(r2)
                    or bool(pflags & 1) != any(content) or not count or first + count > nsnaps):
                raise bad
            media(npdm, (MEDIA_NPDM,), f"npdm{i}")
            programs.append((pid, app, first, count))
        # snapshots
        snaps = []
        for i in range(nsnaps):
            (prog, kind, sflags, r0, version, root, lsize, dv_off, dv_len, content,
             dep_first, dep_count, identity, build) = _snapshot.unpack_from(data,
                                                                            snap_off + i * 120)
            if kind > 4 or sflags & ~3 or r0 or root >= len(self.dirs):
                raise bad
            if bool(sflags & 1) != any(build):
                raise bad
            if prog == NONE32:
                if kind != SNAP_DLC:
                    raise bad
            elif prog >= nprogs:
                raise bad
            addon = 0
            if sflags & 2:
                addon, tail = struct.unpack("<Q8s", content)
                if kind != SNAP_DLC or prog != NONE32 or not addon or any(tail):
                    raise bad
            if dep_first + dep_count > ndeps:
                raise bad
            dv = string(dv_off, dv_len, 256) or ""
            snaps.append([prog, kind, version, root, lsize, dv, content, addon, identity, build])
        # program ranges hold exactly their snapshots
        for p, (pid, app, first, count) in enumerate(programs):
            if any(snaps[s][0] != p for s in range(first, first + count)):
                raise bad
        # applications
        self.applications: list[Application] = []
        prev_key = None
        snap_cursor = prog_cursor = loc_cursor = 0
        owner_of_snapshot = [None] * nsnaps
        for i in range(napps):
            (aid, platform, aflags, r0, pfirst, pcount, sfirst, scount, t_off, t_len, p_off,
             p_len, icon_i, lfirst, lcount, nacp_i, ofirst, ocount, meta_snap,
             r1) = _application.unpack_from(data, app_off + i * 80)
            key = (platform, aid)
            if (not aid or platform > 2 or aflags & ~3 or r0 or any(r1) or not scount
                    or (prev_key is not None and key <= prev_key)):
                raise bad
            prev_key = key
            if sfirst != snap_cursor or sfirst + scount > nsnaps:
                raise bad
            snap_cursor += scount
            if pcount:
                if pfirst != prog_cursor or pfirst + pcount > nprogs:
                    raise bad
                prog_cursor += pcount
                if any(programs[p][1] != i for p in range(pfirst, pfirst + pcount)):
                    raise bad
            elif pfirst != NONE32:
                raise bad
            if lfirst != loc_cursor and lcount:
                raise bad
            loc_cursor += lcount
            app = Application(i, aid, platform)
            kinds = set()
            for s in range(sfirst, sfirst + scount):
                prog = snaps[s][0]
                if prog != NONE32 and not pfirst <= prog < pfirst + pcount:
                    raise bad
                owner_of_snapshot[s] = i
                kinds.add(snaps[s][1])
            if bool(aflags & 1) != (SNAP_BASE in kinds) or bool(aflags & 2) != (SNAP_DLC in kinds):
                raise bad
            app.title = string(t_off, t_len, 4096) or ""
            app.publisher = string(p_off, p_len, 4096) or ""
            app.icon_media = media(icon_i, icon, "icon")
            app.nacp_media = media(nacp_i, (MEDIA_NACP,), f"nacp{i}")
            for k in range(ocount):
                media(ofirst + k, (MEDIA_OPAQUE,), f"opaque{i}")
            has_meta = (app.title or app.publisher or icon_i != NONE32 or nacp_i != NONE32
                        or lcount or ocount)
            if meta_snap == NONE32:
                if has_meta:
                    raise bad
            else:
                if not sfirst <= meta_snap < sfirst + scount or snaps[meta_snap][1] == SNAP_DLC \
                        or not has_meta:
                    raise bad
                app.metadata_snapshot = meta_snap
            app.loc_range = (lfirst, lcount)
            self.applications.append(app)
        if snap_cursor != nsnaps or prog_cursor != nprogs or loc_cursor != nloc:
            raise bad
        # localized entries
        for app in self.applications:
            lfirst, lcount = app.loc_range
            prev = None
            for k in range(lfirst, lfirst + lcount):
                l_off, l_len, t_off, t_len, icon_i, lflags = struct.unpack_from(
                    "<IIIIII", data, loc_off + k * 24)
                lang = string(l_off, l_len, 63, True)
                title = string(t_off, t_len, 4096)
                if lflags or (title is None and icon_i == NONE32) or not valid_language(lang) \
                        or (prev is not None and lang.encode() <= prev.encode()):
                    raise bad
                prev = lang
                if title:
                    app.localized[lang] = title
                if icon_i != NONE32:
                    app.localized_icons[lang] = media(icon_i, icon, "icon")
        # extensions
        seen_ext = set()
        cursor = pool_end
        for k in range(nexts):
            tid, eflags, owner_kind, r0, owner, d_off, d_size = struct.unpack_from(
                "<IIHHIQQ", data, ext_off + k * 32)
            if (eflags & ~1 or r0 or owner_kind > 3 or d_off != _align(cursor, 8)
                    or d_off + d_size > total or (tid, owner_kind, owner) in seen_ext):
                raise bad
            seen_ext.add((tid, owner_kind, owner))
            cursor = d_off + d_size
            payload = data[d_off:d_off + d_size]
            if tid == EXT_HISTORY:
                self._history(payload, owner, snaps, owner_of_snapshot, string, media, bad)
            elif tid == EXT_NATIVE:
                self._native(payload, owner, snaps, owner_of_snapshot, media, bad)
            elif tid == EXT_RESOURCES:
                if self.minor != 3:
                    raise bad
                self._resources(payload, bad)
            elif eflags & 1:
                raise NxError(tr("{name}: estensione NX obbligatoria non supportata.", name=name))
        if cursor != total:
            raise bad
        if self.minor == 3 and not any(t == EXT_RESOURCES for t, _k, _o in seen_ext):
            raise bad
        # every media record used, non-icon records by exactly one owner
        for i, (mtype, *_rest) in enumerate(self.media):
            roles = media_use.get(i)
            if not roles or (mtype not in icon and len(roles) != 1):
                raise bad
            if mtype not in icon and any(r == "icon" for r in roles):
                raise bad
        # snapshots: identities and logical sizes
        idents = set()
        for s, row in enumerate(snaps):
            prog, kind, version, root, lsize, dv, content, addon, identity, build = row
            app = self.applications[owner_of_snapshot[s]]
            nodes = [(b"", None)]
            logical = 0
            for path, nkind, target in self._nodes(root):
                if nkind == 1:
                    nodes.append((path.encode("utf-8"), None))
                else:
                    size, _f, _c, _l, sha = self.files[target]
                    logical += size
                    nodes.append((path.encode("utf-8"), (size, sha)))
            pid = programs[prog][0] if prog != NONE32 else NONE64
            computed = snapshot_identity(app.platform, app.application_id, pid, kind, version,
                                         content, nodes)
            if computed != identity or identity in idents or logical != lsize:
                raise bad
            idents.add(identity)
            app.snapshots.append(Snapshot(s, app, None if prog == NONE32 else pid, kind,
                                          version, root, lsize, dv, content, addon, identity,
                                          build))

    def _history(self, p: bytes, owner: int, snaps, owners, string, media, bad) -> None:
        if len(p) < 24:
            raise bad
        magic, ver, hsize, nrec, nloc, nopq, res = struct.unpack_from("<4sHHIIII", p)
        if (magic != b"NXSM" or ver != 1 or hsize != 24 or res or not 1 <= nrec <= 65536
                or len(p) != 24 + nrec * 48 + nloc * 24 + nopq * 4
                or owner >= len(self.applications)):
            raise bad
        app = self.applications[owner]
        prev = -1
        for r in range(nrec):
            (snap, t_off, t_len, p_off, p_len, icon_i, nacp_i, lfirst, lcount, ofirst, ocount,
             res) = struct.unpack_from("<12I", p, 24 + r * 48)
            if (res or snap <= prev or snap >= len(snaps) or owners[snap] != owner
                    or snap == app.metadata_snapshot or snaps[snap][1] == SNAP_DLC
                    or lfirst + lcount > nloc or ofirst + ocount > nopq):
                raise bad
            prev = snap
            meta = SnapshotMeta(string(t_off, t_len, 4096) or "",
                                string(p_off, p_len, 4096) or "",
                                icon_media=media(icon_i, (MEDIA_PNG, MEDIA_JPEG), "icon"),
                                nacp_media=media(nacp_i, (MEDIA_NACP,), f"hist-nacp{snap}"))
            app.history[snap] = meta
            base = 24 + nrec * 48
            prev_lang = None
            for k in range(lfirst, lfirst + lcount):
                l_off, l_len, lt_off, lt_len, l_icon, lflags = struct.unpack_from(
                    "<IIIIII", p, base + k * 24)
                lang = string(l_off, l_len, 63, True)
                title = string(lt_off, lt_len, 4096)
                if lflags or (title is None and l_icon == NONE32) or not valid_language(lang) \
                        or (prev_lang is not None and lang.encode() <= prev_lang.encode()):
                    raise bad
                prev_lang = lang
                if title:
                    meta.localized[lang] = title
                if l_icon != NONE32:
                    meta.localized_icons[lang] = media(l_icon, (MEDIA_PNG, MEDIA_JPEG), "icon")
            obase = base + nloc * 24
            for k in range(ofirst, ofirst + ocount):
                media(struct.unpack_from("<I", p, obase + k * 4)[0], (MEDIA_OPAQUE,),
                      f"hist-opaque{snap}-{k}")

    def _native(self, p: bytes, owner: int, snaps, owners, media, bad) -> None:
        if len(p) < 16:
            raise bad
        magic, ver, hsize, n, res = struct.unpack_from("<4sHHII", p)
        if magic != b"NXNM" or ver != 1 or hsize != 16 or res or not n or len(p) != 16 + n * 16:
            raise bad
        prev = -1
        for r in range(n):
            snap, npdm, firmware, flags = struct.unpack_from("<IIII", p, 16 + r * 16)
            if (snap <= prev or snap >= len(snaps) or owners[snap] != owner or flags & ~1
                    or (not flags & 1 and firmware) or (npdm == NONE32 and not flags & 1)
                    or (npdm != NONE32 and snaps[snap][0] == NONE32)):
                raise bad
            prev = snap
            media(npdm, (MEDIA_NPDM,), f"native-npdm{snap}")

    def _resources(self, p: bytes, bad) -> None:
        if len(p) < 16:
            raise bad
        magic, ver, hsize, n, res = struct.unpack_from("<4sHHII", p)
        if magic != b"NXSR" or ver != 1 or hsize != 16 or res or not 1 <= n <= 4096 \
                or len(p) != 16 + n * 8:
            raise bad
        prev = None
        for r in range(n):
            kind, root = struct.unpack_from("<II", p, 16 + r * 8)
            if kind > 1 or root >= len(self.dirs) or (prev is not None and (kind, root) <= prev):
                raise bad
            prev = (kind, root)

    # -- data ----------------------------------------------------------------------------
    def block(self, block_id: int, start: int = 0, end: Optional[int] = None) -> bytes:
        """Logical bytes [start, end) of a block, each covering 16 KiB slice CRC-checked."""
        off, stored, logical, codec, first = self.blocks[block_id]
        end = logical if end is None else min(end, logical)
        if codec == 0:
            if not self.verify_reads:
                return self._read(off + start, end - start)
            s0, s1 = start // CHECK, (end + CHECK - 1) // CHECK  # only the slices needed
            lo, hi = s0 * CHECK, min(s1 * CHECK, logical)
            data = self._read(off + lo, hi - lo)
            for k in range(s0, s1):
                piece = data[(k - s0) * CHECK:(k - s0 + 1) * CHECK]
                if crc32c(piece) != self.crcs[first + k]:
                    raise self._damaged()
            return data[start - lo:end - lo]
        data = self._cache.get(block_id)
        if data is None:
            frame = self._read(off, stored)
            try:
                data = self._dctx.decompress(frame, max_output_size=logical)
            except Exception as ex:
                raise self._damaged() from ex
            checks = (logical + CHECK - 1) // CHECK
            if len(data) != logical or slice_crcs(data) != self.crcs[first:first + checks].tolist():
                raise self._damaged()
            self._cache[block_id] = data
            if len(self._cache) > 8:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(block_id)
        return data[start:end]

    def _damaged(self) -> NxError:
        return NxError(tr("{name}: blocco di dati NX danneggiato.",
                          name=os.path.basename(self.path)))

    def open(self, file_id: int) -> "NxFileStorage":
        return NxFileStorage(self, file_id)

    def tree(self, root: int) -> dict[str, "NxFileStorage"]:
        return {path: self.open(fid) for path, fid in self.walk(root)}

    def verify(self, progress: Optional[Callable[[int, int], None]] = None,
               should_cancel: Optional[Callable[[], bool]] = None) -> list[tuple[str, str]]:
        """Full verification: every block (orphans too), media hashes, every file SHA-256."""
        out = []
        total = sum(b[2] for b in self.blocks) + sum(f[0] for f in self.files)
        done = 0
        bad_blocks = set()
        for i in range(len(self.blocks)):
            if should_cancel and should_cancel():
                raise Cancelled()
            try:
                self.block(i)
            except NxError:
                bad_blocks.add(i)
            done += self.blocks[i][2]
            if progress:
                progress(done, total)
        if bad_blocks:
            out.append((tr("{n} blocchi", n=len(bad_blocks)), "mismatch"))
        for i in range(len(self.media)):
            try:
                self.media_bytes(i)
            except NxError:
                out.append((f"media {i}", "mismatch"))
        names = {}
        for app in self.applications:
            for s in app.snapshots:
                for path, fid in self.walk(s.root):
                    names.setdefault(fid, f"{app.application_id:016X}/{path}")
        for fid, (size, first, count, log2, sha) in enumerate(self.files):
            if should_cancel and should_cancel():
                raise Cancelled()
            label = names.get(fid, f"file {fid}")
            if any(self.refs[first + k] in bad_blocks for k in range(count)):
                out.append((label, "mismatch"))
                done += size
                continue
            h = hashlib.sha256()
            for k in range(count):
                piece = self.block(self.refs[first + k])
                h.update(piece)
                done += len(piece)
                if progress:
                    progress(done, total)
            out.append((label, "ok" if h.digest() == sha else "mismatch"))
        return out


class NxFileStorage(Storage):
    def __init__(self, archive: NxArchive, file_id: int):
        self.archive = archive
        self.size, self.first, self.count, log2, self.sha = archive.files[file_id]
        self.chunk = 1 << log2

    def read(self, offset: int, size: int) -> bytes:
        size = max(0, min(size, self.size - offset))
        out = bytearray()
        end = offset + size
        while offset < end:
            k, inner = divmod(offset, self.chunk)
            n = min(self.chunk - inner, end - offset)
            out += self.archive.block(self.archive.refs[self.first + k], inner, inner + n)
            offset += n
        return bytes(out)

    def chunks(self, offset: int = 0, size: Optional[int] = None,
               chunk: int = CHUNK) -> Iterator[bytes]:
        if offset or size is not None:
            yield from super().chunks(offset, size, chunk)
            return
        for k in range(self.count):  # block by block: each block decoded once
            yield self.archive.block(self.archive.refs[self.first + k])


def is_nx(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == MAGIC
    except OSError:
        return False


# =====================================================================================
# Writer
# =====================================================================================

@dataclass
class Meta:
    """Native display metadata of one snapshot."""
    title: str = ""
    publisher: str = ""
    localized: dict[str, tuple[str, Optional[bytes]]] = field(default_factory=dict)
    icon: Optional[bytes] = None
    nacp: Optional[bytes] = None

    def __bool__(self) -> bool:
        return bool(self.title or self.publisher or self.localized or self.icon or self.nacp)


@dataclass
class NewSnapshot:
    kind: int
    version: int
    tree: dict[str, int]                  # relative path -> file id
    executable: bool = True               # False: DLC without a program
    content_id: bytes = bytes(16)
    addon_title_id: int = 0
    build_id: bytes = bytes(32)
    display_version: str = ""
    meta: Meta = field(default_factory=Meta)


@dataclass
class NewApplication:
    application_id: int
    platform: int
    snapshots: list[NewSnapshot] = field(default_factory=list)


class NxWriter:
    """Streams file data into blocks; finish() writes the metadata tail and headers."""

    def __init__(self, path: str, level: int = 3, chunk_log2: int = 18,
                 compressed_chunk_log2: int = 16, min_gain_bp: int = 2500,
                 compress: bool = True,
                 should_cancel: Optional[Callable[[], bool]] = None, workers: int = 0):
        self.path = path
        self.tmp = path + ".part"
        self.level = level
        self.chunk_log2 = chunk_log2
        self.compressed_chunk_log2 = compressed_chunk_log2
        self.min_gain_bp = min_gain_bp
        self.should_cancel = should_cancel
        self.compress = compress
        self._local = threading.local()
        self._dctx = zstd().ZstdDecompressor()
        self.workers = workers or min(8, os.cpu_count() or 1)
        self.pool = ThreadPoolExecutor(self.workers) if self.workers > 1 else None
        self.batch = 4 * self.workers
        self.f = open(self.tmp, "w+b")
        self.f.write(bytes(DATA_START))
        self.pos = DATA_START
        self.blocks: list[tuple[int, int, int, int, int]] = []
        self.crcs = array("I")
        self.by_digest: dict[tuple[int, bytes], list[int]] = {}
        self.files: list[tuple[int, int, int, int, bytes]] = []
        self.refs = array("I")
        self.file_index: dict[tuple, int] = {}
        self.source_files: dict[int, tuple[Storage, int]] = {}  # id(storage) -> file id
        self.written = 0                         # logical bytes processed

    # -- data ----------------------------------------------------------------------------
    def _compressor(self):
        """zstd contexts are not thread safe: one per worker thread."""
        cctx = getattr(self._local, "cctx", None)
        if cctx is None:
            cctx = self._local.cctx = zstd().ZstdCompressor(
                level=self.level, write_content_size=True, write_checksum=False,
                write_dict_id=False)
        return cctx

    def _worth_compressing(self, data: bytes) -> bool:
        """Cheap probe: already-compressed data (most game assets) skips the full attempt.
        Three 8 KiB samples at level 1 must save at least half the required gain."""
        if len(data) <= 3 * PROBE:
            return True
        mid = len(data) // 2
        sample = data[:PROBE] + data[mid:mid + PROBE] + data[-PROBE:]
        packed = self._probe_ctx().compress(sample)
        return len(packed) * 20000 <= len(sample) * (20000 - self.min_gain_bp)

    def _probe_ctx(self):
        cctx = getattr(self._local, "probe", None)
        if cctx is None:
            cctx = self._local.probe = zstd().ZstdCompressor(level=1)
        return cctx

    def _encode(self, data: bytes) -> tuple[bytes, int, list[int]]:
        """(stored bytes, codec, slice CRCs). Runs in worker threads."""
        crcs = slice_crcs(data)
        if not self.compress or not self._worth_compressing(data):
            return data, 0, crcs
        packed = self._compressor().compress(data)
        if len(packed) * 10000 <= len(data) * (10000 - self.min_gain_bp):
            return packed, 1, crcs
        return data, 0, crcs

    def _stored_equals(self, block_id: int, data: bytes) -> bool:
        off, stored, logical, codec, _first = self.blocks[block_id]
        self.f.seek(off)
        raw = self.f.read(stored)
        self.f.seek(self.pos)
        if codec == 1:
            raw = self._dctx.decompress(raw, max_output_size=logical)
        return raw == data

    def _store(self, pieces: list[bytes]) -> list[int]:
        """Block ids for a batch of chunks: identical existing blocks are reused (after a
        byte comparison), the new ones are hashed and encoded in parallel."""
        keys = [(len(p), d) for p, d in zip(pieces, self._map(_sha256, pieces))]
        ids: list[Optional[int]] = [None] * len(pieces)
        fresh: dict[tuple, int] = {}       # key -> first position in this batch
        todo = []
        for k, (piece, key) in enumerate(zip(pieces, keys)):
            for candidate in self.by_digest.get(key, ()):
                if self._stored_equals(candidate, piece):  # a digest match is not enough
                    ids[k] = candidate
                    break
            else:
                if key in fresh and pieces[fresh[key]] == piece:
                    continue  # same as an earlier chunk of this batch: resolved below
                fresh.setdefault(key, k)
                todo.append(k)
        encoded = dict(zip(todo, self._map(self._encode, [pieces[k] for k in todo])))
        for k in range(len(pieces)):
            if ids[k] is not None:
                continue
            if k not in encoded:
                ids[k] = ids[fresh[keys[k]]]
                continue
            payload, codec, crcs = encoded[k]
            start = _align(self.pos)
            if start != self.pos:
                self.f.write(bytes(start - self.pos))
            self.f.write(payload)
            self.pos = start + len(payload)
            ids[k] = len(self.blocks)
            self.blocks.append((start, len(payload), len(pieces[k]), codec, len(self.crcs)))
            self.crcs.extend(crcs)
            self.by_digest.setdefault(keys[k], []).append(ids[k])
        return ids

    def _map(self, fn, items: list) -> list:
        if self.pool is None or len(items) < 2:
            return [fn(x) for x in items]
        return list(self.pool.map(fn, items))

    def add_file(self, path: str, st: Storage,
                 progress: Optional[Callable[[int], None]] = None) -> int:
        """Store a file and return its NxFile id. The same unchanged file at the same path
        (any snapshot) keeps one record, as the format requires. The source is streamed:
        a multi-GB file never sits in memory."""
        seen = self.source_files.get(id(st))
        known = seen[1] if seen is not None and seen[0] is st else None
        if known is not None:  # the very same source object, already stored
            key = self._file_key(path, known)
            if key in self.file_index:
                self.written += st.size
                if progress:
                    progress(self.written)
                return self.file_index[key]
        log2 = self.chunk_log2
        stream = iter(_rechunk(st.chunks(), 1 << log2))
        head = next(stream, b"")
        if self.compress and self.compressed_chunk_log2 and head \
                and self._encode(head)[1] == 1:
            log2 = self.compressed_chunk_log2  # compressible: smaller frames read faster
            stream = iter(_rechunk(itertools.chain([head], stream), 1 << log2))
        else:
            stream = itertools.chain([head] if head else [], stream)
        first = len(self.refs)
        h = hashlib.sha256()
        batch: list[bytes] = []
        for piece in itertools.chain(stream, [None]):
            if piece is not None:
                h.update(piece)
                batch.append(piece)
            if batch and (piece is None or len(batch) >= self.batch):
                if self.should_cancel and self.should_cancel():
                    raise Cancelled()
                self.refs.extend(self._store(batch))
                self.written += sum(map(len, batch))
                batch = []
                if progress:
                    progress(self.written)
        size = sum(self.blocks[b][2] for b in self.refs[first:])
        if size != st.size:
            raise NxError(tr("Il file {name} è cambiato durante la conversione.", name=path))
        record = (size, first, len(self.refs) - first, log2, h.digest())
        key = (path, record[0], record[2], record[3], record[4])
        existing = self.file_index.get(key)
        if existing is not None and self._same_refs(existing, first):
            del self.refs[first:]
            fid = existing
        else:
            fid = len(self.files)
            self.files.append(record)
            self.file_index[key] = fid
        self.source_files[id(st)] = (st, fid)
        return fid

    def _file_key(self, path: str, fid: int) -> tuple:
        size, _first, count, log2, sha = self.files[fid]
        return (path, size, count, log2, sha)

    def _same_refs(self, fid: int, first: int) -> bool:
        _s, f_first, count, _l, _sha = self.files[fid]
        return self.refs[f_first:f_first + count] == self.refs[first:first + count]

    # -- metadata tail -------------------------------------------------------------------
    def _shutdown(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True)
            self.pool = None

    def finish(self, applications: list[NewApplication]) -> str:
        self._shutdown()
        try:
            media = _MediaTable()
            manifest, roots = self._manifest(applications)
            catalog = _build_catalog(applications, roots, media, self)
            media_bytes = media.serialize()
            return self._write_tail(media_bytes, catalog, manifest)
        except BaseException:
            self.abort()
            raise

    def abort(self) -> None:
        self._shutdown()
        try:
            self.f.close()
        finally:
            try:
                os.remove(self.tmp)
            except OSError:
                pass

    def _manifest(self, applications: list[NewApplication]) -> tuple[bytes, list[list[int]]]:
        """Directory tree of every snapshot; returns the Manifest and the root ids."""
        dirs: list[dict] = []  # each: {name: (kind, target)}

        def build(paths: dict[str, int]) -> int:
            root = len(dirs)
            dirs.append({})
            index = {(): root}
            for rel, fid in sorted(paths.items()):
                parts = [p.encode("utf-8") for p in rel.split("/") if p]
                for part in parts:
                    if not valid_name(part):
                        raise NxError(tr("Nome di file non ammesso in un archivio NX: {name}",
                                         name=rel))
                for depth in range(1, len(parts)):
                    key = tuple(parts[:depth])
                    if key not in index:
                        parent = dirs[index[key[:-1]]]
                        if key[-1] in parent:
                            raise NxError(tr("Nome di file non ammesso in un archivio NX: {name}",
                                             name=rel))
                        index[key] = len(dirs)
                        dirs.append({})
                        parent[key[-1]] = (1, index[key])
                parent = dirs[index[tuple(parts[:-1])]]
                if parts[-1] in parent:
                    raise NxError(tr("Nome di file non ammesso in un archivio NX: {name}",
                                     name=rel))
                parent[parts[-1]] = (2, fid)
            return root

        roots = [[build(s.tree) for s in app.snapshots] for app in applications]
        pool = bytearray()
        interned: dict[bytes, int] = {}
        entries = []
        dir_rows = []
        for d in dirs:
            dir_rows.append((len(entries), len(d)))
            for name in sorted(d):
                if name not in interned:
                    interned[name] = len(pool)
                    pool += name
                kind, target = d[name]
                entries.append((name, kind, target))
        n_dirs, n_files = len(dirs), len(self.files)
        n_entries, n_refs = len(entries), len(self.refs)
        dir_off = 96
        file_off = _align(dir_off + n_dirs * 16, 8) if n_files else 0
        cursor = (file_off + n_files * 64) if n_files else dir_off + n_dirs * 16
        entry_off = _align(cursor, 8) if n_entries else 0
        cursor = (entry_off + n_entries * 24) if n_entries else cursor
        ref_off = _align(cursor, 4) if n_refs else 0
        cursor = (ref_off + n_refs * 4) if n_refs else cursor
        pool_off = _align(cursor, 8)
        total = pool_off + len(pool)
        out = bytearray(total)
        struct.pack_into("<4sHHQIIIIQQQQQQ", out, 0, b"NXMF", 1, 96, total, n_dirs, n_files,
                         n_entries, n_refs, dir_off, file_off, entry_off, ref_off, pool_off,
                         len(pool))
        for i, (first, count) in enumerate(dir_rows):
            struct.pack_into("<IIII", out, dir_off + i * 16, first, count, 0, 0)
        for i, (size, first, count, log2, sha) in enumerate(self.files):
            _file.pack_into(out, file_off + i * 64, size, first, count, log2, 0, 0, sha,
                            bytes(12))
        for i, (name, kind, target) in enumerate(entries):
            _entry.pack_into(out, entry_off + i * 24, pool_off + interned[name], len(name),
                             target, kind, 0, 0, 0)
        if n_refs:
            refs = array("I", self.refs)
            if struct.pack("<I", 1) != struct.pack("=I", 1):  # pragma: no cover
                refs.byteswap()
            out[ref_off:ref_off + n_refs * 4] = refs.tobytes()
        out[pool_off:total] = pool
        self._dirs = dirs
        return bytes(out), roots

    def snapshot_nodes(self, root: int) -> tuple[list[tuple[bytes, Optional[tuple]]], int]:
        nodes = [(b"", None)]
        logical = 0
        stack = [(root, b"")]
        while stack:
            d, prefix = stack.pop()
            for name, (kind, target) in self._dirs[d].items():
                path = prefix + name
                if kind == 1:
                    nodes.append((path, None))
                    stack.append((target, path + b"/"))
                else:
                    size, _f, _c, _l, sha = self.files[target]
                    logical += size
                    nodes.append((path, (size, sha)))
        return nodes, logical

    def _write_tail(self, media: bytes, catalog: bytes, manifest: bytes) -> str:
        n = len(self.blocks)
        data_size = self.pos - DATA_START if n else 0
        index = bytearray(n * 24)
        for i, (off, stored, logical, codec, first) in enumerate(self.blocks):
            _block.pack_into(index, i * 24, off, stored, logical, codec, 0, 0, first)
        crcs = array("I", self.crcs)
        if struct.pack("<I", 1) != struct.pack("=I", 1):  # pragma: no cover
            crcs.byteswap()
        integrity = crcs.tobytes()
        cursor = self.pos if n else DATA_START
        layout = {}
        tail = bytearray()
        start = _align(cursor)
        parts = ([("media", media)] if media else []) + [("catalog", catalog),
                                                        ("manifest", manifest)]
        if n:
            parts += [("index", bytes(index)), ("integrity", integrity)]
        pos = start
        for name, blob in parts:
            pos = _align(pos)
            layout[name] = (pos, len(blob))
            tail += bytes(pos - start - len(tail)) + blob
            pos += len(blob)
        archive_size = pos
        self.f.seek(self.pos)
        self.f.write(bytes(start - self.pos) + tail)
        header = bytearray(HEADER_SIZE)
        media_off, media_size = layout.get("media", (0, 0))
        idx_off = layout["index"][0] if n else 0
        integ_off, integ_size = layout.get("integrity", (0, 0))
        _header.pack_into(header, 0, MAGIC, 0, 2, HEADER_SIZE, FEATURE_SEPARATE_PAGES, 0,
                          archive_size, layout["catalog"][0], len(catalog), media_off,
                          media_size, DATA_START if n else 0, data_size,
                          layout["manifest"][0], len(manifest), idx_off, n, integ_off,
                          integ_size, hashlib.sha256(catalog).digest(),
                          hashlib.sha256(manifest).digest(),
                          hashlib.sha256(bytes(index) + integrity).digest(), 0, CHECK,
                          bytes(24), 0)
        struct.pack_into("<I", header, 252, crc32c(bytes(header[:252])))
        page = bytes(header) + bytes(PAGE - HEADER_SIZE)
        self.f.seek(0)
        self.f.write(page + page)
        self.f.truncate(archive_size)
        self.f.flush()
        os.fsync(self.f.fileno())
        self.f.close()
        os.replace(self.tmp, self.path)
        return self.path


# -- catalog construction --------------------------------------------------------------

class _MediaTable:
    def __init__(self):
        self.records: list[tuple[int, bytes]] = []
        self.icons: dict[tuple[int, bytes], int] = {}

    def icon(self, data: Optional[bytes]) -> int:
        if not data:
            return NONE32
        mtype = MEDIA_PNG if data[:4] == b"\x89PNG" else MEDIA_JPEG
        key = (mtype, data)
        if key not in self.icons:  # identical icons share one record
            self.icons[key] = len(self.records)
            self.records.append((mtype, data))
        return self.icons[key]

    def owned(self, mtype: int, data: Optional[bytes]) -> int:
        if not data:
            return NONE32
        self.records.append((mtype, data))  # one owner each: never shared
        return len(self.records) - 1

    def serialize(self) -> bytes:
        if not self.records:
            return b""
        n = len(self.records)
        payload = _align(32 + n * 64)
        offsets = []
        cursor = payload
        for _t, data in self.records:
            cursor = _align(cursor)
            offsets.append(cursor)
            cursor += len(data)
        out = bytearray(cursor)
        struct.pack_into("<4sHHQIHHII", out, 0, b"NXMD", 1, 32, cursor, n, 64, 0, 32, payload)
        for i, ((mtype, data), off) in enumerate(zip(self.records, offsets)):
            if not 1 <= len(data) <= 64 << 20:
                raise NxError(tr("Dati media troppo grandi per un archivio NX."))
            _media_record.pack_into(out, 32 + i * 64, mtype, 0, 0, len(data), len(data), off,
                                    hashlib.sha256(data).digest(), 0)
            out[off:off + len(data)] = data
        return bytes(out)


class _Strings:
    def __init__(self):
        self.pool = bytearray()
        self.index: dict[bytes, int] = {}

    def ref(self, text: Optional[str], limit: int = 4096) -> tuple[int, int]:
        if not text:
            return NONE32, 0
        raw = text.encode("utf-8")[:limit]
        while raw:  # never cut a character in half
            try:
                raw.decode("utf-8")
                break
            except UnicodeDecodeError:
                raw = raw[:-1]
        raw = raw.replace(b"\x00", b"")
        if not raw:
            return NONE32, 0
        if raw not in self.index:
            self.index[raw] = len(self.pool)
            self.pool += raw
        return self.index[raw], len(raw)


def _snapshot_order(s: NewSnapshot) -> tuple:
    if s.executable:
        return (0, s.kind, s.version, s.content_id, s.build_id)
    return (1, s.version, s.content_id, s.build_id)


def _build_catalog(applications: list[NewApplication], roots: list[list[int]],
                   media: _MediaTable, writer: NxWriter) -> bytes:
    strings = _Strings()
    apps = sorted(zip(applications, roots), key=lambda a: (a[0].platform, a[0].application_id))
    app_rows, prog_rows, snap_rows, loc_rows, ext_rows = [], [], [], [], []
    identities = set()
    for app_i, (app, app_roots) in enumerate(apps):
        ordered = sorted(zip(app.snapshots, app_roots), key=lambda x: _snapshot_order(x[0]))
        executable = [x for x in ordered if x[0].executable]
        prog_index = len(prog_rows) if executable else NONE32
        first_snap = len(snap_rows)
        if executable:
            prog_rows.append((app.application_id, app_i, first_snap, len(executable)))
        # display metadata: highest-version non-DLC snapshot that has some
        candidates = [(s.version, first_snap + k) for k, (s, _r) in enumerate(ordered)
                      if s.kind != SNAP_DLC and s.meta]
        meta_snap = max(candidates)[1] if candidates else NONE32
        history = []
        for k, (s, root) in enumerate(ordered):
            index = first_snap + k
            pid = app.application_id if s.executable else NONE64
            content = s.content_id
            flags = 1 if any(s.build_id) else 0
            if not s.executable and s.addon_title_id:
                content = struct.pack("<Q", s.addon_title_id) + bytes(8)
                flags |= 2
            nodes, logical = writer.snapshot_nodes(root)
            identity = snapshot_identity(app.platform, app.application_id, pid, s.kind,
                                         s.version, content, nodes)
            if identity in identities:
                raise NxError(tr("Due versioni identiche dello stesso gioco: impossibile "
                                 "crearne l'archivio NX."))
            identities.add(identity)
            dv = strings.ref(s.display_version, 256)
            snap_rows.append((prog_index if s.executable else NONE32, s.kind, flags, s.version,
                              root, logical, dv, content, identity, s.build_id))
            if index != meta_snap and s.kind != SNAP_DLC and s.meta:
                history.append((index, s.meta))
        loc_first = len(loc_rows)
        title = publisher = (NONE32, 0)
        icon_i = nacp_i = NONE32
        if meta_snap != NONE32:
            meta = ordered[meta_snap - first_snap][0].meta
            title, publisher = strings.ref(meta.title), strings.ref(meta.publisher)
            icon_i = media.icon(meta.icon)
            nacp_i = media.owned(MEDIA_NACP, meta.nacp)
            for lang in sorted(meta.localized, key=str.encode):
                text, icon = meta.localized[lang]
                row = (strings.ref(lang, 63), strings.ref(text), media.icon(icon))
                if row[1][0] != NONE32 or row[2] != NONE32:
                    loc_rows.append(row)
        flags = (1 if any(s.kind == SNAP_BASE for s in app.snapshots) else 0) | \
                (2 if any(s.kind == SNAP_DLC for s in app.snapshots) else 0)
        app_rows.append((app.application_id, app.platform, flags,
                         prog_index if executable else NONE32, 1 if executable else 0,
                         first_snap, len(ordered), title, publisher, icon_i, loc_first,
                         len(loc_rows) - loc_first, nacp_i, meta_snap))
        if history:
            ext_rows.append((EXT_HISTORY, 0, 1, app_i, _history_payload(history, strings,
                                                                          media)))
    if not snap_rows:
        raise NxError(tr("Nessun contenuto da convertire."))

    counts = (len(app_rows), len(prog_rows), len(snap_rows), len(loc_rows), 0, len(ext_rows))
    offsets = []
    cursor = 128
    for count, size in zip(counts, (80, 64, 120, 24, 56, 32)):
        if count:
            cursor = _align(cursor, 8)
            offsets.append(cursor)
            cursor += count * size
        else:
            offsets.append(0)
    pool_off = _align(cursor, 8)
    payload_offsets = []
    cursor = pool_off + len(strings.pool)
    for row in ext_rows:
        cursor = _align(cursor, 8)
        payload_offsets.append(cursor)
        cursor += len(row[4])
    total = cursor
    if total > MAX_CATALOG:
        raise NxError(tr("Catalogo NX troppo grande."))
    out = bytearray(total)
    struct.pack_into("<4sHHQIIIIIIIIQQQQQQQQ", out, 0, b"NXCT", 1, 128, total, 0, *counts, 0,
                     *offsets, pool_off, len(strings.pool))
    for i, (aid, platform, flags, pfirst, pcount, sfirst, scount, title, publisher, icon_i,
            lfirst, lcount, nacp_i, meta_snap) in enumerate(app_rows):
        _application.pack_into(out, offsets[0] + i * 80, aid, platform, flags, 0, pfirst, pcount,
                               sfirst, scount, title[0], title[1], publisher[0], publisher[1],
                               icon_i, lfirst if lcount else 0, lcount, nacp_i, 0, 0, meta_snap,
                               bytes(8))
    for i, (pid, app_i, sfirst, scount) in enumerate(prog_rows):
        _program.pack_into(out, offsets[1] + i * 64, pid, app_i, 0, 0, 0, bytes(16), sfirst,
                           scount, NONE32, 0, bytes(16))
    for i, (prog, kind, flags, version, root, logical, dv, content, identity,
            build) in enumerate(snap_rows):
        _snapshot.pack_into(out, offsets[2] + i * 120, prog, kind, flags, 0, version, root,
                            logical, dv[0], dv[1], content, 0, 0, identity, build)
    for i, (lang, text, icon_i) in enumerate(loc_rows):
        struct.pack_into("<IIIIII", out, offsets[3] + i * 24, lang[0], lang[1], text[0],
                         text[1], icon_i, 0)
    for i, ((tid, flags, owner_kind, owner, payload), d_off) in enumerate(zip(ext_rows,
                                                                          payload_offsets)):
        struct.pack_into("<IIHHIQQ", out, offsets[5] + i * 32, tid, flags, owner_kind, 0, owner,
                         d_off, len(payload))
        out[d_off:d_off + len(payload)] = payload
    out[pool_off:pool_off + len(strings.pool)] = strings.pool
    return bytes(out)


def _history_payload(history: list[tuple[int, Meta]], strings: _Strings,
                     media: _MediaTable) -> bytes:
    rows, locs = [], []
    for index, meta in history:
        first = len(locs)
        for lang in sorted(meta.localized, key=str.encode):
            text, icon = meta.localized[lang]
            row = (strings.ref(lang, 63), strings.ref(text), media.icon(icon))
            if row[1][0] != NONE32 or row[2] != NONE32:
                locs.append(row)
        title, publisher = strings.ref(meta.title), strings.ref(meta.publisher)
        rows.append((index, title[0], title[1], publisher[0], publisher[1],
                     media.icon(meta.icon), media.owned(MEDIA_NACP, meta.nacp), first,
                     len(locs) - first, 0, 0, 0))
    out = bytearray(struct.pack("<4sHHIIII", b"NXSM", 1, 24, len(rows), len(locs), 0, 0))
    for row in rows:
        out += struct.pack("<12I", *row)
    for lang, text, icon_i in locs:
        out += struct.pack("<IIIIII", lang[0], lang[1], text[0], text[1], icon_i, 0)
    return bytes(out)
