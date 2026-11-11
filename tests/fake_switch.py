"""Builds small but structurally real Switch files for the tests, with random keys:
prod.keys, NCAs (PFS0/RomFS sections, AES-CTR, title keys), tickets, NSPs and update
NCAs whose RomFS is a BKTR patch over the base game."""

from __future__ import annotations

import base64
import os
import struct
import zlib

from switchtk import crypto
from switchtk.fs import build_partition

GEN = 3  # key generation used by the fake titles (NCA byte 0x220 = GEN + 1)


def make_keys(path: str) -> dict[str, bytes]:
    keys = {"header_key": os.urandom(32)}
    for g in range(GEN + 1):
        keys[f"key_area_key_application_{g:02x}"] = os.urandom(16)
        keys[f"titlekek_{g:02x}"] = os.urandom(16)
    with open(path, "w") as f:
        for name, value in keys.items():
            f.write(f"{name} = {value.hex()}\n")
    return keys


# -- RomFS ----------------------------------------------------------------------------

def build_romfs(files: dict[str, bytes]) -> bytes:
    """RomFS image (directory/file tables + data) for {"a/b.bin": data}."""
    dirs = {"": {"children": [], "files": []}}
    for path in sorted(files):
        parts = path.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if d not in dirs:
                dirs[d] = {"children": [], "files": []}
                dirs["/".join(parts[:i - 1])]["children"].append(d)
        dirs["/".join(parts[:-1])]["files"].append(path)

    def align4(n):
        return (n + 3) & ~3

    dir_off, off = {}, 0
    order = []
    stack = [""]
    while stack:  # breadth-ish order, root first
        d = stack.pop(0)
        order.append(d)
        dir_off[d] = off
        off += 0x18 + align4(len(d.rsplit("/", 1)[-1].encode()) if d else 0)
        stack += dirs[d]["children"]
    file_off, off = {}, 0
    for d in order:
        for p in dirs[d]["files"]:
            file_off[p] = off
            off += 0x20 + align4(len(p.rsplit("/", 1)[-1].encode()))

    data, data_off = bytearray(), {}
    for p in sorted(files):
        data += bytes(-len(data) % 0x10)
        data_off[p] = len(data)
        data += files[p]

    none = 0xFFFFFFFF
    dtab = bytearray()
    for d in order:
        parent = dir_off["/".join(d.split("/")[:-1])] if d else 0
        sibs = dirs["/".join(d.split("/")[:-1])]["children"] if d else [d]
        i = sibs.index(d)
        sibling = dir_off[sibs[i + 1]] if i + 1 < len(sibs) else none
        child = dir_off[dirs[d]["children"][0]] if dirs[d]["children"] else none
        fil = file_off[dirs[d]["files"][0]] if dirs[d]["files"] else none
        name = d.rsplit("/", 1)[-1].encode() if d else b""
        dtab += struct.pack("<6I", parent, sibling, child, fil, none, len(name))
        dtab += name.ljust(align4(len(name)), b"\0")
    ftab = bytearray()
    for d in order:
        fl = dirs[d]["files"]
        for i, p in enumerate(fl):
            sibling = file_off[fl[i + 1]] if i + 1 < len(fl) else none
            name = p.rsplit("/", 1)[-1].encode()
            ftab += struct.pack("<IIQQII", dir_off[d], sibling, data_off[p], len(files[p]),
                                none, len(name))
            ftab += name.ljust(align4(len(name)), b"\0")

    dh = struct.pack("<I", none)
    fh = struct.pack("<I", none)
    pos = 0x50
    layout = []
    for blob in (dh, bytes(dtab), fh, bytes(ftab)):
        layout.append((pos, len(blob)))
        pos += len(blob)
    data_start = (pos + 0xF) & ~0xF
    head = struct.pack("<10Q", 0x50, layout[0][0], layout[0][1], layout[1][0], layout[1][1],
                       layout[2][0], layout[2][1], layout[3][0], layout[3][1], data_start)
    body = head + dh + bytes(dtab) + fh + bytes(ftab)
    return body.ljust(data_start, b"\0") + bytes(data)


