"""Command line interface."""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from . import __version__, i18n, jobs, nup, nx, titles, xci
from .fs import Cancelled, FormatError
from .i18n import tr
from .keys import KeySet, KeysError
from .meta import tid_str


def _keys(args) -> Optional[KeySet]:
    """The keys; NUP/NX files are plain, so without prod.keys they still work on their own."""
    try:
        return KeySet.load(args.keys, args.title_keys)
    except KeysError:
        paths = titles.collect_files(getattr(args, "files", []))
        if paths and all(_plain(p) for p in paths) and not args.keys:
            return None
        raise


def _plain(path: str) -> bool:
    return nup.is_nup(path) or nx.is_nx(path)


def _load(args, ks: KeySet) -> list[titles.Title]:
    paths = titles.collect_files(args.files)
    if not paths:
        raise FormatError(tr("Nessun file NSP/XCI/NUP/NX trovato."))
    out = []
    for path in paths:
        out += titles.read_titles(path, ks)
    return out


class _Progress:
    def __init__(self):
        self.last = 0.0
        self.ended = False

    def __call__(self, done, total, name=""):
        now = time.monotonic()
        finished = done >= total
        if self.ended or (now - self.last < 0.2 and not finished):
            return
        self.last = now
        self.ended = finished
        pct = 100 * done // total if total else 100
        sys.stdout.write(f"\r    {pct:3d}%  {name[-60:]:<60}")
        if finished:
            sys.stdout.write("\n")
        sys.stdout.flush()


def _cmd_info(args) -> int:
    ks = _keys(args)
    for path in titles.collect_files(args.files):
        print(path)
        if nup.is_nup(path):
            _info_nup(path, args.files_list)
            continue
        if nx.is_nx(path):
            _info_nx(path, args.files_list)
            continue
        with titles.Container(path, ks) as c:
            for t in c.titles():
                print(f"  {titles.kind_label(t.kind):<16} {tid_str(t.title_id)}  "
                      f"{titles.version_label(t)}  {t.name}")
            if args.files_list:
                for name, st in c.files.items():
                    detail = ""
                    if name.endswith(".nca"):
                        nca = c.nca(name)
                        detail = f"  {nca.content_type}, keygen {nca.key_generation}"
                        if nca.has_rights_id:
                            detail += f", rights id {nca.rights_id.hex().upper()}"
                    print(f"    {name}  ({st.size} B){detail}")
    return 0


def _info_nup(path: str, files: bool) -> None:
    for t in titles.nup_titles(path):
        print(f"  {titles.kind_label(t.kind):<16} {tid_str(t.title_id)}  "
              f"{titles.version_label(t)}  {t.name}")
    if files:
        with nup.NupFile(path) as n:
            for pkg in n.packages:
                print(f"    [{pkg.title_id:016X} v{pkg.version}]")
                for which in ("exefs", "romfs"):
                    for rel, e in sorted(n.tree(pkg, which).items()):
                        print(f"    {which}/{rel}  ({e.size} B)")


def _info_nx(path: str, files: bool) -> None:
    for t in titles.nx_titles(path):
        print(f"  {titles.kind_label(t.kind):<16} {tid_str(t.title_id)}  "
              f"{titles.version_label(t)}  {t.name}")
    if files:
        with nx.NxArchive(path) as a:
            for app in a.applications:
                for s in app.snapshots:
                    print(f"    [{app.application_id:016X} v{s.version} · "
                          f"{s.identity.hex()[:16]}]")
                    for rel, st in sorted(a.tree(s.root).items()):
                        print(f"    {rel}  ({st.size} B)")


def _cmd_xci(args) -> int:
    ks = _keys(args)
    games = list(titles.group_titles(_load(args, ks)).values())
    if not args.no_merge:
        for game in games:
            for w in game.warnings():
                print(tr("ATTENZIONE: {message}", message=w))
    todo = jobs.xci_jobs(games, os.path.abspath(args.output), merge=not args.no_merge,
                         include_update=not args.no_update, include_dlc=not args.no_dlc)
    if jobs.has_decrypted(games):
        print(tr("ATTENZIONE: {message}", message=tr(
            "i file NUP e NX non possono diventare XCI (contengono dati già decifrati, senza "
            "gli NCA): vengono ignorati.")))
    for job in todo:
        print(tr("Creo {name}", name=job.target))
        xci.build_xci(job.titles, job.target, ks, progress=_Progress(), log=lambda _m: None,
                      check_space=not args.force)
    return 0


def _cmd_archive(args) -> int:
    ks = _keys(args)
    games = list(titles.group_titles(_load(args, ks)).values())
    common = dict(merge=not args.no_merge, include_update=not args.no_update,
                  include_dlc=not args.no_dlc, level=args.level)
    if args.cmd == "nx":
        todo = jobs.nx_jobs(games, os.path.abspath(args.output), compress=not args.no_compress,
                            **common)
    else:
        todo = jobs.nup_jobs(games, os.path.abspath(args.output), **common)
    for job in todo:
        print(tr("Creo {name}", name=job.target))
        job.run(ks, _Progress(), lambda: False, lambda _m: None)
    return 0


def _cmd_extract(args) -> int:
    ks = _keys(args)
    games = list(titles.group_titles(_load(args, ks)).values())
    todo = jobs.extract_jobs(games, os.path.abspath(args.output), exefs=not args.no_exefs,
                             romfs=not args.no_romfs, romfs_bin=args.romfs_bin,
                             apply_update=not args.no_update)
    if not todo:
        raise FormatError(tr("Niente da estrarre: aggiungi il gioco base."))
    for job in todo:
        print(job.target)
        job.run(ks, _Progress(), lambda: False, lambda _m: None)
    return 0


