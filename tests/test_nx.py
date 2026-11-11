"""NX archives: the format's own conformance fixtures, and archives written here."""

import hashlib
import json
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
from switchtk import cli, extract, jobs, nup, nx, titles  # noqa: E402
from switchtk import fs  # noqa: E402
from switchtk.fs import Cancelled, MemoryStorage  # noqa: E402
from switchtk.keys import KeySet  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "nx_fixtures"   # from fab918/nxformat (MIT)
TID = 0x0100ABCD00010000


def outcome(path) -> str:
    try:
        with nx.NxArchive(str(path)) as a:
            ok = all(s == "ok" for _p, s in a.verify())
            return "accept" if ok else "reject_full_verify"
    except nx.NxError:
        return "reject"


class Conformance(unittest.TestCase):
    def test_reference_fixtures(self):
        index = json.loads((FIXTURES / "fixture_index.json").read_text())
        self.assertEqual(len(index["fixtures"]), 61)
        for fx in index["fixtures"]:
            path = FIXTURES / fx["file"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), fx["sha256"])
            with self.subTest(fx["file"]):
                self.assertEqual(outcome(path), fx["expected"], fx["reason"])

    def test_fixture_contents(self):
        with nx.NxArchive(str(FIXTURES / "nested_vfs.nx")) as a:
            (app,) = a.applications
            (snap,) = app.snapshots
            files = {p: b"".join(st.chunks()) for p, st in a.tree(snap.root).items()}
            self.assertEqual(files, {"root.txt": b"root", "folder/child.bin": b"nested child"})
            self.assertEqual(app.title, "Synthetic NX Fixture")
        with nx.NxArchive(str(FIXTURES / "mixed_raw_zstd.nx")) as a:
            (snap,) = a.applications[0].snapshots
            st = a.tree(snap.root)["mixed.bin"]
            whole = b"".join(st.chunks())
            self.assertEqual(st.read(200_000, 100_000), whole[200_000:300_000])  # across blocks
            self.assertEqual(hashlib.sha256(whole).digest(), st.sha)
        with nx.NxArchive(str(FIXTURES / "switch2_localized_icons.nx")) as a:
            app = a.applications[0]
            self.assertEqual(app.platform, nx.PLATFORM_SWITCH2)
            self.assertEqual(app.localized, {"fr": "Titre synthétique", "ja": "架空の作品"})
            self.assertEqual(a.media_bytes(app.localized_icons["fr"])[:4], b"\x89PNG")
        with nx.NxArchive(str(FIXTURES / "appended_generation_2.nx")) as a:
            self.assertEqual(a.header["generation"], 2)
        (t,) = titles.read_titles(str(FIXTURES / "localized_metadata_icons.nx"), None)
        self.assertEqual((t.kind, t.name), ("game", "Example"))

    def test_dlc_in_a_separate_application(self):
        """An archive may keep add-ons (e.g. a Switch 2 upgrade pack) in an application of
        their own: they still belong to the game in the same file, with no warning."""
        with tempfile.TemporaryDirectory() as d:
            w = nx.NxWriter(os.path.join(d, "two.nx"))
            game = w.add_file("exefs/main.npdm", MemoryStorage(b"META"))
            pack = w.add_file("romfs/pack.bin", MemoryStorage(b"PACK"))
            meta = nx.Meta(title="Gioco", localized={"en-us": ("Gioco", None)})
            out = w.finish([
                nx.NewApplication(TID, nx.PLATFORM_SWITCH1, [
                    nx.NewSnapshot(nx.SNAP_BASE, 0, {"exefs/main.npdm": game}, meta=meta)]),
                nx.NewApplication(0x0400ABCD00012000, nx.PLATFORM_SWITCH2, [
                    nx.NewSnapshot(nx.SNAP_DLC, 0, {"romfs/pack.bin": pack}, executable=False,
                                   addon_title_id=0x0400ABCD00013001)]),
            ])
            games = titles.group_titles(titles.read_titles(out, None))
            self.assertEqual(list(games), [TID])
            self.assertEqual(len(games[TID].dlcs), 1)
            self.assertEqual(games[TID].warnings(), [])
            # read_titles recognizes an archive whose first header page is lost
            raw = bytearray(pathlib.Path(out).read_bytes())
            raw[:4096] = bytes(4096)
            pathlib.Path(out).write_bytes(raw)
            self.assertEqual(len(titles.read_titles(out, None)), 2)
        (only,) = titles.group_titles(
            titles.read_titles(str(FIXTURES / "dlc_only_application.nx"), None)).values()
        self.assertEqual(only.warnings(),
                         ["Contiene solo DLC: per giocarli serve anche il gioco base."])

    def test_crc32c_and_languages(self):
        self.assertEqual(nx.crc32c(b"123456789"), 0xE3069283)
        self.assertTrue(nx.valid_language("zh-hant"))
        for bad in ("j-", "EN", "en_us", "-en", "toolongtag", ""):
            self.assertFalse(nx.valid_language(bad), bad)


