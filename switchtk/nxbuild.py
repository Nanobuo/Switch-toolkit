"""NSP/XCI, NUP or NX titles → NX archive.

Each snapshot is a complete view, as the format requires: the base game; the updated game
(update ExeFS and the RomFS rebuilt from base + patch, or the NUP update shadowing the base
files); every DLC. Unchanged files keep one NxFile record across snapshots and identical
chunks one block, so a base + update archive is not much larger than the update alone.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from typing import Callable, Optional

from . import nx
from .extract import Layer, merge, open_layer
from .fs import FormatError
from .i18n import tr
from .keys import KeySet
from .meta import Nacp
from .titles import Title
from .xci import default_name


def nx_name(titles: list[Title]) -> str:
    return default_name(titles)[:-len(".xci")] + ".nx"


def platform_of(title: Title, layer: Layer) -> int:
    """Switch generation, only from reliable evidence (as the format asks)."""
    if title.platform is not None:              # an NX source states it
        return title.platform
    if title.container in ("nsp", "xci"):       # decrypted here with Switch 1 keys
        return nx.PLATFORM_SWITCH1
    prefix = title.base_id >> 56                # NUP: same rule as the reference converter
    npdm = layer.exefs.get("main.npdm")
    if prefix == 0x04:
        return nx.PLATFORM_SWITCH2
    if prefix == 0x01 and npdm is not None and npdm.read(0, 4) == b"META":
        return nx.PLATFORM_SWITCH1
    return nx.PLATFORM_UNKNOWN


def display_meta(title: Title) -> nx.Meta:
    """Native display metadata: NACP strings (first populated language is the default),
    per-language titles and icons, original NACP bytes."""
    meta = nx.Meta()
    nacp: Optional[Nacp] = title.nacp
    if nacp is not None:
        for i in range(len(nx.NACP_LANGS)):
            name, publisher = nacp.titles.get(i, ("", ""))
            if name and not meta.title:
                meta.title = name
            if publisher and not meta.publisher:
                meta.publisher = publisher
        for i, tag in enumerate(nx.NACP_LANGS):
            name = nacp.titles.get(i, ("", ""))[0]
            icon = title.icons.get(i)
            if name or icon:
                meta.localized[tag] = (name, icon)
    meta.icon = title.icons.get(0) or next(iter(title.icons.values()), None) or title.icon
    meta.nacp = title.nacp_raw
    return meta


def build_id(layer: Layer) -> bytes:
    main = layer.exefs.get("main")
    if main is not None and main.size >= 0x60:
        head = main.read(0, 0x60)
        if head[:4] in (b"NSO0", b"NRO0"):
            return head[0x40:0x60]
    return bytes(32)


def build_nx(titles: list[Title], out_path: str, keys: Optional[KeySet], level: int = 3,
             compress: bool = True,
             progress: Optional[Callable[[int, int, str], None]] = None,
             should_cancel: Optional[Callable[[], bool]] = None,
             log: Callable[[str], None] = print) -> str:
    """One NX with the game, its latest update and its DLC among titles."""
    base = next((t for t in titles if t.kind == "game"), None)
    update = max((t for t in titles if t.kind == "update"), key=lambda t: t.version, default=None)
    dlcs = sorted((t for t in titles if t.kind == "dlc"), key=lambda t: t.title_id)
    if base is None and update is None and not dlcs:
        raise FormatError(tr("Nessun contenuto da convertire."))
    log(tr("Preparazione di {name}…", name=os.path.basename(out_path)))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with ExitStack() as stack:
        views: list[tuple[Title, int, Layer]] = []
        base_layer = None
        if base is not None:
            base_layer = open_layer(stack, base, keys, log=log)
            views.append((base, nx.SNAP_BASE, base_layer))
        if update is not None:
            layer = open_layer(stack, update, keys, base_layer, log=log)
            kind = nx.SNAP_UPDATE if base is not None else nx.SNAP_STANDALONE
            views.append((update, kind, merge(base_layer, layer)))  # complete updated view
        for dlc in dlcs:
            views.append((dlc, nx.SNAP_DLC, open_layer(stack, dlc, keys, log=log)))

        main = next((v for v in views if v[1] != nx.SNAP_DLC), views[0])
        app_id = main[0].base_id
        platform = platform_of(main[0], main[2])
        trees = [v[2].tree() for v in views]
        total = sum(st.size for tree in trees for st in tree.values())
        writer = nx.NxWriter(out_path, level=level, compress=compress,
                             should_cancel=should_cancel)
        try:
            current = [""]

            def report(done: int) -> None:
                if progress:
                    progress(done, total, current[0])

            app = nx.NewApplication(app_id, platform)
            for (title, kind, layer), tree in zip(views, trees):
                ids = {}
                for path in sorted(tree):
                    current[0] = path
                    ids[path] = writer.add_file(path, tree[path], report)
                snap = nx.NewSnapshot(kind, title.version, ids,
                                      display_version=title.display_version)
                if kind == nx.SNAP_DLC:
                    snap.executable = False
                    snap.addon_title_id = title.title_id
                else:
                    snap.build_id = build_id(layer)
                    snap.meta = display_meta(title)
                app.snapshots.append(snap)
            path = writer.finish([app])
        except BaseException:
            writer.abort()
            raise
    if progress:
        progress(total, total, "")
    return path

