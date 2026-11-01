"""NCA: Nintendo Content Archive. Header decryption, sections, update (BKTR) RomFS, and the
header rewrite needed to move an NCA from an NSP into an XCI."""

from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass
from typing import Optional

from . import crypto
from .fs import FormatError, Partition, RomFS, Storage, SubStorage
from .i18n import tr
from .keys import KeySet, KeysError

HEADER_SIZE = 0xC00
MEDIA = 0x200

CONTENT_TYPES = {0: "program", 1: "meta", 2: "control", 3: "manual", 4: "data", 5: "publicdata"}

ENC_NONE, ENC_XTS, ENC_CTR, ENC_BKTR = 1, 2, 3, 4
_ENC_ALIASES = {5: ENC_CTR, 6: ENC_BKTR}  # "skip layer hash" variants


class NcaError(FormatError):
    pass


@dataclass
class Section:
    index: int
    offset: int          # absolute, inside the NCA
    size: int
    fs_type: int         # 0 RomFS, 1 PFS0
    hash_type: int
    encryption: int
    header: bytes        # 0x200 fs header

    @property
    def upper_ctr(self) -> bytes:
        return self.header[0x140:0x148][::-1]

    def data_region(self) -> tuple[int, int]:
        """(offset, size) of the payload inside the section: PFS0 or RomFS level 6."""
        h = self.header
        if h[0x8:0xC] == b"IVFC":
            offset, size = struct.unpack_from("<QQ", h, 0x8 + 0x10 + 5 * 0x18)
            return offset, size
        offset, size = struct.unpack_from("<QQ", h, 0x8 + 0x38)
        return offset, size

    @property
    def is_sparse(self) -> bool:
        return struct.unpack_from("<Q", self.header, 0x148 + 8)[0] != 0

    @property
    def is_compressed(self) -> bool:
        return struct.unpack_from("<Q", self.header, 0x178 + 8)[0] != 0


class CtrStorage(Storage):
    """AES-CTR decryption of one section; offsets are relative to the section."""

    def __init__(self, raw: Storage, section: Section, key: bytes):
        self.raw, self.base, self.size = raw, section.offset, section.size
        self.key, self.upper = key, section.upper_ctr

    def read(self, offset: int, size: int) -> bytes:
        size = max(0, min(size, self.size - offset))
        if not size:
            return b""
        pos = self.base + offset
        aligned = pos & ~0xF
        data = self.raw.read(aligned, size + pos - aligned)
        return crypto.ctr_crypt(self.key, self.upper, aligned, data)[pos - aligned:]


class CtrExStorage(Storage):
    """AES-CTR with a per-subsection counter (the patch section of an update)."""

    def __init__(self, raw: Storage, section: Section, key: bytes,
                 subsections: list[tuple[int, int]], end: int):
        """subsections: (offset, counter) covering [first offset, end); outside that range
        (the BKTR tables at the end of the section) the section's own counter applies."""
        self.raw, self.base, self.size = raw, section.offset, section.size
        self.key, self.upper = key, section.upper_ctr
        self.starts = [s for s, _ in subsections] + [end]
        self.ctrs = [self._upper(c) for _, c in subsections] + [self.upper]

    def _upper(self, ctr: int) -> bytes:
        return self.upper[:4] + ctr.to_bytes(4, "big")

    def read(self, offset: int, size: int) -> bytes:
        size = max(0, min(size, self.size - offset))
        out = bytearray()
        end = offset + size
        while offset < end:
            i = bisect.bisect_right(self.starts, offset) - 1
            upper = self.ctrs[i] if i >= 0 else self.upper
            stop = self.starts[i + 1] if i + 1 < len(self.starts) else end
            n = min(end, stop) - offset
            pos = self.base + offset
            aligned = pos & ~0xF
            data = self.raw.read(aligned, n + pos - aligned)
            out += crypto.ctr_crypt(self.key, upper, aligned, data)[pos - aligned:]
            offset += n
        return bytes(out)


class IndirectStorage(Storage):
    """Virtual (patched) section: each range comes from the base game or from the patch."""

    def __init__(self, size: int, entries: list[tuple[int, int, int]], sources: list[Storage]):
        self.size = size
        self.virt = [e[0] for e in entries]
        self.entries = entries
        self.sources = sources

    def read(self, offset: int, size: int) -> bytes:
        size = max(0, min(size, self.size - offset))
        out = bytearray()
        end = offset + size
        while offset < end:
            i = bisect.bisect_right(self.virt, offset) - 1
            if i < 0:
                raise NcaError(tr("Tabella di rilocazione dell'aggiornamento non valida."))
            virt, phys, src = self.entries[i]
            stop = self.virt[i + 1] if i + 1 < len(self.virt) else self.size
            n = min(end, stop) - offset
            source = self.sources[src] if src < len(self.sources) else None
            if source is None:
                raise NcaError(tr("L'aggiornamento richiede il gioco base per ricostruire la "
                                  "RomFS: aggiungi anche il gioco base."))
            out += source.read(phys + offset - virt, n)
            offset += n
        return bytes(out)