def build_pfs0(files: dict[str, bytes]) -> bytes:
    hdr = build_partition("PFS0", [(n, len(d), 0, b"") for n, d in files.items()], align=0x20)
    return hdr + b"".join(files.values())


# -- NCA ------------------------------------------------------------------------------

def _align(n, a=0x200):
    return (n + a - 1) // a * a


def _fs_header(fs_type: int, payload_off: int, payload_size: int, enc: int,
               upper: bytes) -> bytearray:
    fsh = bytearray(0x200)
    struct.pack_into("<HBBB", fsh, 0, 2, fs_type, 2 if fs_type == 1 else 3, enc)
    if fs_type == 1:  # hierarchical sha256: hash table, then PFS0
        struct.pack_into("<II", fsh, 0x8 + 0x20, 0x1000, 2)
        struct.pack_into("<QQQQ", fsh, 0x8 + 0x28, 0, 0x20, payload_off, payload_size)
    else:             # IVFC: level 6 is the RomFS
        fsh[0x8:0xC] = b"IVFC"
        struct.pack_into("<III", fsh, 0xC, 0x20000, 0x20, 7)
        struct.pack_into("<QQI", fsh, 0x8 + 0x10 + 5 * 0x18, payload_off, payload_size, 14)
    fsh[0x140:0x148] = upper[::-1]
    return fsh


