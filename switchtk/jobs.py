"""What to do with the loaded games: one job per XCI to create or per game to extract."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import extract, xci
from .keys import KeySet
from .titles import Game, Title


@dataclass
class Job:
    kind: str                     # xci | extract
    label: str                    # shown in the queue
    target: str                   # XCI file or extraction folder
    weight: int                   # bytes, for the overall percentage
    titles: list[Title] = field(default_factory=list)
    game: Optional[Game] = None
    options: dict = field(default_factory=dict)

    def run(self, keys: KeySet, progress: Callable[[int, int, str], None],
            should_cancel: Callable[[], bool], log: Callable[[str], None]) -> str:
        if self.kind == "xci":
            return xci.build_xci(self.titles, self.target, keys, progress=progress,
                                 should_cancel=should_cancel, log=log)
        return extract.extract(self.game, os.path.dirname(self.target), keys,
                               progress=progress, should_cancel=should_cancel, log=log,
                               **self.options)


def _size(paths) -> int:
    return sum(os.path.getsize(p) for p in set(paths) if os.path.exists(p))


def xci_jobs(games: list[Game], out_dir: str, merge: bool = True, include_update: bool = True,
             include_dlc: bool = True) -> list[Job]:
    jobs = []
    for game in games:
        if merge:
            groups = [[t for t in game.merged()
                       if (include_update or t.kind != "update")
                       and (include_dlc or t.kind != "dlc")]]
        else:  # plain conversion: one XCI per input file
            by_file: dict[str, list[Title]] = {}
            for t in game.all_titles():
                by_file.setdefault(t.path, []).append(t)
            groups = list(by_file.values())
        for content in groups:
            if not content:
                continue
            name = xci.default_name(content)
            jobs.append(Job("xci", name, os.path.join(out_dir, name),
                            _size(t.path for t in content), content))
    return jobs


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
