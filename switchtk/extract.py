"""ExeFS / RomFS of a game, with its latest update applied when present, from NSP/XCI
(decrypting the NCAs) or NUP (plain files). Used by the extraction and by the NUP builder."""

from __future__ import annotations

import os
import re
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import meta, nup
from .fs import Cancelled, FormatError, Storage
from .i18n import tr
from .keys import KeySet
from .meta import tid_str
from .nca import Nca
from .titles import Container, Game, Title, version_label
from .xci import safe_name


def _safe_part(part: str) -> str:
    part = re.sub(r'[<>:"\\|?*\x00-\x1f]', "_", part)
    return "_" if part in ("", ".", "..") else part


@dataclass
class Layer:
    """The files one title contributes: a base game, an update or a DLC."""
    exefs: dict[str, Storage] = field(default_factory=dict)
    romfs: dict[str, Storage] = field(default_factory=dict)
    romfs_image: Optional[Storage] = None   # the whole RomFS (NCA sources only)
    nca: Optional[Nca] = None
    overlay: bool = False                   # NUP update: shadows the base files one by one


def _nca(stack: ExitStack, title: Title, keys: KeySet, content_type: int) -> Nca:
    c = stack.enter_context(Container(title.path, keys))
    rec = next((r for r in title.cnmt.contents if r.type == content_type), None)
    name = c.nca_file(rec.nca_id) if rec else None
    if name is None:
        raise FormatError(tr("{name}: NCA del programma non trovato.",
                             name=os.path.basename(title.path)))
    return c.nca(name)


def open_layer(stack: ExitStack, title: Title, keys: Optional[KeySet],
               base: Optional[Layer] = None, exefs: bool = True, romfs: bool = True,
               log: Callable[[str], None] = print) -> Layer:
    if title.container == "nup":
        n = stack.enter_context(nup.NupFile(title.path))
        pkg = n.packages[title.nup_index]
        return Layer(n.tree(pkg, "exefs") if exefs else {},
                     n.tree(pkg, "romfs") if romfs else {}, overlay=True)
    if keys is None:
        raise FormatError(tr("File prod.keys non trovato. Ricavalo dalla tua console con "
                             "Lockpick_RCM e indicalo nelle impostazioni (o con --keys)."))
    if title.kind == "dlc":
        nca = _nca(stack, title, keys, 2)  # data
        fs = nca.romfs()
        return Layer({}, {f.path: fs.open(f) for f in fs.files()}, fs.storage, nca)
    nca = _nca(stack, title, keys, meta.CONTENT_PROGRAM)
    layer = Layer(nca=nca)
    if exefs:
        s = nca.exefs_section()
        if s is None:
            raise FormatError(tr("ExeFS non trovata."))
        pfs = nca.pfs0(s)
        layer.exefs = {e.name: pfs.open(e.name) for e in pfs.entries}
    if romfs:
        if title.kind == "update" and (base is None or base.nca is None):
            log(tr("ATTENZIONE: senza il gioco base la RomFS dell'aggiornamento non può "
                   "essere ricostruita: estraggo solo la ExeFS."))
        else:
            fs = nca.romfs(base=base.nca) if title.kind == "update" else nca.romfs()
            layer.romfs = {f.path: fs.open(f) for f in fs.files()}
            layer.romfs_image = fs.storage
    return layer


def merge(base: Optional[Layer], update: Optional[Layer]) -> Layer:
    """The files of the updated game."""
    if update is None:
        return base or Layer()
    if base is None:
        return update
    if update.overlay:  # NUP: path by path
        return Layer({**base.exefs, **update.exefs}, {**base.romfs, **update.romfs})
    # NCA update: complete ExeFS and complete (rebuilt) RomFS
    return Layer(update.exefs or base.exefs, update.romfs or base.romfs,
                 update.romfs_image if update.romfs else base.romfs_image)


def dest_dir(game: Game, out_dir: str) -> str:
    return os.path.join(out_dir, f"{safe_name(game.name)} [{tid_str(game.title_id)}]")


def write_files(dest: str, jobs: list[tuple[str, Storage]],
                progress: Optional[Callable[[int, int, str], None]] = None,
                should_cancel: Optional[Callable[[], bool]] = None) -> None:
    total = sum(st.size for _p, st in jobs)
    done = 0
    for rel, st in jobs:
        if should_cancel and should_cancel():
            raise Cancelled()
        path = os.path.join(dest, *(_safe_part(p) for p in rel.split("/")))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as out:
            for chunk in st.chunks():
                if should_cancel and should_cancel():
                    raise Cancelled()
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total, rel)
        if progress and not st.size:
            progress(done, total, rel)
    if progress:
        progress(total, total, "")


def extract(game: Game, out_dir: str, keys: Optional[KeySet], exefs: bool = True,
            romfs: bool = True, romfs_bin: bool = False, use_update: bool = True,
            progress: Optional[Callable[[int, int, str], None]] = None,
            should_cancel: Optional[Callable[[], bool]] = None,
            log: Callable[[str], None] = print) -> str:
    """Write exefs/ and romfs/ (or romfs.bin) into '<out_dir>/<Name> [TID]'. Returns that folder."""
    dest = dest_dir(game, out_dir)
    update = game.latest_update if use_update else None
    with ExitStack() as stack:
        if game.base is None and update is None:
            raise FormatError(tr("Niente da estrarre: aggiungi il gioco base."))
        base = open_layer(stack, game.base, keys, None, exefs, romfs, log) if game.base else None
        patch = open_layer(stack, update, keys, base, exefs, romfs, log) if update else None
        if update is not None:
            log(tr("Applico l'aggiornamento {version}.", version=version_label(update)))
        final = merge(base, patch)
        if exefs and not final.exefs:
            raise FormatError(tr("ExeFS non trovata."))
        jobs: list[tuple[str, Storage]] = [("exefs/" + p, st) for p, st in final.exefs.items()]
        if romfs_bin and final.romfs_image is not None:
            jobs.append(("romfs.bin", final.romfs_image))
        else:
            if romfs_bin and final.romfs:
                log(tr("I NUP contengono i singoli file: estraggo la cartella romfs invece di "
                       "romfs.bin."))
            jobs += [("romfs/" + p, st) for p, st in final.romfs.items()]
        write_files(dest, jobs, progress, should_cancel)
    log(tr("Estratti {n} file in {dest}", n=len(jobs), dest=dest))
    return dest