def build_nca(keys: dict, title_id: int, content_type: int, sections: list[tuple[int, bytes]],
              title_key: bytes | None = None, raw_sections: list | None = None) -> bytes:
    """sections: [(fs_type, payload)], payload placed at 0x200 inside the section.

    raw_sections: [(fs_header, plaintext, encrypt(nca_offset, plaintext) -> bytes)] used as is
    (for BKTR update sections)."""
    header = bytearray(0xC00)
    header[0x200:0x204] = b"NCA3"
    header[0x205] = content_type
    header[0x206] = 2
    header[0x220] = GEN + 1
    struct.pack_into("<Q", header, 0x210, title_id)
    if title_key is not None:
        header[0x230:0x240] = title_id.to_bytes(8, "big") + bytes(7) + bytes([GEN])
        key = title_key
    else:
        key = os.urandom(16)
    kak = keys[f"key_area_key_application_{GEN:02x}"]
    header[0x300:0x340] = crypto.ecb_encrypt(kak, os.urandom(32) + key + os.urandom(16))

    body = bytearray()
    pos = 0xC00
    entries = []
    for fs_type, payload in sections:
        upper = os.urandom(4) + (1).to_bytes(4, "big")
        plain = bytes(0x200) + payload
        plain = plain.ljust(_align(len(plain)), b"\0")
        fsh = _fs_header(fs_type, 0x200, len(payload), 3, upper)
        enc = crypto.ctr_crypt(key, upper, pos, plain)
        entries.append((pos, len(plain), fsh))
        body += enc
        pos += len(plain)
    for fsh, plain, encrypt in raw_sections or []:
        enc = encrypt(key, pos, plain)
        entries.append((pos, len(plain), fsh))
        body += enc
        pos += len(plain)
    for i, (start, size, fsh) in enumerate(entries):
        struct.pack_into("<II", header, 0x240 + 0x10 * i, start // 0x200, (start + size) // 0x200)
        header[0x400 + 0x200 * i:0x600 + 0x200 * i] = fsh
    struct.pack_into("<Q", header, 0x208, 0xC00 + len(body))
    return crypto.xts_encrypt(keys["header_key"], bytes(header), 0) + bytes(body)


def nca_id(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()[:32]


# -- update (BKTR) section ------------------------------------------------------------

def _bucket_tree(entries: list[bytes], end: int) -> bytes:
    """Header (bucket count, end, each bucket's start offset) and 0x4000-byte buckets."""
    per = (0x4000 - 0x10) // len(entries[0])
    groups = [entries[i:i + per] for i in range(0, len(entries), per)]
    starts = [struct.unpack_from("<Q", g[0])[0] for g in groups]
    head = struct.pack("<IIQ", 0, len(groups), end) + b"".join(struct.pack("<Q", s)
                                                               for s in starts)
    out = head.ljust(0x4000, b"\0")
    for k, g in enumerate(groups):
        stop = starts[k + 1] if k + 1 < len(groups) else end
        out += (struct.pack("<IIQ", 0, len(g), stop) + b"".join(g)).ljust(0x4000, b"\0")
    return out


def bktr_section(base_section: bytes, new_payload: bytes, block: int = 0x4000):
    """(fs_header, plaintext, encrypt) of an update RomFS section patching base_section.

    The virtual section is [0x200 bytes][new_payload]; blocks equal to the base section
    come from the base game, the others are stored in the patch."""
    virtual = (bytes(0x200) + new_payload).ljust(_align(0x200 + len(new_payload), block), b"\0")
    rel, patch = [], bytearray()
    for v in range(0, len(virtual), block):
        chunk = virtual[v:v + block]
        if base_section[v:v + block] == chunk:
            rel.append((v, v, 0))
        else:
            rel.append((v, len(patch), 1))
            patch += chunk
    rel_bytes = [struct.pack("<QQI", *e) for e in rel]
    gen = 7
    half = _align(len(patch) // 2, 0x10)
    rel_off = _align(len(patch), 0x10)
    rel_table = _bucket_tree(rel_bytes, len(virtual))
    sub_off = rel_off + len(rel_table)
    # As in real updates: subsections (with their own counters) cover the patch data
    # only; the two tables after it use the section's counter.
    subs = [(0, 0, 2), (half, 0, 5)]
    sub_table = _bucket_tree([struct.pack("<QII", *s) for s in subs], rel_off)
    plain = bytes(patch).ljust(rel_off, b"\0") + rel_table + sub_table
    plain = plain.ljust(_align(len(plain)), b"\0")

    upper = os.urandom(4) + gen.to_bytes(4, "big")
    fsh = _fs_header(0, 0x200, len(new_payload), 4, upper)
    struct.pack_into("<QQ4sII", fsh, 0x100, rel_off, len(rel_table), b"BKTR", 1, len(rel))
    struct.pack_into("<QQ4sII", fsh, 0x120, sub_off, len(sub_table), b"BKTR", 1, len(subs))

    def encrypt(key, nca_offset, data):
        out = bytearray()
        ranges = subs + [(rel_off, 0, gen)]
        bounds = [s[0] for s in ranges] + [len(data)]
        for (start, _r, ctr), stop in zip(ranges, bounds[1:]):
            up = upper[:4] + ctr.to_bytes(4, "big")
            out += crypto.ctr_crypt(key, up, nca_offset + start, data[start:stop])
        return bytes(out)

    return fsh, plain, encrypt


def section_plain(payload: bytes) -> bytes:
    """Plaintext of a section built by build_nca for this payload (for BKTR bases)."""
    plain = bytes(0x200) + payload
    return plain.ljust(_align(len(plain)), b"\0")


# -- tickets, CNMT, NSP ---------------------------------------------------------------

def build_ticket(keys: dict, title_id: int, title_key: bytes) -> bytes:
    enc = crypto.ecb_encrypt(keys[f"titlekek_{GEN:02x}"], title_key)
    t = bytearray(0x2C0)
    struct.pack_into("<I", t, 0, 0x10004)
    body = 0x140
    t[body:body + 6] = b"Root-C"
    t[body + 0x40:body + 0x50] = enc
    t[body + 0x141] = 0
    t[body + 0x160:body + 0x170] = title_id.to_bytes(8, "big") + bytes(7) + bytes([GEN])
    return bytes(t)


def build_cnmt(title_id: int, version: int, mtype: int, contents: list[tuple[str, bytes, int]],
               base_id: int = 0) -> bytes:
    """contents: [(nca_id, nca_data, content type)]."""
    import hashlib
    ext = struct.pack("<QI", base_id, 0).ljust(0x10, b"\0")
    head = struct.pack("<QIBBHHHH", title_id, version, mtype, 0, len(ext), len(contents), 0, 0)
    head = head.ljust(0x20, b"\0")
    recs = b""
    for nid, data, ctype in contents:
        recs += (hashlib.sha256(data).digest() + bytes.fromhex(nid)
                 + len(data).to_bytes(6, "little") + bytes([ctype, 0]))
    return head + ext + recs + bytes(0x20)


def build_nacp(name: str, version: str, compressed: bool = False) -> bytes:
    """compressed: titles packed as in newer games (u16 size + raw Deflate of 32 entries,
    flag at 0x3215)."""
    titles = bytearray(0x300 * (32 if compressed else 16))
    for lang in (0, 7):
        titles[lang * 0x300:lang * 0x300 + len(name.encode())] = name.encode()
        titles[lang * 0x300 + 0x200:lang * 0x300 + 0x205] = b"Tests"
    nacp = bytearray(0x4000)
    if compressed:
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = c.compress(bytes(titles)) + c.flush()
        nacp[0:2 + len(packed)] = struct.pack("<H", len(packed)) + packed
        nacp[0x3215] = 1
    else:
        nacp[0:0x3000] = titles
    nacp[0x3060:0x3060 + len(version)] = version.encode()
    return bytes(nacp)


# 4x4 red JPEG: the icon of the fake titles.
ICON_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIs"
    "IxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAEAAQDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAA"
    "AAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAk"
    "M2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKT"
    "lJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QA"
    "HwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdh"
    "cRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hp"
    "anN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDhqKKK8Q/ZT//Z")
KIND = {"game": 0x80, "update": 0x81, "dlc": 0x82}
LAST_TITLE_KEYS: dict[bytes, bytes] = {}  # rights id -> plain title key, for the tests


def build_title(keys: dict, kind: str, title_id: int, version: int, *, name: str = "",
                display_version: str = "", compressed_nacp: bool = False,
                exefs: dict | None = None,
                romfs: dict | None = None, base_romfs_section: bytes | None = None,
                ticket: bool = True) -> tuple[dict[str, bytes], dict]:
    """Files of an NSP for one title: NCAs (+ ticket). Returns (files, info).

    ticket=False leaves the ticket out (the NCAs still need it)."""
    title_key = os.urandom(16)
    LAST_TITLE_KEYS[title_id.to_bytes(8, "big") + bytes(7) + bytes([GEN])] = title_key
    files, contents, info = {}, [], {}
    if kind == "dlc":
        data = build_nca(keys, title_id, 4, [(0, build_romfs(romfs or {"dlc.bin": b"dlc"}))],
                         title_key)
        contents.append((nca_id(data), data, 2))
    else:
        exefs_img = build_pfs0(exefs or {"main": b"MAIN", "main.npdm": b"META"})
        romfs_img = build_romfs(romfs or {"data.bin": b"x"})
        if base_romfs_section is not None:
            raw = [bktr_section(base_romfs_section, romfs_img)]
            data = build_nca(keys, title_id, 0, [(1, exefs_img)], title_key, raw_sections=raw)
        else:
            data = build_nca(keys, title_id, 0, [(1, exefs_img), (0, romfs_img)], title_key)
            info["romfs_section"] = section_plain(romfs_img)
        contents.append((nca_id(data), data, 1))
        control = build_romfs({"control.nacp": build_nacp(name, display_version, compressed_nacp),
                               "icon_AmericanEnglish.dat": ICON_JPEG})
        cdata = build_nca(keys, title_id, 2, [(0, control)], title_key)
        contents.append((nca_id(cdata), cdata, 3))
    for nid, data, _t in contents:
        files[f"{nid}.nca"] = data
    prefix = {"game": "Application", "update": "Patch", "dlc": "AddOnContent"}[kind]
    cnmt = build_cnmt(title_id, version, KIND[kind], contents)
    meta_nca = build_nca(keys, title_id, 1,
                         [(1, build_pfs0({f"{prefix}_{title_id:016x}.cnmt": cnmt}))])
    files[f"{nca_id(meta_nca)}.cnmt.nca"] = meta_nca
    if ticket:
        rid = (title_id.to_bytes(8, "big") + bytes(7) + bytes([GEN])).hex()
        files[f"{rid}.tik"] = build_ticket(keys, title_id, title_key)
        files[f"{rid}.cert"] = b"\0" * 0x700
    return files, info


def write_nsp(path: str, files: dict[str, bytes]) -> str:
    with open(path, "wb") as f:
        f.write(build_pfs0(files))
    return path
