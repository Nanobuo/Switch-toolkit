"""NUP container: reading a file built by hand from the spec, writing, conversion from NSP."""

import hashlib
import os
import pathlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["SWITCHTK_LANG"] = "it"

import fake_switch as fk  # noqa: E402
from switchtk import cli, extract, jobs, nup, titles  # noqa: E402
from switchtk.fs import MemoryStorage  # noqa: E402
from switchtk.keys import KeySet  # noqa: E402

TID = 0x0100ABCD00010000


def spec_example(path, meta=b""):
    """SPEC section 7, built field by field: one base package, root -> romfs -> hello.bin."""
    data = b"hello"
    pool = b"romfshello.bin"
    toc = bytearray()
    toc += struct.pack("<4sIQQQQQ", b"TOC\x01", 1, 2, 1, 0, 0, 0)  # pool offset patched
    meta_off = 0  # patched below when there is a meta block
    toc += struct.pack("<QIIIIBBHIQI20x", 0x040074A01BF12000, 0, 0xFFFFFFFF, 1, 0x10000,
                       0, 0, 0, 0, 0, len(meta))
    toc += struct.pack("<IIIIQ", 0, 0, 0, 1, 0)          # root: no name, 1 child (slot 0)
    toc += struct.pack("<IIIIQ", 0, 5, 1, 1, 0)          # "romfs": 1 child (slot 1)
    toc += struct.pack("<IIQQQ32sBBH12x", 5, 9, 5, 0, 5, hashlib.sha256(data).digest(),
                       0, 0, 0)                          # extent offset patched
    toc += struct.pack("<II", 1, 2)                      # children: dir 1, file 0 (id 2)
    pool_off = len(toc)
    toc += pool
    toc += bytes(-len(toc) % 16)
    struct.pack_into("<QQ", toc, 0x18, pool_off, len(pool))
    blob = 0x80 + len(toc)
    struct.pack_into("<Q", toc, 0x30 + 0x40 + 0x30 + 0x10, blob)
    end = blob + 16
    if meta:
        meta_off = end
        struct.pack_into("<Q", toc, 0x30 + 0x20, meta_off)
    size = end + len(meta)
    head = struct.pack("<4sHHQQ32sQQII16s32s", b"NUP\x01", 0x80, 1, 0x80, len(toc),
                       hashlib.sha256(toc).digest(), blob, size, 1, 0, bytes(16), bytes(32))
    with open(path, "wb") as f:
        f.write(head + toc + data.ljust(16, b"\0") + meta)
    return path


def structure_problems(path):
    """The structural rules the format's reference verifier checks."""
    problems = []
    with nup.NupFile(path) as n:
        raw = pathlib.Path(path).read_bytes()
        if raw[0x60:0x80] != bytes(32):
            problems.append("superblock reserved")
        ranges = sorted((first, first + count) for _n, first, count in n.dirs)
        for (_a, a2), (b1, _b) in zip(ranges, ranges[1:]):
            if a2 > b1:
                problems.append("overlapping child ranges")
        for name, first, count in n.dirs:
            kids = [n.dirs[c][0] if c < n.ndirs else n.files[c - n.ndirs][0]
                    for c in n.children[first:first + count]]
            names = [k.encode() for k in kids]
            if names != sorted(names) or len(set(names)) != len(names):
                problems.append(f"children of {name!r} not sorted/unique")
        for name, size, _sha, eoff, esize, codec in n.files:
            if size and eoff % 16:
                problems.append(f"{name}: extent not aligned")
            if not size and (eoff or esize or codec):
                problems.append(f"{name}: empty file with an extent")
        if sum(c for _n, _f, c in n.dirs) != len(n.children):
            problems.append("unused child slots")
    return problems


