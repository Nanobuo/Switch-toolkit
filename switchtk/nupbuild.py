"""NSP/XCI (or NUP) titles → NUP: decrypted ExeFS/RomFS of the game, its update and its DLC,
with the metadata (NPDM, NACP, name, icon, version) the format keeps."""

from __future__ import annotations

import os
from contextlib import ExitStack
from typing import Callable, Optional

from . import nup
from .extract import Layer, open_layer
from .fs import FormatError
from .i18n import tr
from .keys import KeySet
from .titles import Title
from .xci import default_name


def nup_name(titles: list[Title]) -> str:
    return default_name(titles)[:-len(".xci")] + ".nup"


def _small(layer: Layer, name: str, limit: int = 1 << 20) -> Optional[bytes]:
    st = layer.exefs.get(name)
    return st.read(0, st.size) if st is not None and st.size <= limit else None


def _data(layer: Layer) -> dict:
    """Files outside exefs/romfs go below the package's data/ directory."""
    return {p[len("data/"):] if p.startswith("data/") else p: st for p, st in layer.data.items()}


def package_meta(title: Title, layer: Layer, app: bool) -> dict[int, bytes]:
    m = {nup.TLV_SOURCE: os.path.basename(title.path).encode("utf-8")}
    npdm = _small(layer, "main.npdm")
    if npdm:
        m[nup.TLV_NPDM] = npdm
    main = layer.exefs.get("main")
    if main is not None and main.size >= 0x60:
        head = main.read(0, 0x60)
        if head[:4] in (b"NSO0", b"NRO0"):
            m[nup.TLV_BUILD_ID] = head[0x40:0x60]
    if title.nacp_raw:
        m[nup.TLV_NACP] = title.nacp_raw
    if title.display_version:
        m[nup.TLV_DISPLAY_VERSION] = title.display_version.encode("utf-8")
    if app and title.nacp is not None:
        if title.nacp.name():
            m[nup.TLV_TITLE_NAME] = title.nacp.name().encode("utf-8")
        if title.nacp.publisher():
            m[nup.TLV_PUBLISHER] = title.nacp.publisher().encode("utf-8")
        names = {nup.NACP_LANGS[i]: n.encode("utf-8")
                 for i, (n, _p) in title.nacp.titles.items() if i < len(nup.NACP_LANGS) and n}
        if names:
            m[nup.TLV_LOCALIZED_TITLES] = nup.encode_localized(names)
    if app and title.icon:
        m[nup.TLV_ICON] = title.icon
    return m


def build_nup(titles: list[Title], out_path: str, keys: Optional[KeySet], level: int = 3,
              progress: Optional[Callable[[int, int, str], None]] = None,
              should_cancel: Optional[Callable[[], bool]] = None,
              log: Callable[[str], None] = print) -> str:
    """One NUP with the game, its latest update and its DLC among titles."""
    base = next((t for t in titles if t.kind == "game"), None)
    update = max((t for t in titles if t.kind == "update"), key=lambda t: t.version, default=None)
    dlcs = sorted((t for t in titles if t.kind == "dlc"), key=lambda t: t.title_id)
    if base is None and update is None and not dlcs:
        raise FormatError(tr("Nessun contenuto da convertire."))
    log(tr("Preparazione di {name}…", name=os.path.basename(out_path)))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with ExitStack() as stack:
        packages = []
        base_layer = None
        if base is not None:
            base_layer = open_layer(stack, base, keys, log=log)
            packages.append(nup.NewPackage(nup.KIND_BASE, base.title_id, base.version,
                                           exefs=base_layer.exefs, romfs=base_layer.romfs,
                                           meta=package_meta(base, base_layer, True),
                                           data=_data(base_layer)))
        if update is not None:
            layer = open_layer(stack, update, keys, base_layer, log=log)
            # The program id of an update is the game's (as in its main.npdm).
            tid = update.title_id if update.container in ("nup", "nx") else update.base_id
            flags = 0 if base is not None else nup.FLAG_STANDALONE_UPDATE
            packages.append(nup.NewPackage(nup.KIND_UPDATE, tid, update.version, flags,
                                           layer.exefs, layer.romfs,
                                           package_meta(update, layer, base is None),
                                           _data(layer)))
        for dlc in dlcs:
            layer = open_layer(stack, dlc, keys, log=log)
            packages.append(nup.NewPackage(nup.KIND_ADDON, dlc.title_id, dlc.version,
                                           romfs=layer.romfs,
                                           meta=package_meta(dlc, layer, False),
                                           data=_data(layer)))
        return nup.write_nup(out_path, packages, level, progress, should_cancel)
