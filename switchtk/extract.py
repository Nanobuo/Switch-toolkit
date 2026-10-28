"""ExeFS / RomFS extraction of a game, with its latest update applied when present."""

from __future__ import annotations

import os
import re
from contextlib import ExitStack
from typing import Callable, Optional

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


def _program(stack: ExitStack, title: Title, keys: KeySet) -> Nca:
    c = stack.enter_context(Container(title.path, keys))
    name = title.program_nca(c)
    if name is None:
        raise FormatError(tr("{name}: NCA del programma non trovato.",
                             name=os.path.basename(title.path)))
    return c.nca(name)


def dest_dir(game: Game, out_dir: str) -> str:
    return os.path.join(out_dir, f"{safe_name(game.name)} [{tid_str(game.title_id)}]")


def extract(game: Game, out_dir: str, keys: KeySet, exefs: bool = True, romfs: bool = True,
            romfs_bin: bool = False, use_update: bool = True,
            progress: Optional[Callable[[int, int, str], None]] = None,
            should_cancel: Optional[Callable[[], bool]] = None,
            log: Callable[[str], None] = print) -> str:
    """Write exefs/ and romfs/ (or romfs.bin) into '<out_dir>/<Name> [TID]'. Returns that folder."""
    dest = dest_dir(game, out_dir)
    update = game.latest_update if use_update else None
    with ExitStack() as stack:
        base = _program(stack, game.base, keys) if game.base else None
        patch = _program(stack, update, keys) if update else None
        if base is None and patch is None:
            raise FormatError(tr("Niente da estrarre: aggiungi il gioco base."))
        jobs: list[tuple[str, Storage]] = []
        if exefs:
            nca = patch or base
            s = nca.exefs_section()
            if s is None:
                raise FormatError(tr("ExeFS non trovata."))
            pfs = nca.pfs0(s)
            jobs += [("exefs/" + e.name, pfs.open(e.name)) for e in pfs.entries]
        if romfs:
            if patch is not None and base is None:
                log(tr("ATTENZIONE: senza il gioco base la RomFS dell'aggiornamento non può "
                       "essere ricostruita: estraggo solo la ExeFS."))
            else:
                fs = patch.romfs(base=base) if patch is not None else base.romfs()
                if romfs_bin:
                    jobs.append(("romfs.bin", fs.storage))
                else:
                    jobs += [("romfs/" + f.path, fs.open(f)) for f in fs.files()]
        if update is not None:
            log(tr("Applico l'aggiornamento {version}.", version=version_label(update)))
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
    log(tr("Estratti {n} file in {dest}", n=len(jobs), dest=dest))
    if progress:
        progress(total, total, "")
    return dest