class Nup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def p(self, *a):
        return os.path.join(self.d, *a)

    def test_reads_spec_example(self):
        meta = nup.build_tlvs({nup.TLV_TITLE_NAME: b"Bananza", nup.TLV_DISPLAY_VERSION: b"1.0.0",
                               nup.TLV_LOCALIZED_TITLES: nup.encode_localized(
                                   {"it": "Bananza IT".encode()})})
        path = spec_example(self.p("spec.nup"), meta)
        with nup.NupFile(path) as n:
            (pkg,) = n.packages
            self.assertEqual((pkg.title_id, pkg.kind, pkg.version), (0x040074A01BF12000, 0,
                                                                     0x10000))
            tree = n.tree(pkg, "romfs")
            self.assertEqual(list(tree), ["hello.bin"])
            self.assertEqual(b"".join(tree["hello.bin"].chunks()), b"hello")
            self.assertEqual(n.tree(pkg, "exefs"), {})
        self.assertEqual(structure_problems(path), [])
        (t,) = titles.read_titles(path, None)  # no keys needed
        self.assertEqual((t.kind, t.name, t.display_version), ("game", "Bananza IT", "1.0.0"))
        game = titles.group_titles([t])[0x040074A01BF12000]
        dest = extract.extract(game, self.p("x"), None, exefs=False, log=lambda m: None)
        self.assertEqual(pathlib.Path(dest, "romfs", "hello.bin").read_bytes(), b"hello")

    def test_rejects_damage(self):
        path = spec_example(self.p("spec.nup"))
        raw = bytearray(pathlib.Path(path).read_bytes())
        bad_toc = bytearray(raw)
        bad_toc[0x90] ^= 1
        pathlib.Path(self.p("toc.nup")).write_bytes(bad_toc)
        with self.assertRaises(nup.NupError):
            nup.NupFile(self.p("toc.nup"))
        pathlib.Path(self.p("short.nup")).write_bytes(raw[:-1])
        with self.assertRaises(nup.NupError):
            nup.NupFile(self.p("short.nup"))
        bad_data = bytearray(raw)
        bad_data[len(raw) - 16] ^= 1  # first byte of "hello"
        pathlib.Path(self.p("data.nup")).write_bytes(bad_data)
        with nup.NupFile(self.p("data.nup")) as n:
            self.assertEqual([s for _p, s in n.verify()], ["mismatch"])
            with self.assertRaises(nup.NupError):
                b"".join(n.tree(n.packages[0], "romfs")["hello.bin"].chunks())

    def test_standalone_update_is_a_complete_game(self):
        """Dumps of an installed, updated game come as one standalone update package
        holding the whole updated game: no "base game missing" warning."""
        tid = 0x0400D4201C6EA000
        packages = [nup.NewPackage(nup.KIND_UPDATE, tid, 327680, nup.FLAG_STANDALONE_UPDATE,
                                   exefs={"main.npdm": MemoryStorage(b"META")},
                                   romfs={"a.bin": MemoryStorage(b"full game")},
                                   meta={nup.TLV_TITLE_NAME: b"Tennis"})]
        path = nup.write_nup(self.p("upd.nup"), packages)
        (t,) = titles.read_titles(path, None)
        self.assertTrue(t.complete)
        self.assertEqual(titles.title_label(t), "Gioco aggiornato")
        game = titles.group_titles([t])[tid]
        self.assertEqual(game.warnings(), [])
        dest = extract.extract(game, self.p("x"), None, log=lambda m: None)
        self.assertEqual(pathlib.Path(dest, "romfs", "a.bin").read_bytes(), b"full game")
        (job,) = jobs.nx_jobs([game], self.p("nx"))
        job.run(None, lambda *a: None, lambda: False, lambda m: None)
        (back,) = titles.read_titles(job.target, None)
        self.assertTrue(back.standalone and back.complete)
        self.assertEqual(titles.group_titles([back])[tid].warnings(), [])

    def test_write_roundtrip(self):
        big = os.urandom(3000) * 50 + os.urandom(9000)
        old, nup.SMALL_FILE = nup.SMALL_FILE, 4096   # also exercise the streaming path
        try:
            packages = [
                nup.NewPackage(nup.KIND_BASE, TID, 0,
                               exefs={"main.npdm": MemoryStorage(b"META")},
                               romfs={"b/big.bin": MemoryStorage(big),
                                      "a.txt": MemoryStorage(b"same"),
                                      "B.txt": MemoryStorage(b"same"),
                                      "empty": MemoryStorage(b""),
                                      "rnd": MemoryStorage(os.urandom(6000))},
                               meta={nup.TLV_TITLE_NAME: b"Gioco"}),
                nup.NewPackage(nup.KIND_UPDATE, TID, 65536,
                               romfs={"a.txt": MemoryStorage(b"new"),
                                      "b/big.bin": MemoryStorage(big)}),
            ]
            path = nup.write_nup(self.p("w.nup"), packages)
        finally:
            nup.SMALL_FILE = old
        self.assertFalse(os.path.exists(path + ".part"))
        self.assertEqual(structure_problems(path), [])
        with nup.NupFile(path) as n:
            base, upd = n.packages
            files = {p: e for p, e in n.tree(base, "romfs").items()}
            self.assertEqual(b"".join(files["b/big.bin"].chunks()), big)
            self.assertEqual(files["b/big.bin"].codec, nup.CODEC_ZSTD)
            self.assertEqual(files["rnd"].codec, nup.CODEC_RAW)        # incompressible
            self.assertEqual(files["a.txt"].offset, files["B.txt"].offset)  # dedup
            self.assertEqual(n.tree(upd, "romfs")["b/big.bin"].offset,
                             files["b/big.bin"].offset)
            self.assertEqual((files["empty"].size, files["empty"].offset), (0, 0))
            self.assertTrue(all(s == "ok" for _p, s in n.verify()))
            self.assertEqual(base.meta[nup.TLV_TITLE_NAME], b"Gioco")

    def test_nsp_to_nup_and_back(self):
        raw = fk.make_keys(self.p("prod.keys"))
        keys = KeySet.load(self.p("prod.keys"))
        base_rom = {"data/a.bin": os.urandom(70_000), "keep.bin": b"k" * 50_000}
        files, info = fk.build_title(raw, "game", TID, 0, name="Gioco Prova",
                                     display_version="1.0.0", romfs=base_rom)
        fk.write_nsp(self.p("base.nsp"), files)
        new_rom = dict(base_rom, **{"data/a.bin": os.urandom(70_000), "n.txt": b"n"})
        files, _ = fk.build_title(raw, "update", TID | 0x800, 65536, name="Gioco Prova",
                                  display_version="1.0.1",
                                  exefs={"main": b"NSO0" + bytes(0x3C) + b"B" * 32,
                                         "main.npdm": b"META2"},
                                  romfs=new_rom, base_romfs_section=info["romfs_section"])
        fk.write_nsp(self.p("upd.nsp"), files)
        files, _ = fk.build_title(raw, "dlc", TID + 0x1001, 0, romfs={"d.bin": b"DLC"})
        fk.write_nsp(self.p("dlc.nsp"), files)
        loaded = []
        for n in ("base.nsp", "upd.nsp", "dlc.nsp"):
            loaded += titles.read_titles(self.p(n), keys)
        (job,) = jobs.nup_jobs(list(titles.group_titles(loaded).values()), self.p("out"))
        self.assertEqual(job.label, "Gioco Prova [0100ABCD00010000][v65536][+1 DLC].nup")
        job.run(keys, lambda *a: None, lambda: False, lambda m: None)
        self.assertEqual(structure_problems(job.target), [])

        back = titles.read_titles(job.target, None)
        self.assertEqual(sorted((t.kind, t.version) for t in back),
                         [("dlc", 0), ("game", 0), ("update", 65536)])
        games = titles.group_titles(back)
        self.assertEqual(list(games), [TID])
        game = games[TID]
        self.assertEqual((game.name, game.base.display_version, game.latest_update.display_version),
                         ("Gioco Prova", "1.0.0", "1.0.1"))
        self.assertTrue(game.icon)
        with nup.NupFile(job.target) as n:
            base_pkg, upd_pkg, dlc_pkg = n.packages
            self.assertEqual(upd_pkg.title_id, TID)        # program id of the game
            self.assertEqual(upd_pkg.meta[nup.TLV_BUILD_ID], b"B" * 32)
            self.assertEqual(base_pkg.meta[nup.TLV_DISPLAY_VERSION], b"1.0.0")
            self.assertIn(b"Gioco Prova", base_pkg.meta[nup.TLV_LOCALIZED_TITLES])
            self.assertEqual(list(n.tree(dlc_pkg, "romfs")), ["d.bin"])

        dest = extract.extract(game, self.p("x"), None, log=lambda m: None)
        for path, data in new_rom.items():
            self.assertEqual(pathlib.Path(dest, "romfs", path).read_bytes(), data, path)
        self.assertEqual(pathlib.Path(dest, "exefs", "main.npdm").read_bytes(), b"META2")
        self.assertEqual(jobs.xci_jobs([game], self.p("o")), [])   # no NCAs inside
        self.assertTrue(jobs.has_decrypted([game]))

        # Command line: info and extraction of a NUP without any prod.keys.
        os.environ["SWITCHTK_KEYS"] = self.p("missing.keys")
        try:
            self.assertEqual(cli.main(["info", job.target, "--files"]), 0)
            self.assertEqual(cli.main(["verify", job.target]), 0)
            self.assertEqual(cli.main(["extract", job.target, "-o", self.p("cli")]), 0)
            self.assertEqual(cli.main(["--keys", self.p("prod.keys"), "nup", self.p("base.nsp"),
                                       "-o", self.p("cli-nup"), "--level", "1"]), 0)
        finally:
            del os.environ["SWITCHTK_KEYS"]
        self.assertEqual(len(os.listdir(self.p("cli-nup"))), 1)


if __name__ == "__main__":
    unittest.main()