class Writer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.d = pathlib.Path(cls.tmp.name)
        cls.raw_keys = fk.make_keys(str(d / "prod.keys"))
        cls.keys = KeySet.load(str(d / "prod.keys"))
        cls.base_rom = {"data/a.bin": os.urandom(300_000), "same.bin": b"s" * 600_000,
                        "random.bin": os.urandom(700_000), "empty": b""}
        files, info = fk.build_title(cls.raw_keys, "game", TID, 0, name="Gioco Prova",
                                     display_version="1.0.0", romfs=cls.base_rom)
        fk.write_nsp(str(d / "base.nsp"), files)
        cls.new_rom = dict(cls.base_rom, **{"data/a.bin": os.urandom(300_000), "n.txt": b"n"})
        files, _ = fk.build_title(cls.raw_keys, "update", TID | 0x800, 65536,
                                  name="Gioco Prova", display_version="1.0.1",
                                  compressed_nacp=True,
                                  exefs={"main": b"NSO0" + bytes(0x3C) + b"B" * 32,
                                         "main.npdm": b"META"},
                                  romfs=cls.new_rom, base_romfs_section=info["romfs_section"])
        fk.write_nsp(str(d / "upd.nsp"), files)
        files, _ = fk.build_title(cls.raw_keys, "dlc", TID + 0x1001, 0, romfs={"d.bin": b"DLC"})
        fk.write_nsp(str(d / "dlc.nsp"), files)
        loaded = []
        for n in ("base.nsp", "upd.nsp", "dlc.nsp"):
            loaded += titles.read_titles(str(d / n), cls.keys)
        cls.games = list(titles.group_titles(loaded).values())
        (job,) = jobs.nx_jobs(cls.games, str(d / "out"))
        job.run(cls.keys, lambda *a: None, lambda: False, lambda m: None)
        cls.archive = job.target

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_layout(self):
        raw = pathlib.Path(self.archive).read_bytes()
        self.assertEqual(pathlib.Path(self.archive).name,
                         "Gioco Prova [0100ABCD00010000][v65536][+1 DLC].nx")
        self.assertEqual(raw[:4096], raw[4096:8192])            # identical header pages
        self.assertEqual(raw[256:4096], bytes(3840))
        with nx.NxArchive(self.archive) as a:
            h = a.header
            self.assertEqual((h["major"], h["minor"], h["generation"]), (0, 2, 0))
            self.assertEqual(h["archive_size"], len(raw))      # compact
            for i, (off, stored, *_r) in enumerate(a.blocks):  # minimum alignment, no gaps
                if i:
                    prev = a.blocks[i - 1]
                    self.assertEqual(off, nx._align(prev[0] + prev[1]))
            self.assertTrue(all(s == "ok" for _p, s in a.verify()))

    def test_catalog(self):
        with nx.NxArchive(self.archive) as a:
            (app,) = a.applications
            self.assertEqual((app.application_id, app.platform), (TID, nx.PLATFORM_SWITCH1))
            self.assertEqual((app.title, app.publisher), ("Gioco Prova", "Tests"))
            self.assertEqual(app.localized, {"en-us": "Gioco Prova", "it": "Gioco Prova"})
            self.assertEqual(list(app.localized_icons), ["en-us"])
            kinds = [(s.kind, s.version, s.display_version) for s in app.snapshots]
            self.assertEqual(kinds, [(nx.SNAP_BASE, 0, "1.0.0"), (nx.SNAP_UPDATE, 65536, "1.0.1"),
                                     (nx.SNAP_DLC, 0, "")])
            base, upd, dlc = app.snapshots
            self.assertEqual(app.metadata_snapshot, upd.index)  # newest version wins
            self.assertIn(base.index, app.history)               # base metadata kept
            self.assertEqual(upd.build_id, b"B" * 32)
            self.assertEqual((dlc.program_id, dlc.addon_title_id), (None, TID + 0x1001))
            self.assertEqual(len(a.media_bytes(app.nacp_media)), 0x4000)
            # unchanged files keep one record across snapshots; changed ones do not
            b, u = a.tree(base.root), a.tree(upd.root)
            self.assertEqual(b["romfs/same.bin"].first, u["romfs/same.bin"].first)
            self.assertNotEqual(b["romfs/data/a.bin"].first, u["romfs/data/a.bin"].first)
            # compressible file: 64 KiB frames; random file: 256 KiB RAW blocks
            self.assertEqual(b["romfs/same.bin"].chunk, 64 << 10)
            self.assertEqual(b["romfs/random.bin"].chunk, 256 << 10)
            codecs = {a.blocks[a.refs[b["romfs/random.bin"].first + k]][3] for k in range(3)}
            self.assertEqual(codecs, {0})
            same_blocks = {a.refs[b["romfs/same.bin"].first + k]
                           for k in range(b["romfs/same.bin"].count)}
            self.assertEqual(len(same_blocks), 2)  # 9 identical full chunks + the tail

    def test_zstd_frames(self):
        zstd = fs.zstd()
        with nx.NxArchive(self.archive) as a:
            frames = [(o, s, lg) for o, s, lg, codec, _f in a.blocks if codec == 1]
            self.assertTrue(frames)
            with open(self.archive, "rb") as f:
                for off, stored, logical in frames:
                    f.seek(off)
                    params = zstd.get_frame_parameters(f.read(stored))
                    self.assertEqual((params.content_size, params.dict_id), (logical, 0))
                    self.assertLessEqual(params.window_size, 1 << 20)

    def test_extract_and_roundtrip(self):
        back = titles.read_titles(self.archive, None)
        self.assertEqual(sorted((t.kind, t.version) for t in back),
                         [("dlc", 0), ("game", 0), ("update", 65536)])
        game = titles.group_titles(back)[TID]
        self.assertEqual((game.name, game.base.name), ("Gioco Prova", "Gioco Prova"))
        dest = extract.extract(game, str(self.d / "x"), None, log=lambda m: None)
        for path, data in self.new_rom.items():
            self.assertEqual(pathlib.Path(dest, "romfs", path).read_bytes(), data, path)
        dest = extract.extract(game, str(self.d / "xb"), None, use_update=False,
                               log=lambda m: None)
        for path, data in self.base_rom.items():
            self.assertEqual(pathlib.Path(dest, "romfs", path).read_bytes(), data, path)
        self.assertEqual(jobs.xci_jobs([game], str(self.d / "o")), [])  # nothing to encrypt
        (to_nup,) = jobs.nup_jobs([game], str(self.d / "nup"))
        to_nup.run(None, lambda *a: None, lambda: False, lambda m: None)
        again = titles.group_titles(titles.read_titles(to_nup.target, None))[TID]
        (to_nx,) = jobs.nx_jobs([again], str(self.d / "nx2"))
        to_nx.run(None, lambda *a: None, lambda: False, lambda m: None)
        with nx.NxArchive(self.archive) as a1, nx.NxArchive(to_nx.target) as a2:
            ids1 = [s.identity for s in a1.applications[0].snapshots]
            ids2 = [s.identity for s in a2.applications[0].snapshots]
            self.assertEqual(ids1[:2], ids2[:2])  # same trees, same canonical identities

    def test_damage_is_detected(self):
        raw = bytearray(pathlib.Path(self.archive).read_bytes())
        with nx.NxArchive(self.archive) as a:
            raw_block = next(b for b in a.blocks if b[3] == 0 and b[2] > 1000)
        raw[raw_block[0] + 500] ^= 1  # inside the block's data
        bad = self.d / "bad.nx"
        bad.write_bytes(raw)
        with nx.NxArchive(str(bad)) as a:  # structure intact: mounts, reads fail
            self.assertIn("mismatch", [s for _p, s in a.verify()])
        raw = bytearray(pathlib.Path(self.archive).read_bytes())
        raw[10] ^= 1  # slot A damaged: slot B still opens the archive
        bad.write_bytes(raw)
        with nx.NxArchive(str(bad)) as a:
            self.assertEqual(len(a.applications), 1)

    def test_switch2_nup_and_options(self):
        packages = [nup.NewPackage(nup.KIND_BASE, 0x0400ABCD00010000, 0,
                                   exefs={"main.npdm": MemoryStorage(b"META")},
                                   romfs={"a.bin": MemoryStorage(b"x" * 100)},
                                   meta={nup.TLV_TITLE_NAME: b"Switch 2"})]
        path = nup.write_nup(str(self.d / "s2.nup"), packages)
        game = titles.group_titles(titles.read_titles(path, None))[0x0400ABCD00010000]
        (job,) = jobs.nx_jobs([game], str(self.d / "s2"), compress=False)
        job.run(None, lambda *a: None, lambda: False, lambda m: None)
        with nx.NxArchive(job.target) as a:
            self.assertEqual(a.applications[0].platform, nx.PLATFORM_SWITCH2)
            self.assertEqual(a.applications[0].title, "Switch 2")
            self.assertEqual({b[3] for b in a.blocks}, {0})  # --no-compress: RAW only

    def test_writer_rules(self):
        w = nx.NxWriter(str(self.d / "names.nx"))
        fid = w.add_file("x", MemoryStorage(b"1"))
        with self.assertRaises(nx.NxError):
            w.finish([nx.NewApplication(1, 1, [nx.NewSnapshot(0, 0, {"a/../b": fid})])])
        self.assertFalse(os.path.exists(str(self.d / "names.nx.part")))
        w = nx.NxWriter(str(self.d / "cancel.nx"), should_cancel=lambda: True)
        with self.assertRaises(Cancelled):
            w.add_file("big", MemoryStorage(bytes(1 << 20)))
        w.abort()
        self.assertFalse(os.path.exists(str(self.d / "cancel.nx.part")))
        # sources are streamed: a large NUP file is never read whole (regression)
        class StreamOnly(fs.Storage):
            def __init__(self, data):
                self.data, self.size = data, len(data)

            def chunks(self, offset=0, size=None, chunk=fs.CHUNK):
                for i in range(0, self.size, 100_000):  # odd-sized pieces
                    yield self.data[i:i + 100_000]

            def read(self, offset, size):
                raise AssertionError("whole-file read")

        payload = os.urandom(700_000) + bytes(300_000)
        w = nx.NxWriter(str(self.d / "stream.nx"))
        fid = w.add_file("s", StreamOnly(payload))
        out = w.finish([nx.NewApplication(1, 1, [nx.NewSnapshot(0, 0, {"s": fid})])])
        with nx.NxArchive(out) as a:
            self.assertEqual(b"".join(a.open(fid).chunks()), payload)
        # identical chunks are stored once even in different files
        w = nx.NxWriter(str(self.d / "dedup.nx"), compress=False)
        data = os.urandom(300_000)
        ids = {p: w.add_file(p, MemoryStorage(data)) for p in ("one", "two")}
        out = w.finish([nx.NewApplication(1, 1, [nx.NewSnapshot(0, 0, ids)])])
        with nx.NxArchive(out) as a:
            self.assertEqual(len(a.blocks), 2)
            self.assertEqual(len(a.files), 2)

    def test_cli(self):
        os.environ["SWITCHTK_KEYS"] = str(self.d / "missing.keys")
        try:
            self.assertEqual(cli.main(["info", self.archive, "--files"]), 0)
            self.assertEqual(cli.main(["verify", self.archive]), 0)
            self.assertEqual(cli.main(["extract", self.archive, "-o", str(self.d / "cli")]), 0)
        finally:
            del os.environ["SWITCHTK_KEYS"]
        self.assertEqual(cli.main(["--keys", str(self.d / "prod.keys"), "nx",
                                   str(self.d / "base.nsp"), "-o", str(self.d / "cli-nx"),
                                   "--level", "1"]), 0)
        (made,) = (self.d / "cli-nx").iterdir()
        self.assertEqual(outcome(made), "accept")


if __name__ == "__main__":
    unittest.main()