def _bucket_tree(data: bytes, entry_fmt: str, name: str = "") -> tuple[list[tuple], int]:
    """(entries, end offset) of a BKTR bucket tree: a 0x4000 header, then 0x4000-byte
    buckets. Counts that do not fit mean wrong keys or a damaged file."""
    esize = struct.calcsize(entry_fmt)
    bad = NcaError(tr("{name}: tabelle dell'aggiornamento illeggibili (chiavi errate o file "
                      "danneggiato).", name=name))
    if len(data) < 0x10:
        raise bad
    _, buckets, total = struct.unpack_from("<IIQ", data)
    if 0x4000 * (buckets + 1) > len(data):
        raise bad
    out = []
    for b in range(buckets):
        base = 0x4000 * (b + 1)
        _, count, _end = struct.unpack_from("<IIQ", data, base)
        if 0x10 + count * esize > 0x4000:
            raise bad
        for i in range(count):
            out.append(struct.unpack_from(entry_fmt, data, base + 0x10 + i * esize))
    return out, total


class Nca:
    def __init__(self, raw: Storage, keys: KeySet, tickets: Optional[dict[bytes, bytes]] = None,
                 name: str = ""):
        """raw: the encrypted NCA. tickets: rights id -> encrypted title key (from .tik)."""
        self.raw = raw
        self.keys = keys
        self.name = name
        if raw.size < HEADER_SIZE:
            raise NcaError(tr("{name}: file NCA troppo piccolo.", name=name))
        self.header = crypto.xts_decrypt(keys.header_key, raw.read(0, HEADER_SIZE), 0)
        magic = self.header[0x200:0x204]
        if magic != b"NCA3":
            if magic in (b"NCA2", b"NCA0"):
                raise NcaError(tr("{name}: formato {magic} (molto vecchio) non supportato.",
                                  name=name, magic=magic.decode()))
            raise NcaError(tr("{name}: intestazione NCA non valida. header_key errata o file "
                              "danneggiato/compresso (NCZ).", name=name))
        h = self.header
        self.distribution = h[0x204]
        self.content_type = CONTENT_TYPES.get(h[0x205], str(h[0x205]))
        self.key_area_index = h[0x207]
        self.content_size, self.title_id = struct.unpack_from("<QQ", h, 0x208)
        generation = max(h[0x206], h[0x220])
        self.key_generation = generation - 1 if generation else 0
        self.rights_id = h[0x230:0x240]
        self.sections: list[Section] = []
        for i in range(4):
            start, end = struct.unpack_from("<II", h, 0x240 + 0x10 * i)
            if not end:
                continue
            fsh = h[0x400 + 0x200 * i:0x600 + 0x200 * i]
            enc = _ENC_ALIASES.get(fsh[4], fsh[4])
            self.sections.append(Section(i, start * MEDIA, (end - start) * MEDIA, fsh[2],
                                         fsh[3], enc, fsh))
        self._tickets = tickets or {}
        self._title_key: Optional[bytes] = None

    @property
    def has_rights_id(self) -> bool:
        return any(self.rights_id)

    @property
    def title_key(self) -> bytes:
        """Decrypted title key (only for NCAs with a rights id)."""
        if self._title_key is None:
            rid = bytes(self.rights_id)
            if rid in self._tickets:
                self._title_key = crypto.ecb_decrypt(self.keys.titlekek(self.key_generation),
                                                     self._tickets[rid])
            elif rid in self.keys.title_keys:
                self._title_key = self.keys.title_keys[rid]
            else:
                raise KeysError(tr("{name}: manca il ticket (.tik) con la title key "
                                   "{rights_id}. Usa l'NSP completo di ticket o aggiungila a "
                                   "title.keys.", name=self.name, rights_id=rid.hex().upper()))
        return self._title_key

    def key_area(self) -> list[bytes]:
        kak = self.keys.key_area_key(self.key_area_index, self.key_generation)
        plain = crypto.ecb_decrypt(kak, self.header[0x300:0x340])
        return [plain[i:i + 16] for i in range(0, 0x40, 16)]

    def section_key(self) -> bytes:
        return self.title_key if self.has_rights_id else self.key_area()[2]

    # -- sections ------------------------------------------------------------------
    def section(self, index: int) -> Section:
        for s in self.sections:
            if s.index == index:
                return s
        raise NcaError(tr("{name}: sezione {index} assente.", name=self.name, index=index))

    def _check(self, s: Section) -> None:
        if s.is_compressed:
            raise NcaError(tr("{name}: NCA compresso (formato dei giochi più recenti) non ancora "
                              "supportato per l'estrazione.", name=self.name))
        if s.is_sparse:
            raise NcaError(tr("{name}: NCA sparse non supportato per l'estrazione.",
                              name=self.name))

    def open_section(self, s: Section) -> Storage:
        """Decrypted section (not for update sections: see patched_section)."""
        self._check(s)
        if s.encryption == ENC_NONE:
            return SubStorage(self.raw, s.offset, s.size)
        if s.encryption == ENC_CTR:
            return CtrStorage(self.raw, s, self.section_key())
        if s.encryption == ENC_BKTR:
            raise NcaError(tr("{name}: sezione di aggiornamento: serve anche il gioco base.",
                              name=self.name))
        raise NcaError(tr("{name}: cifratura della sezione non supportata ({kind}).",
                          name=self.name, kind=s.encryption))

    def patched_section(self, s: Section, base: Optional[Storage]) -> Storage:
        """Virtual section of an update: base RomFS section + the update's patch data."""
        self._check(s)
        key = self.section_key()
        plain = CtrStorage(self.raw, s, key)
        rel_off, rel_size = struct.unpack_from("<QQ", s.header, 0x100)
        sub_off, sub_size = struct.unpack_from("<QQ", s.header, 0x120)
        if s.header[0x110:0x114] != b"BKTR" or s.header[0x130:0x134] != b"BKTR":
            raise NcaError(tr("{name}: intestazione BKTR non valida.", name=self.name))
        # Both tables are encrypted with the section's own counter, not the subsections'.
        sub_entries, sub_end = _bucket_tree(plain.read(sub_off, sub_size), "<QII", self.name)
        patch = CtrExStorage(self.raw, s, key, [(o, c) for o, _r, c in sub_entries], sub_end)
        entries, virt_size = _bucket_tree(plain.read(rel_off, rel_size), "<QQI", self.name)
        return IndirectStorage(virt_size, [(v, p, src) for v, p, src in entries], [base, patch])

    def payload(self, s: Section, storage: Storage) -> Storage:
        offset, size = s.data_region()
        return SubStorage(storage, offset, size)

    def pfs0(self, s: Section) -> Partition:
        return Partition(self.payload(s, self.open_section(s)))

    def romfs_section(self) -> Optional[Section]:
        return next((s for s in self.sections if s.fs_type == 0), None)

    def exefs_section(self) -> Optional[Section]:
        for s in self.sections:
            if s.fs_type == 1 and s.encryption != ENC_BKTR:
                try:
                    if "main.npdm" in self.pfs0(s).by_name:
                        return s
                except FormatError:
                    continue
        return None

    def romfs(self, base: Optional["Nca"] = None) -> RomFS:
        s = self.romfs_section()
        if s is None:
            raise NcaError(tr("{name}: nessuna RomFS.", name=self.name))
        if s.encryption == ENC_BKTR:
            bs = base.romfs_section() if base is not None else None
            if bs is None:
                raise NcaError(tr("L'aggiornamento richiede il gioco base per ricostruire la "
                                  "RomFS: aggiungi anche il gioco base."))
            virtual = self.patched_section(s, base.open_section(bs))
            return RomFS(self.payload(s, virtual))
        return RomFS(self.payload(s, self.open_section(s)))

    # -- XCI conversion ------------------------------------------------------------
    def gamecard_header(self) -> bytes:
        """Encrypted header for an XCI: game card distribution, title key moved into the
        key area (the XCI has no tickets), rights id cleared."""
        h = bytearray(self.header)
        h[0x204] = 1
        if self.has_rights_id:
            title_key = self.title_key
            kak = self.keys.key_area_key(self.key_area_index, self.key_generation)
            h[0x230:0x240] = bytes(16)
            h[0x300:0x340] = crypto.ecb_encrypt(kak, bytes(0x20) + title_key + bytes(0x10))
        return crypto.xts_encrypt(self.keys.header_key, bytes(h), 0)
