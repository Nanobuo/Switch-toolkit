"""What to do with the loaded games: one job per XCI/NUP to create or per game to extract."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import extract, nupbuild, nxbuild, xci
from .keys import KeySet
from .titles import Game, Title, group_titles


@dataclass
class Job:
    kind: str                     # xci | nup | nx | extract
    label: str                    # shown in the queue
    target: str                   # XCI/NUP/NX file or extraction folder
    weight: int                   # bytes, for the overall percentage
    titles: list[Title] = field(default_factory=list)
    game: Optional[Game] = None
    options: dict = field(default_factory=dict)

    def run(self, keys: KeySet, progress: Callable[[int, int, str], None],
            should_cancel: Callable[[], bool], log: Callable[[str], None]) -> str:
        if self.kind == "xci":
            return xci.build_xci(self.titles, self.target, keys, progress=progress,
                                 should_cancel=should_cancel, log=log)
        if self.kind == "nup":
            return nupbuild.build_nup(self.titles, self.target, keys, progress=progress,
                                      should_cancel=should_cancel, log=log, **self.options)
        if self.kind == "nx":
            return nxbuild.build_nx(self.titles, self.target, keys, progress=progress,
                                    should_cancel=should_cancel, log=log, **self.options)
        return extract.extract(self.game, os.path.dirname(self.target), keys,
                               progress=progress, should_cancel=should_cancel, log=log,
                               **self.options)


def _size(paths) -> int:
    return sum(os.path.getsize(p) for p in set(paths) if os.path.exists(p))


def xci_jobs(games: list[Game], out_dir: str, merge: bool = True, include_update: bool = True,
             include_dlc: bool = True) -> list[Job]:
    """NUP and NX sources are left out: they hold no encrypted NCAs to put in an XCI."""
    return _container_jobs("xci", games, out_dir, merge, include_update, include_dlc)


def nup_jobs(games: list[Game], out_dir: str, merge: bool = True, include_update: bool = True,
             include_dlc: bool = True, level: int = 3) -> list[Job]:
    return _container_jobs("nup", games, out_dir, merge, include_update, include_dlc,
                           dict(level=level))


def nx_jobs(games: list[Game], out_dir: str, merge: bool = True, include_update: bool = True,
            include_dlc: bool = True, level: int = 3, compress: bool = True) -> list[Job]:
    return _container_jobs("nx", games, out_dir, merge, include_update, include_dlc,
                           dict(level=level, compress=compress))


def _container_jobs(kind: str, games: list[Game], out_dir: str, merge: bool,
                    include_update: bool, include_dlc: bool,
                    options: Optional[dict] = None) -> list[Job]:
    jobs = []
    for game in games:
        titles = [t for t in game.all_titles() if kind != "xci" or not decrypted(t)]
        if not titles:
            continue
        if merge:
            usable = next(iter(group_titles(titles).values()))
            groups = [[t for t in usable.merged()
                       if (include_update or t.kind != "update")
                       and (include_dlc or t.kind != "dlc")]]
        else:  # plain conversion: one output per input file
            by_file: dict[str, list[Title]] = {}
            for t in titles:
                by_file.setdefault(t.path, []).append(t)
            groups = list(by_file.values())
        for content in groups:
            if not content:
                continue
            name = {"xci": xci.default_name, "nup": nupbuild.nup_name,
                    "nx": nxbuild.nx_name}[kind](content)
            jobs.append(Job(kind, name, os.path.join(out_dir, name),
                            _size(t.path for t in content), content,
                            options=dict(options or {})))
    return jobs


def decrypted(title: Title) -> bool:
    """NUP and NX hold decrypted files: no NCA to put in an XCI."""
    return title.container in ("nup", "nx")


def has_decrypted(games: list[Game]) -> bool:
    return any(decrypted(t) for g in games for t in g.all_titles())


def extract_jobs(games: list[Game], out_dir: str, exefs: bool = True, romfs: bool = True,
                 romfs_bin: bool = False, apply_update: bool = True) -> list[Job]:
    jobs = []
    for game in games:
        if game.base is None and (not apply_update or game.latest_update is None):
            continue
        used = [t for t in (game.base, game.latest_update if apply_update else None) if t]
        target = extract.dest_dir(game, out_dir)
        jobs.append(Job("extract", os.path.basename(target), target,
                        _size(t.path for t in used), game=game,
                        options=dict(exefs=exefs, romfs=romfs, romfs_bin=romfs_bin,
                                     use_update=apply_update)))
    return jobs
