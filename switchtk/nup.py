"""NUP (NX Package, SPEC 1.0-draft): container for unencrypted ExeFS/RomFS trees of a base
game, its updates and its add-ons, with zstd-compressed files and SHA-256 per file.

Layout: 0x80-byte superblock, table of contents (packages, directories, files, children,
string pool), then the blob region with 16-byte-aligned file extents and the per-package
meta blocks (TLV records). All integers little endian.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field
from typing import BinaryIO, Callable, Iterator, Optional

from .fs import CHUNK, Cancelled, FileStorage, FormatError, Storage
from .i18n import tr

MAGIC = b"NUP\x01"
TOC_MAGIC = b"TOC\x01"
HEADER_SIZE = 0x80
NO_DIR = 0xFFFFFFFF
ALIGN = 16
CODEC_RAW, CODEC_ZSTD = 0, 1
KIND_BASE, KIND_UPDATE, KIND_ADDON = 0, 1, 2
FLAG_STANDALONE_UPDATE = 0x01

TLV_NPDM, TLV_NACP, TLV_ICON, TLV_DISPLAY_VERSION = 0x1, 0x2, 0x3, 0x4
TLV_TITLE_NAME, TLV_PUBLISHER, TLV_BUILD_ID, TLV_SOURCE = 0x5, 0x6, 0x7, 0x8
TLV_LOCALIZED_TITLES, TLV_LOCALIZED_ICONS = 0x9, 0xA

# NACP title index -> BCP-47 tag (SPEC appendix B)
NACP_LANGS = ("en-us", "en-gb", "ja", "fr", "de", "es-419", "es", "it", "nl", "fr-ca", "pt",
              "ru", "ko", "zh-hant", "zh-hans", "pt-br")

_super = struct.Struct("<4sHHQQ32sQQII16s32s")
_toc_head = struct.Struct("<4sIQQQQQ")
_pkg = struct.Struct("<QIIIIBBHIQI20x")
_dir = struct.Struct("<IIIIQ")
_file = struct.Struct("<IIQQQ32sBBH12x")
_tlv = struct.Struct("<HHI")

SMALL_FILE = 32 << 20   # compressed in memory below this size, streamed above


class NupError(FormatError):
    pass


def _align(n: int) -> int:
    return (n + ALIGN - 1) & ~(ALIGN - 1)


def _zstd():
    try:
        import zstandard
    except ImportError as ex:  # pragma: no cover - dependency of the program
        raise NupError(tr("Manca il modulo zstandard, necessario per i file NUP.")) from ex
    return zstandard


def zstd_version() -> str:
    try:
        return ".".join(map(str, _zstd().ZSTD_VERSION))
    except NupError:
        return ""


# -- meta blocks ----------------------------------------------------------------------

def parse_tlvs(block: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    pos = 0
    while pos + 8 <= len(block):
        kind, _r, size = _tlv.unpack_from(block, pos)
        if pos + 8 + size > len(block):
            break
        out.setdefault(kind, block[pos + 8:pos + 8 + size])
        pos += 8 + size
    return out


def build_tlvs(records: dict[int, bytes]) -> bytes:
    return b"".join(_tlv.pack(k, 0, len(v)) + v for k, v in sorted(records.items()))


def decode_localized(payload: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    pos = 0
    while pos < len(payload):
        n = payload[pos]
        lang = payload[pos + 1:pos + 1 + n].decode("ascii", "replace").lower()
        pos += 1 + n
        if pos + 4 > len(payload):
            break
        size = struct.unpack_from("<I", payload, pos)[0]
        pos += 4
        if pos + size > len(payload):
            break
        out[lang] = payload[pos:pos + size]
        pos += size
    return out


def encode_localized(entries: dict[str, bytes]) -> bytes:
    out = bytearray()
    for lang in sorted(entries):
        tag = lang.lower().encode("ascii")
        out += bytes([len(tag)]) + tag + struct.pack("<I", len(entries[lang])) + entries[lang]
    return bytes(out)


# -- reading --------------------------------------------------------------------------

@dataclass
class Package:
    index: int
    title_id: int
    kind: int
    version: int
    flags: int
    root: int
    exefs: int
    romfs: int
    meta: dict[int, bytes]


class Entry(Storage):
    """One file of a NUP. chunks() streams (decompressing and checking the SHA-256);
    read() decompresses the whole file, for the small ones (NACP, icons)."""

    def __init__(self, nup: "NupFile", name: str, size: int, sha256: bytes, offset: int,
                 stored: int, codec: int):
        self.nup, self.name, self.size, self.sha256 = nup, name, size, sha256
        self.offset, self.stored, self.codec = offset, stored, codec
        self._data: Optional[bytes] = None

    def chunks(self, offset: int = 0, size: Optional[int] = None,
               chunk: int = CHUNK) -> Iterator[bytes]:
        if offset or size is not None:
            yield from super().chunks(offset, size, chunk)
            return
        raw = FileStorage(self.nup.f, self.offset, self.stored)
        h = hashlib.sha256()
        done = 0
        if self.codec == CODEC_RAW:
            for piece in raw.chunks(0, None, chunk):
                h.update(piece)
                done += len(piece)
                yield piece
        else:
            dobj = _zstd().ZstdDecompressor().decompressobj()
            for piece in raw.chunks(0, None, chunk):
                out = dobj.decompress(piece)
                done += len(out)
                if done > self.size:
                    break
                if out:
                    h.update(out)
                    yield out
        if done != self.size or h.digest() != self.sha256:
            raise NupError(tr("{name}: file danneggiato nel NUP (impronta SHA-256 diversa).",
                              name=self.name))

    def read(self, offset: int, size: int) -> bytes:
        if self._data is None:
            self._data = b"".join(self.chunks())
        return self._data[offset:offset + size]


class NupFile:
    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "rb")
        try:
            self._parse()
        except (struct.error, IndexError, UnicodeDecodeError) as ex:
            self.f.close()
            raise NupError(tr("{name}: file NUP non valido.",
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

    def _bad(self, why: str) -> NupError:
        return NupError(tr("{name}: file NUP non valido ({why}).",
                           name=os.path.basename(self.path), why=why))

    def _parse(self) -> None:
        raw = FileStorage(self.f)
        head = raw.read(0, HEADER_SIZE)
        if len(head) < HEADER_SIZE or head[:3] != MAGIC[:3]:
            raise self._bad("magic")
        if head[3] != 1:
            raise NupError(tr("{name}: versione del formato NUP non supportata ({version}).",
                              name=os.path.basename(self.path), version=head[3]))
        (_m, header_size, _hv, toc_off, toc_size, toc_sha, blob_off, file_size, pkg_count,
         flags, _uuid, _res) = _super.unpack(head)
        if header_size < HEADER_SIZE or toc_off != header_size or flags:
            raise self._bad("superblock")
        if file_size != raw.size:
            raise self._bad(tr("file troncato"))
        if blob_off < toc_off + toc_size or blob_off > file_size or not pkg_count:
            raise self._bad("superblock")
        toc = raw.read(toc_off, toc_size)
        if hashlib.sha256(toc).digest() != toc_sha:
            raise self._bad(tr("indice danneggiato"))
        magic, count, ndirs, nfiles, pool_off, pool_size, _r = _toc_head.unpack_from(toc)
        if magic != TOC_MAGIC or count != pkg_count:
            raise self._bad("TOC")
        pos = _toc_head.size
        nodes_end = pos + count * _pkg.size + ndirs * _dir.size + nfiles * _file.size
        if nodes_end > pool_off or pool_off + pool_size > len(toc) or (pool_off - nodes_end) % 4:
            raise self._bad("TOC")
        pool = toc[pool_off:pool_off + pool_size]

        def name(off: int, n: int) -> str:
            if off + n > len(pool):
                raise self._bad("TOC")
            return pool[off:off + n].decode("utf-8", "replace")

        self.packages: list[Package] = []
        for i in range(count):
            (tid, root, exefs, romfs, version, kind, pflags, _a, _b, meta_off,
             meta_size) = _pkg.unpack_from(toc, pos)
            pos += _pkg.size
            if meta_size and meta_off + meta_size > file_size:
                raise self._bad("meta")
            meta = parse_tlvs(raw.read(meta_off, meta_size)) if meta_size else {}
            for d in (root, exefs, romfs):
                if d != NO_DIR and d >= ndirs:
                    raise self._bad("TOC")
            self.packages.append(Package(i, tid, kind, version, pflags, root, exefs, romfs,
                                         meta))
        self.ndirs = ndirs
        self.dirs: list[tuple[str, int, int]] = []
        for _ in range(ndirs):
            off, n, first, kids, _r = _dir.unpack_from(toc, pos)
            pos += _dir.size
            self.dirs.append((name(off, n), first, kids))
        self.files: list[tuple] = []
        for _ in range(nfiles):
            off, n, size, eoff, esize, sha, codec, cflags, _r = _file.unpack_from(toc, pos)
            pos += _file.size
            if codec not in (CODEC_RAW, CODEC_ZSTD) or cflags:
                raise NupError(tr("{name}: compressione non supportata nel NUP.",
                                  name=os.path.basename(self.path)))
            if size and (eoff < blob_off or eoff + esize > file_size or not esize):
                raise self._bad("extent")
            self.files.append((name(off, n), size, sha, eoff, esize, codec))
        nslots = (pool_off - pos) // 4
        self.children = struct.unpack_from(f"<{nslots}I", toc, pos)
        for _n, first, kids in self.dirs:
            if first + kids > nslots:
                raise self._bad("TOC")

    def walk(self, dir_index: int) -> Iterator[tuple[str, int]]:
        """(relative path, file index) of every file below a directory."""
        if dir_index == NO_DIR:
            return
        stack = [(dir_index, "", 0)]
        while stack:
            d, prefix, depth = stack.pop()
            if depth > 256:
                raise self._bad("TOC")
            _name, first, kids = self.dirs[d]
            for node in self.children[first:first + kids]:
                if node < self.ndirs:
                    stack.append((node, prefix + _safe(self.dirs[node][0]) + "/", depth + 1))
                elif node - self.ndirs < len(self.files):
                    yield prefix + _safe(self.files[node - self.ndirs][0]), node - self.ndirs
                else:
                    raise self._bad("TOC")

    def open(self, index: int) -> Entry:
        name, size, sha, eoff, esize, codec = self.files[index]
        return Entry(self, name, size, sha, eoff, esize, codec)

    def tree(self, pkg: Package, which: str) -> dict[str, Entry]:
        d = pkg.exefs if which == "exefs" else pkg.romfs
        return {path: self.open(i) for path, i in self.walk(d)}

    def verify(self, progress: Optional[Callable[[int, int], None]] = None,
               should_cancel: Optional[Callable[[], bool]] = None) -> list[tuple[str, str]]:
        """SHA-256 of every file: [(package/path, ok|mismatch)]."""
        items = []
        for pkg in self.packages:
            label = f"{pkg.title_id:016X}"
            items += [(f"{label}/{p}", i) for p, i in self.walk(pkg.root)]
        total = sum(self.files[i][1] for _p, i in items)
        done, out = 0, []
        for path, i in items:
            entry = self.open(i)
            try:
                for piece in entry.chunks():
                    if should_cancel and should_cancel():
                        raise Cancelled()
                    done += len(piece)
                    if progress:
                        progress(done, total)
                out.append((path, "ok"))
            except NupError:
                out.append((path, "mismatch"))
            except Exception as ex:  # zstd errors on damaged data
                if type(ex).__name__ != "ZstdError":
                    raise
                out.append((path, "mismatch"))
        return out


def _safe(name: str) -> str:
    """File names are labels, not paths (SPEC 6.1): no separators or dot-dot."""
    name = name.replace("/", "_").replace("\\", "_")
    return "_" if name in ("", ".", "..") else name


def is_nup(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(3) == MAGIC[:3]
    except OSError:
        return False


# -- writing --------------------------------------------------------------------------

@dataclass
class NewPackage:
    kind: int
    title_id: int
    version: int
    flags: int = 0
    exefs: dict[str, Storage] = field(default_factory=dict)
    romfs: dict[str, Storage] = field(default_factory=dict)
    meta: dict[int, bytes] = field(default_factory=dict)


class _Dir:
    def __init__(self, name: bytes):
        self.name = name
        self.dirs: dict[bytes, "_Dir"] = {}
        self.files: dict[bytes, int] = {}
        self.id = 0

    def child(self, name: bytes) -> "_Dir":
        if name not in self.dirs:
            self.dirs[name] = _Dir(name)
        return self.dirs[name]


def write_nup(path: str, packages: list[NewPackage], level: int = 3,
              progress: Optional[Callable[[int, int, str], None]] = None,
              should_cancel: Optional[Callable[[], bool]] = None) -> str:
    """Write the packages to path (through a temporary .part file). Returns path."""
    zstd = _zstd()
    cctx = zstd.ZstdCompressor(level=level, write_content_size=True)

    # Trees: package root -> exefs/, romfs/ -> files. File records in insertion order.
    sources: list[tuple[str, Storage]] = []
    roots = []
    for pkg in packages:
        root = _Dir(b"")
        for top, files in (("exefs", pkg.exefs), ("romfs", pkg.romfs)):
            if not files:
                continue
            base = root.child(top.encode())
            for rel, st in files.items():
                parts = [p.encode("utf-8") for p in rel.split("/") if p]
                d = base
                for p in parts[:-1]:
                    d = d.child(p)
                d.files[parts[-1]] = len(sources)
                sources.append((f"{top}/{rel}", st))
        roots.append(root)

    dirs: list[_Dir] = []
    queue = list(roots)
    while queue:  # breadth first: ids for every directory
        d = queue.pop(0)
        d.id = len(dirs)
        dirs.append(d)
        queue += [d.dirs[k] for k in sorted(d.dirs)]
    ndirs = len(dirs)
    children: list[int] = []
    dir_slots = []
    for d in dirs:
        named = [(k, v.id) for k, v in d.dirs.items()] + \
                [(k, ndirs + i) for k, i in d.files.items()]
        dir_slots.append((len(children), len(named)))
        children += [node for _k, node in sorted(named)]
    file_names: list[bytes] = [b""] * len(sources)
    for d in dirs:
        for k, i in d.files.items():
            file_names[i] = k

    pool = bytearray()
    interned: dict[bytes, int] = {}

    def intern(s: bytes) -> int:
        if s not in interned:
            interned[s] = len(pool)
            pool.extend(s)
        return interned[s]

    for d in dirs:
        intern(d.name)
    for n in file_names:
        intern(n)

    pool_off = (_toc_head.size + len(packages) * _pkg.size + ndirs * _dir.size
                + len(sources) * _file.size + len(children) * 4)
    toc_size = _align(pool_off + len(pool))
    blob_off = HEADER_SIZE + toc_size

    total = sum(st.size for _n, st in sources)
    done = 0
    records = []
    seen: dict[bytes, tuple[int, int, int]] = {}
    tmp = path + ".part"
    try:
        with open(tmp, "w+b") as out:
            out.write(bytes(blob_off))
            for rel, st in sources:
                if should_cancel and should_cancel():
                    raise Cancelled()
                rec = _write_extent(out, st, cctx, zstd, seen, should_cancel)
                records.append(rec)
                done += st.size
                if progress:
                    progress(done, total, rel)
            meta_pos = []
            for pkg in packages:
                block = build_tlvs(pkg.meta)
                start = _pad(out)
                out.write(block)
                meta_pos.append((start if block else 0, len(block)))
            file_size = out.tell()

            toc = bytearray(toc_size)
            _toc_head.pack_into(toc, 0, TOC_MAGIC, len(packages), ndirs, len(sources),
                                pool_off, len(pool), 0)
            pos = _toc_head.size
            for pkg, root, (moff, msize) in zip(packages, roots, meta_pos):
                exefs = root.dirs[b"exefs"].id if b"exefs" in root.dirs else NO_DIR
                romfs = root.dirs[b"romfs"].id if b"romfs" in root.dirs else NO_DIR
                _pkg.pack_into(toc, pos, pkg.title_id, root.id, exefs, romfs, pkg.version,
                               pkg.kind, pkg.flags, 0, 0, moff, msize)
                pos += _pkg.size
            for d, (first, count) in zip(dirs, dir_slots):
                _dir.pack_into(toc, pos, interned[d.name], len(d.name), first, count, 0)
                pos += _dir.size
            for n, (size, sha, eoff, esize, codec) in zip(file_names, records):
                _file.pack_into(toc, pos, interned[n], len(n), size, eoff, esize, sha, codec, 0, 0)
                pos += _file.size
            struct.pack_into(f"<{len(children)}I", toc, pos, *children)
            toc[pool_off:pool_off + len(pool)] = pool

            uuid = hashlib.sha256(b"".join(struct.pack("<QIB", p.title_id, p.version, p.kind)
                                           for p in packages)).digest()[:16]
            head = _super.pack(MAGIC, HEADER_SIZE, 1, HEADER_SIZE, toc_size,
                               hashlib.sha256(toc).digest(), blob_off, file_size,
                               len(packages), 0, uuid, bytes(32))
            out.seek(0)
            out.write(head + bytes(toc))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    if progress:
        progress(total, total, "")
    return path


def _pad(out: BinaryIO) -> int:
    pos = out.tell()
    start = _align(pos)
    out.write(bytes(start - pos))
    return start


def _write_extent(out: BinaryIO, st: Storage, cctx, zstd, seen: dict,
                  should_cancel) -> tuple[int, bytes, int, int, int]:
    """(size, sha256, extent offset, extent size, codec); identical files share one extent."""
    size = st.size
    if not size:
        return 0, hashlib.sha256(b"").digest(), 0, 0, CODEC_RAW
    start = _pad(out)
    if size <= SMALL_FILE:
        data = b"".join(st.chunks())
        sha = hashlib.sha256(data).digest()
        if sha in seen:
            out.seek(start)
            out.truncate()
            return (size, sha) + seen[sha]
        packed = cctx.compress(data)
        codec = CODEC_ZSTD if len(packed) < size else CODEC_RAW
        out.write(packed if codec == CODEC_ZSTD else data)
    else:
        h = hashlib.sha256()
        writer = cctx.stream_writer(out, size=size, closefd=False)
        for piece in st.chunks():
            if should_cancel and should_cancel():
                raise Cancelled()
            h.update(piece)
            writer.write(piece)
        writer.flush(zstd.FLUSH_FRAME)
        sha = h.digest()
        codec = CODEC_ZSTD
        if sha in seen or out.tell() - start >= size:
            out.seek(start)
            out.truncate()
            if sha in seen:
                return (size, sha) + seen[sha]
            codec = CODEC_RAW
            for piece in st.chunks():
                out.write(piece)
    seen[sha] = (start, out.tell() - start, codec)
    return (size, sha) + seen[sha]