def _cmd_verify(args) -> int:
    ks = _keys(args)
    bad = False
    labels = {"ok": "OK", "mismatch": tr("NON CORRISPONDE"), "missing": tr("MANCANTE")}
    for path in titles.collect_files(args.files):
        print(path)
        for name, status in titles.verify(path, ks):
            print(f"  {name:<44} {labels[status]}")
            bad |= status != "ok"
        if path.lower().endswith(".xci") and bad:
            print("  " + tr("Nota: negli XCI creati da un NSP l'intestazione degli NCA è "
                            "modificata, quindi l'impronta non corrisponde più."))
    return 2 if bad else 0


def main(argv=None) -> int:
    i18n.set_language(i18n.resolve(os.environ.get("SWITCHTK_LANG", "auto")))
    parser = argparse.ArgumentParser(
        prog="switchtk",
        description=tr("Coltellino svizzero per i file Nintendo Switch: NSP → XCI, NUP o NX, "
                       "unione di gioco, aggiornamenti e DLC, estrazione di ExeFS e RomFS."))
    parser.add_argument("--version", action="version",
                        version=f"{__version__} (zstd {nup.zstd_version() or '-'}, "
                                f"crc32c {nx.CRC_BACKEND})")
    parser.add_argument("--keys", help=tr("file prod.keys (predefinito: cercato nelle cartelle "
                                          "abituali)"))
    parser.add_argument("--title-keys", help=tr("file title.keys facoltativo"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help=tr("mostra i contenuti di file NSP/XCI/NUP/NX"))
    p_info.add_argument("files", nargs="+")
    p_info.add_argument("--files", dest="files_list", action="store_true",
                        help=tr("elenca anche gli NCA contenuti"))
    p_info.set_defaults(func=_cmd_info)

    p_xci = sub.add_parser("xci", help=tr("converte in XCI, unendo gioco, aggiornamento e DLC "
                                          "dello stesso gioco"))
    p_xci.add_argument("files", nargs="+", help=tr("file NSP/XCI o cartelle (in qualsiasi "
                                                   "ordine)"))
    p_xci.add_argument("-o", "--output", required=True, help=tr("cartella di destinazione"))
    p_xci.add_argument("--no-merge", action="store_true",
                       help=tr("un XCI per ogni file, senza unire"))
    p_xci.add_argument("--no-update", action="store_true", help=tr("non includere "
                                                                   "l'aggiornamento"))
    p_xci.add_argument("--no-dlc", action="store_true", help=tr("non includere i DLC"))
    p_xci.add_argument("--force", action="store_true",
                       help=tr("non controllare lo spazio libero"))
    p_xci.set_defaults(func=_cmd_xci)

    for name, text, merge_help, top in (
            ("nup", tr("crea un NUP (file decifrati e compressi) con gioco, aggiornamento e "
                       "DLC"), tr("un NUP per ogni file, senza unire"), 19),
            ("nx", tr("crea un archivio NX (file decifrati, blocchi compressi e deduplicati) "
                      "con gioco, aggiornamento e DLC"), tr("un NX per ogni file, senza unire"),
             13)):
        p_arc = sub.add_parser(name, help=text)
        p_arc.add_argument("files", nargs="+", help=tr("file NSP/XCI/NUP/NX o cartelle (in "
                                                       "qualsiasi ordine)"))
        p_arc.add_argument("-o", "--output", required=True, help=tr("cartella di destinazione"))
        p_arc.add_argument("--no-merge", action="store_true", help=merge_help)
        p_arc.add_argument("--no-update", action="store_true",
                           help=tr("non includere l'aggiornamento"))
        p_arc.add_argument("--no-dlc", action="store_true", help=tr("non includere i DLC"))
        p_arc.add_argument("--level", type=int, default=3, choices=range(1, top + 1),
                           metavar=f"1-{top}",
                           help=tr("livello di compressione zstd (predefinito 3)"))
        if name == "nx":
            p_arc.add_argument("--no-compress", action="store_true",
                               help=tr("blocchi non compressi: archivio più grande, letture "
                                       "più veloci"))
        p_arc.set_defaults(func=_cmd_archive)

    p_ext = sub.add_parser("extract", help=tr("estrae ExeFS e RomFS (con l'aggiornamento "
                                              "applicato)"))
    p_ext.add_argument("files", nargs="+", help=tr("file NSP/XCI o cartelle (in qualsiasi "
                                                   "ordine)"))
    p_ext.add_argument("-o", "--output", required=True, help=tr("cartella di destinazione"))
    p_ext.add_argument("--no-exefs", action="store_true", help=tr("non estrarre la ExeFS"))
    p_ext.add_argument("--no-romfs", action="store_true", help=tr("non estrarre la RomFS"))
    p_ext.add_argument("--romfs-bin", action="store_true",
                       help=tr("salva la RomFS come unico file romfs.bin"))
    p_ext.add_argument("--no-update", action="store_true",
                       help=tr("estrai il gioco base senza applicare l'aggiornamento"))
    p_ext.set_defaults(func=_cmd_extract)

    p_ver = sub.add_parser("verify", help=tr("verifica l'integrità di NSP, NUP e NX"))
    p_ver.add_argument("files", nargs="+")
    p_ver.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FormatError, KeysError, OSError) as ex:
        print(tr("ERRORE: {message}", message=ex), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, Cancelled):
        print("\n" + tr("Annullato."), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
