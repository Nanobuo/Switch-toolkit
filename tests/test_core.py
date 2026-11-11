"""End-to-end tests on fake (randomly keyed) Switch files: NSP → XCI, merging, extraction."""

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
from switchtk import crypto, extract, jobs, titles, xci  # noqa: E402
from switchtk.fs import Cancelled, FileStorage, Partition, SubStorage  # noqa: E402
from switchtk.keys import KeySet, KeysError  # noqa: E402
from switchtk.nca import Nca, NcaError  # noqa: E402

TID = 0x0100ABCD00010000
BASE_ROM = {"data/a.bin": os.urandom(70_000), "data/keep.bin": b"keep" * 9000,
            "top.txt": b"hello", "deep/x/y/z.txt": b"z", "empty.dat": b""}


class Core(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.d = cls.tmp.name
        cls.raw_keys = fk.make_keys(os.path.join(d, "prod.keys"))
        cls.keys = KeySet.load(os.path.join(d, "prod.keys"))
        files, info = fk.build_title(cls.raw_keys, "game", TID, 0, name="Gioco Prova",
                                     display_version="1.0.0", romfs=BASE_ROM)
        fk.write_nsp(cls.p("base.nsp"), files)
        cls.new_rom = dict(BASE_ROM)
        cls.new_rom["data/a.bin"] = os.urandom(70_000)
        cls.new_rom["new/b.txt"] = b"nuovo"
        files, _ = fk.build_title(cls.raw_keys, "update", TID | 0x800, 131072, name="Gioco Prova",
                                  display_version="1.0.2", compressed_nacp=True,
                                  exefs={"main": b"MAIN-v2", "main.npdm": b"META",
                                         "subsdk0": b"SDK"},
                                  romfs=cls.new_rom, base_romfs_section=info["romfs_section"])
        fk.write_nsp(cls.p("upd2.nsp"), files)
        files, _ = fk.build_title(cls.raw_keys, "update", TID | 0x800, 65536, name="Gioco Prova",
                                  display_version="1.0.1", romfs={"old.txt": b"old"},
                                  base_romfs_section=info["romfs_section"])
        fk.write_nsp(cls.p("upd1.nsp"), files)
        files, _ = fk.build_title(cls.raw_keys, "dlc", TID + 0x1001, 0, romfs={"d.bin": b"D1"})
        fk.write_nsp(cls.p("dlc1.nsp"), files)
        files, _ = fk.build_title(cls.raw_keys, "dlc", TID + 0x1002, 0, romfs={"d.bin": b"D2"},
                                  ticket=False)
        fk.write_nsp(cls.p("dlc2.nsp"), files)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def p(cls, *a):
        return os.path.join(cls.d, *a)

    def load(self, *names):
        out = []
        for n in names:
            out += titles.read_titles(self.p(n), self.keys)
        return out

    def test_read_titles(self):
        (t,) = self.load("base.nsp")
        self.assertEqual((t.title_id, t.kind, t.version), (TID, "game", 0))
        self.assertEqual(t.name, "Gioco Prova")
        self.assertEqual(t.display_version, "1.0.0")
        self.assertTrue(t.icon.startswith(b"\xff\xd8"))
        games = titles.group_titles(self.load("upd1.nsp", "dlc1.nsp", "base.nsp", "upd2.nsp"))
        self.assertEqual(list(games), [TID])
        g = games[TID]
        self.assertEqual(g.latest_update.version, 131072)
        self.assertEqual(g.latest_update.name, "Gioco Prova")  # compressed NACP titles
        self.assertEqual(g.name, "Gioco Prova")
        self.assertEqual(len(g.dlcs), 1)
        self.assertEqual([t.kind for t in g.merged()], ["game", "update", "dlc"])
        self.assertEqual(len(g.warnings()), 1)  # two updates: only the latest is used
        (orphan,) = titles.group_titles(self.load("upd2.nsp")).values()
        self.assertFalse(orphan.latest_update.complete)  # an NSP update needs its base
        self.assertIn("Manca il gioco base", orphan.warnings()[0])

    def test_nsp_to_xci(self):
        (t,) = self.load("base.nsp")
        out = xci.build_xci([t], self.p("single", xci.default_name([t])), self.keys,
                            log=lambda m: None)
        self.assertEqual(os.path.basename(out), "Gioco Prova [0100ABCD00010000][v0].xci")
        with open(out, "rb") as f:
            raw = FileStorage(f)
            head = raw.read(0, 0x200)
            self.assertEqual(head[0x100:0x104], b"HEAD")
            root_off, root_size = struct.unpack_from("<QQ", head, 0x130)
            self.assertEqual(root_off, xci.ROOT_OFFSET)
            root = Partition(SubStorage(raw, root_off, raw.size - root_off))
            self.assertEqual([e.name for e in root.entries], ["update", "normal", "secure"])
            self.assertEqual(crypto_sha(raw.read(root_off, root_size)), head[0x140:0x160])
            secure_page = struct.unpack_from("<I", head, 0x104)[0]
            self.assertEqual(secure_page * 0x200, root_off + root.by_name["secure"].offset)
            self.assertEqual(raw.size % 0x200, 0)
            self.assertEqual(struct.unpack_from("<I", head, 0x118)[0], raw.size // 0x200 - 1)
            secure = Partition(root.open("secure"))
            for e in secure.entries:
                self.assertEqual(crypto_sha(secure.open(e.name).read(0, e.hashed_size)), e.sha256)
        with titles.Container(out, self.keys) as c:
            self.assertEqual(c.kind, "xci")
            self.assertEqual(c.tickets, {})
            for name in c.files:
                nca = Nca(c.files[name], self.keys, name=name)
                self.assertEqual(nca.distribution, 1)
                self.assertFalse(nca.has_rights_id)
        # The converted XCI decrypts without any ticket.
        dest = extract.extract(titles.group_titles(titles.read_titles(out, self.keys))[TID],
                               self.p("single-out"), self.keys, log=lambda m: None)
        for path, data in BASE_ROM.items():
            self.assertEqual((pathlib.Path(dest) / "romfs" / path).read_bytes(), data)

    def test_merge_and_extract_update(self):
        games = titles.group_titles(self.load("base.nsp", "upd1.nsp", "upd2.nsp", "dlc1.nsp"))
        (job,) = jobs.xci_jobs(list(games.values()), self.p("merged"))
        self.assertEqual(job.label, "Gioco Prova [0100ABCD00010000][v131072][+1 DLC].xci")
        seen = []
        job.run(self.keys, lambda d, t, n: seen.append((d, t)), lambda: False, lambda m: None)
        self.assertEqual(seen[-1][0], seen[-1][1])
        merged = titles.read_titles(job.target, self.keys)
        self.assertEqual(sorted((t.kind, t.version) for t in merged),
                         [("dlc", 0), ("game", 0), ("update", 131072)])
        game = titles.group_titles(merged)[TID]
        dest = extract.extract(game, self.p("ext"), self.keys, log=lambda m: None)
        root = pathlib.Path(dest)
        for path, data in self.new_rom.items():
            self.assertEqual((root / "romfs" / path).read_bytes(), data, path)
        self.assertEqual((root / "exefs" / "main").read_bytes(), b"MAIN-v2")
        self.assertEqual((root / "exefs" / "subsdk0").read_bytes(), b"SDK")
        # Without the update: base files.
        dest = extract.extract(game, self.p("ext-base"), self.keys, use_update=False,
                               log=lambda m: None)
        for path, data in BASE_ROM.items():
            self.assertEqual((pathlib.Path(dest) / "romfs" / path).read_bytes(), data)
        self.assertEqual((pathlib.Path(dest) / "exefs" / "main").read_bytes(), b"MAIN")

    def test_update_reads_from_base_and_patch(self):
        (upd,) = self.load("upd2.nsp")
        with titles.Container(upd.path, self.keys) as c:
            nca = c.nca(upd.program_nca(c))
            s = nca.romfs_section()
            virtual = nca.patched_section(s, None)
            self.assertEqual({e[2] for e in virtual.entries}, {0, 1})
            with self.assertRaises(NcaError):
                nca.romfs()  # needs the base game

    def test_unreadable_update_tables(self):
        from switchtk.nca import _bucket_tree
        garbage = struct.pack("<IIQ", 0, 1, 0).ljust(0x4000, b"\0")
        garbage += struct.pack("<IIQ", 0, 0x7FFFFFFF, 0).ljust(0x4000, b"\0")
        with self.assertRaises(NcaError):
            _bucket_tree(garbage, "<QQI", "x.nca")
        with self.assertRaises(NcaError):
            _bucket_tree(struct.pack("<IIQ", 0, 5, 0), "<QQI", "x.nca")

    def test_romfs_bin_and_options(self):
        games = titles.group_titles(self.load("base.nsp"))
        (job,) = jobs.extract_jobs(list(games.values()), self.p("bin"), exefs=False,
                                   romfs_bin=True)
        dest = job.run(self.keys, lambda *a: None, lambda: False, lambda m: None)
        self.assertFalse(os.path.exists(os.path.join(dest, "exefs")))
        img = pathlib.Path(dest, "romfs.bin").read_bytes()
        self.assertEqual(struct.unpack_from("<Q", img)[0], 0x50)

    def test_separate_xci_per_file(self):
        games = titles.group_titles(self.load("base.nsp", "upd2.nsp"))
        todo = jobs.xci_jobs(list(games.values()), self.p("sep"), merge=False)
        self.assertEqual(len(todo), 2)
        todo = jobs.xci_jobs(list(games.values()), self.p("sep"), include_update=False)
        self.assertEqual([t.kind for t in todo[0].titles], ["game"])

    def test_missing_ticket_and_title_keys(self):
        (dlc,) = self.load("dlc2.nsp")  # no ticket: loading works, conversion cannot
        with self.assertRaises(KeysError):
            xci.build_xci([dlc], self.p("nokey.xci"), self.keys, log=lambda m: None)
        self.assertFalse(os.path.exists(self.p("nokey.xci.part")))
        # The same title key from title.keys (already decrypted) is enough.
        with titles.Container(dlc.path, self.keys) as c:
            name = next(n for n in c.files if not n.endswith(".cnmt.nca"))
            nca = c.nca(name)
            rid = bytes(nca.rights_id)
            kak = self.keys.key_area_key(0, nca.key_generation)
        with open(self.p("prod.keys")) as f:
            prod = f.read()
        tk_dir = self.p("with-title-keys")
        os.makedirs(tk_dir)
        pathlib.Path(tk_dir, "prod.keys").write_text(prod)
        plain_key = fk.LAST_TITLE_KEYS[rid]
        pathlib.Path(tk_dir, "title.keys").write_text(f"{rid.hex()} = {plain_key.hex()}\n")
        ks = KeySet.load(os.path.join(tk_dir, "prod.keys"))
        out = xci.build_xci([dlc], self.p("withkey.xci"), ks, log=lambda m: None)
        with titles.Container(out, ks) as c:
            nca = c.nca(name)
            self.assertFalse(nca.has_rights_id)
            area = crypto.ecb_decrypt(kak, nca.header[0x300:0x340])
            self.assertEqual(area[0x20:0x30], fk.LAST_TITLE_KEYS[rid])

    def test_cancel_removes_partial_file(self):
        (t,) = self.load("base.nsp")
        with self.assertRaises(Cancelled):
            xci.build_xci([t], self.p("cancel.xci"), self.keys, should_cancel=lambda: True,
                          log=lambda m: None)
        self.assertFalse(os.path.exists(self.p("cancel.xci")))
        self.assertFalse(os.path.exists(self.p("cancel.xci.part")))

    def test_verify(self):
        results = titles.verify(self.p("base.nsp"), self.keys)
        self.assertTrue(results and all(s == "ok" for _n, s in results))
        data = bytearray(pathlib.Path(self.p("base.nsp")).read_bytes())
        data[len(data) // 4] ^= 0xFF  # inside the program NCA
        pathlib.Path(self.p("bad.nsp")).write_bytes(data)
        self.assertIn("mismatch", [s for _n, s in titles.verify(self.p("bad.nsp"), self.keys)])

    def test_cli(self):
        from switchtk import cli
        keys = self.p("prod.keys")
        self.assertEqual(cli.main(["--keys", keys, "info", self.p("base.nsp"), "--files"]), 0)
        self.assertEqual(cli.main(["--keys", keys, "xci", self.p("base.nsp"), self.p("upd2.nsp"),
                                   "-o", self.p("cli")]), 0)
        self.assertEqual(len(os.listdir(self.p("cli"))), 1)
        self.assertEqual(cli.main(["--keys", keys, "extract", self.p("base.nsp"),
                                   "-o", self.p("cli-ext"), "--no-romfs"]), 0)
        self.assertEqual(cli.main(["--keys", keys, "verify", self.p("base.nsp")]), 0)
        self.assertEqual(cli.main(["--keys", self.p("nope.keys"), "info", self.p("base.nsp")]), 1)


def crypto_sha(data):
    import hashlib
    return hashlib.sha256(data).digest()


class Nacp(unittest.TestCase):
    def test_plain_and_compressed_titles(self):
        from switchtk.meta import parse_nacp
        for compressed in (False, True):
            nacp = parse_nacp(fk.build_nacp("Città è", "2.0", compressed))
            self.assertEqual((nacp.name(), nacp.publisher(), nacp.display_version),
                             ("Città è", "Tests", "2.0"))
        broken = bytearray(fk.build_nacp("x", "1.0", True))
        broken[2:20] = b"\xff" * 18
        self.assertEqual(parse_nacp(bytes(broken)).name(), "")  # no garbage, no crash


class Keys(unittest.TestCase):
    def test_derivation_from_master_keys(self):
        master = os.urandom(16)
        src = {n: os.urandom(16) for n in ("aes_kek_generation_source", "aes_key_generation_source",
                                           "key_area_key_application_source", "titlekek_source",
                                           "header_kek_source")}
        src["header_key_source"] = os.urandom(32)
        ks = KeySet(dict(src, master_key_00=master))
        kek = crypto.ecb_decrypt(master, src["aes_kek_generation_source"])
        src_kek = crypto.ecb_decrypt(kek, src["key_area_key_application_source"])
        expected = crypto.ecb_decrypt(src_kek, src["aes_key_generation_source"])
        self.assertEqual(ks.key_area_key(0, 0), expected)
        self.assertEqual(ks.titlekek(0), crypto.ecb_decrypt(master, src["titlekek_source"]))
        self.assertEqual(len(ks.header_key), 32)
        with self.assertRaises(KeysError):
            ks.titlekek(5)

    def test_parse_and_title_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "prod.keys")
            with open(path, "w") as f:
                f.write("# comment\nheader_key = " + "11" * 32 + "\nBAD LINE\n"
                        "titlekek_00 = " + "22" * 16 + "\n")
            with open(os.path.join(d, "title.keys"), "w") as f:
                f.write("0100000000001000000000000000000a = " + "33" * 16 + "\n")
            ks = KeySet.load(path)
            self.assertEqual(ks.titlekek(0), b"\x22" * 16)
            self.assertEqual(ks.title_keys[bytes.fromhex("0100000000001000000000000000000a")],
                             b"\x33" * 16)
            with open(path, "w") as f:
                f.write("titlekek_00 = " + "22" * 16 + "\n")
            with self.assertRaises(KeysError):
                KeySet.load(path)


if __name__ == "__main__":
    unittest.main()
