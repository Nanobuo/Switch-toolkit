"""Reading NSP/XCI/NUP/NX files into titles (game, update, DLC) and grouping them by game."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import meta, nup, nx
from .fs import Cancelled, FileStorage, FormatError, Partition, Storage, SubStorage
from .i18n import get_language, tr
from .keys import KeySet, KeysError
from .meta import Cnmt, ContentRecord, Nacp
from .nca import Nca, NcaError

EXTENSIONS = (".nsp", ".xci", ".nup", ".nx", ".nsz", ".xcz")


class Container:
    """An open NSP (PFS0) or XCI (secure HFS0 partition): its NCAs and tickets."""

    def __init__(self, path: str, keys: KeySet):
        self.path = path
        self.keys = keys
        self.f = open(path, "rb")
        try:
            self._open()
        except BaseException:
            self.f.close()
            raise

    def _open(self) -> None:
        raw = FileStorage(self.f)
        head = raw.read(0, 0x200)
        if head[:4] == b"PFS0":
            self.kind = "nsp"
            part = Partition(raw)
        else:
            start = next((o for o in (0, 0x1000) if raw.read(o + 0x100, 4) == b"HEAD"), None)
            if start is None:
                raise FormatError(tr("{name}: non è un file NSP o XCI valido.",
                                     name=os.path.basename(self.path)))
            self.kind = "xci"
            hdr = raw.read(start, 0x200)
            root_off = int.from_bytes(hdr[0x130:0x138], "little")
            root = Partition(SubStorage(raw, start + root_off, raw.size - start - root_off))
            if "secure" not in root.by_name:
                raise FormatError(tr("{name}: XCI senza partizione secure.",
                                     name=os.path.basename(self.path)))
            part = Partition(root.open("secure"))
        self.partition = part
        self.files: dict[str, Storage] = {e.name: part.open(e.name) for e in part.entries}
        if any(n.lower().endswith(".ncz") for n in self.files):
            raise FormatError(tr("{name}: file compresso (NSZ/XCZ). Decomprimilo prima, ad "
                                 "esempio con «nsz -D».", name=os.path.basename(self.path)))
        self.tickets: dict[bytes, bytes] = {}
        for name, st in self.files.items():
            if name.lower().endswith(".tik"):
                try:
                    t = meta.parse_ticket(st.read(0, st.size))
                except FormatError:
                    continue
                if not t.personalized:
                    self.tickets[t.rights_id] = t.title_key

    def close(self) -> None:
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def nca(self, name: str) -> Nca:
        return Nca(self.files[name], self.keys, self.tickets, name)

    def nca_file(self, nca_id: str) -> Optional[str]:
        for name in (f"{nca_id}.nca", f"{nca_id}.cnmt.nca"):
            if name in self.files:
                return name
        return None

    def titles(self) -> list["Title"]:
        out = []
        for name in sorted(self.files):
            if not name.lower().endswith(".cnmt.nca"):
                continue
            nca = self.nca(name)
            pfs = nca.pfs0(nca.sections[0])
            cnmt_name = next((e.name for e in pfs.entries if e.name.endswith(".cnmt")), None)
            if cnmt_name is None:
                continue
            st = pfs.open(cnmt_name)
            cnmt = meta.parse_cnmt(st.read(0, st.size))
            out.append(Title(self.path, self.kind, cnmt, name, self.files[name].size))
        if not out:
            raise FormatError(tr("{name}: nessun metadato CNMT trovato.",
                                 name=os.path.basename(self.path)))
        for t in out:
            t.load_control(self)
        return out


@dataclass
class Title:
    path: str                 # NSP/XCI/NUP/NX holding it
    container: str            # nsp | xci | nup | nx
    cnmt: Cnmt
    meta_file: str            # name of the .cnmt.nca
    meta_size: int
    nacp: Optional[Nacp] = None
    icon: Optional[bytes] = None
    missing: list[str] = field(default_factory=list)   # NCAs listed in the CNMT but absent
    nacp_raw: Optional[bytes] = None
    icons: dict[int, bytes] = field(default_factory=dict)  # NACP language index -> image
    nup_index: Optional[int] = None    # package record, for NUP titles
    nx_snapshot: Optional[int] = None  # Catalog snapshot index, for NX titles
    platform: Optional[int] = None     # NX platform, when the source states it
    group_id: Optional[int] = None     # game it belongs to, when not derivable from its id
    standalone: bool = False           # update stored without its base (NUP/NX)

    @property
    def title_id(self) -> int:
        return self.cnmt.title_id

    @property
    def kind(self) -> str:
        return self.cnmt.kind

    @property
    def version(self) -> int:
        return self.cnmt.version

    @property
    def base_id(self) -> int:
        if self.group_id is not None:
            return self.group_id
        return meta.base_title_id(self.title_id, self.kind)

    @property
    def name(self) -> str:
        if self.nacp is None:
            return ""
        return self.nacp.name(Nacp.ITALIAN if get_language() == "it" else None)

    @property
    def display_version(self) -> str:
        return self.nacp.display_version if self.nacp else ""

    @property
    def complete(self) -> bool:
        """An update that is a whole game by itself: every NX snapshot is a complete view,
        and a NUP standalone update holds the complete updated ExeFS and RomFS."""
        return self.kind == "update" and (self.container == "nx"
                                          or (self.container == "nup" and self.standalone))

    @property
    def key(self) -> tuple:
        return (self.title_id, self.version, self.kind)

    def files(self, container: Container) -> list[tuple[str, ContentRecord]]:
        """NCA files of this title in the container (delta fragments excluded), cnmt last."""
        out = []
        self.missing = []
        for rec in self.cnmt.contents:
            if rec.type == meta.CONTENT_DELTA:
                continue
            name = container.nca_file(rec.nca_id)
            if name is None:
                self.missing.append(rec.nca_id)
                continue
            out.append((name, rec))
        out.append((self.meta_file, ContentRecord(self.meta_file[:32], self.meta_size,
                                                  meta.CONTENT_META, b"")))
        return out

    def preferred_icon(self) -> Optional[bytes]:
        """Icon in the interface language, else American English, else the first one."""
        order = [Nacp.ITALIAN] if get_language() == "it" else []
        for i in order + [0] + sorted(self.icons):
            if i in self.icons:
                return self.icons[i]
        return None

    def program_nca(self, container: Container) -> Optional[str]:
        for rec in self.cnmt.contents:
            if rec.type == meta.CONTENT_PROGRAM:
                return container.nca_file(rec.nca_id)
        return None

    def load_control(self, container: Container) -> None:
        rec = next((r for r in self.cnmt.contents if r.type == 3), None)
        name = container.nca_file(rec.nca_id) if rec else None
        if not name:
            return
        try:
            romfs = container.nca(name).romfs()
            files = {f.path: f for f in romfs.files()}
            if "control.nacp" in files:
                st = romfs.open(files["control.nacp"])
                self.nacp_raw = st.read(0, st.size)
                self.nacp = meta.parse_nacp(self.nacp_raw)
            for i, lang in enumerate(Nacp.ICON_NAMES):
                f = files.get(f"icon_{lang}.dat")
                if f is not None and f.size:
                    st = romfs.open(f)
                    self.icons[i] = st.read(0, st.size)
            self.icon = self.preferred_icon()
        except (FormatError, NcaError, KeysError, KeyError):
            pass  # name and icon are optional


def verify(path: str, keys: KeySet,
           progress: Optional[Callable[[int, int], None]] = None,
           should_cancel: Optional[Callable[[], bool]] = None) -> list[tuple[str, str]]:
    """SHA-256 of every NCA against its CNMT record: [(file, ok|mismatch|missing)].

    Meaningful for NSPs: NCAs converted for an XCI have a different header, so they differ.
    For a NUP or NX: every file against its SHA-256 (no keys needed)."""
    if nx.is_nx(path):
        with nx.NxArchive(path) as a:
            return a.verify(progress, should_cancel)
    if nup.is_nup(path):
        with nup.NupFile(path) as n:
            return n.verify(progress, should_cancel)
    results = []
    with Container(path, keys) as c:
        checks = []
        for t in c.titles():
            for rec in t.cnmt.contents:
                if rec.type == meta.CONTENT_DELTA:
                    continue
                name = c.nca_file(rec.nca_id)
                if name is None:
                    results.append((rec.nca_id + ".nca", "missing"))
                else:
                    checks.append((name, rec.sha256))
        total = sum(c.files[n].size for n, _ in checks)
        done = 0
        for name, digest in checks:
            h = hashlib.sha256()
            for chunk in c.files[name].chunks():
                if should_cancel and should_cancel():
                    raise Cancelled()
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
            results.append((name, "ok" if h.digest() == digest else "mismatch"))
    return results


def kind_label(kind: str) -> str:
    return {"game": tr("Gioco base"), "update": tr("Aggiornamento"), "dlc": tr("DLC"),
            "delta": tr("Aggiornamento delta")}.get(kind, kind)


def title_label(title: "Title") -> str:
    if title.kind == "update" and title.standalone and title.complete:
        return tr("Gioco aggiornato")
    return kind_label(title.kind)


def version_label(title: Title) -> str:
    shown = f"v{title.version}"
    if title.display_version and title.kind != "dlc":
        shown = f"{title.display_version} ({shown})"
    return shown


@dataclass
class Game:
    """Everything loaded for one game: base, updates and DLC (possibly from many files)."""
    title_id: int
    base: Optional[Title] = None
    updates: list[Title] = field(default_factory=list)
    dlcs: list[Title] = field(default_factory=list)

    @property
    def latest_update(self) -> Optional[Title]:
        return max(self.updates, key=lambda t: t.version) if self.updates else None

    @property
    def name(self) -> str:
        for t in (self.latest_update, self.base):
            if t is not None and t.name:
                return t.name
        return meta.tid_str(self.title_id)

    @property
    def icon(self) -> Optional[bytes]:
        for t in (self.base, self.latest_update):
            if t is not None and t.icon:
                return t.icon
        return None

    @property
    def display_version(self) -> str:
        t = self.latest_update or self.base
        return version_label(t) if t else ""

    def merged(self) -> list[Title]:
        """What goes into a merged XCI: base, latest update, every DLC."""
        out = [t for t in (self.base, self.latest_update) if t is not None]
        return out + sorted(self.dlcs, key=lambda t: t.title_id)

    def all_titles(self) -> list[Title]:
        return ([self.base] if self.base else []) + self.updates + self.dlcs

    def warnings(self) -> list[str]:
        out = []
        if self.base is None and not self.updates:
            out.append(tr("Contiene solo DLC: per giocarli serve anche il gioco base."))
        elif self.base is None and not self.latest_update.complete:
            out.append(tr("Manca il gioco base: l'XCI conterrà solo aggiornamenti/DLC e "
                          "l'estrazione della RomFS dell'aggiornamento non è possibile."))
        if len(self.updates) > 1:
            out.append(tr("Più aggiornamenti: viene usato solo il più recente ({version}), gli "
                          "aggiornamenti sono cumulativi.",
                          version=version_label(self.latest_update)))
        if any(t.kind == "delta" for t in self.updates):
            out.append(tr("Gli aggiornamenti delta non sono supportati."))
        return out


def group_titles(titles: list[Title]) -> dict[int, Game]:
    games: dict[int, Game] = {}
    seen = set()
    for t in titles:
        if t.key in seen:
            continue
        seen.add(t.key)
        g = games.setdefault(t.base_id, Game(t.base_id))
        if t.kind == "game":
            if g.base is None or t.version > g.base.version:
                g.base = t
        elif t.kind in ("update", "delta"):
            g.updates.append(t)
        else:
            g.dlcs.append(t)
    for g in games.values():
        g.updates.sort(key=lambda t: t.version)
    _attach_orphan_dlc(games)
    return games


def _attach_orphan_dlc(games: dict[int, Game]) -> None:
    """DLC whose game id cannot be derived from its own id (Switch 2 upgrade packs,
    add-ons an archive keeps in a separate application) would show as a game without
    base. When they come from the same file as exactly one game, they belong to it."""
    for gid, g in list(games.items()):
        if g.base or g.updates or not g.dlcs:
            continue
        paths = {t.path for t in g.dlcs}
        owners = [o for o in games.values()
                  if o is not g and (o.base or o.updates)
                  and paths & {t.path for t in o.all_titles()}]
        if len(owners) == 1:
            owners[0].dlcs += g.dlcs
            owners[0].dlcs.sort(key=lambda t: t.title_id)
            del games[gid]


NUP_KINDS = {nup.KIND_BASE: "game", nup.KIND_UPDATE: "update", nup.KIND_ADDON: "dlc"}


def _nup_nacp(m: dict[int, bytes]) -> Optional[Nacp]:
    """Name, publisher and version from a NUP meta block: the NACP when present, the
    title/publisher/localized TLVs otherwise."""
    text = lambda b: b.decode("utf-8", "replace").strip()  # noqa: E731
    nacp = meta.parse_nacp(m[nup.TLV_NACP]) if nup.TLV_NACP in m else Nacp({}, "")
    publisher = text(m.get(nup.TLV_PUBLISHER, b"")) or nacp.publisher()
    titles = dict(nacp.titles)
    for tag, name in nup.decode_localized(m.get(nup.TLV_LOCALIZED_TITLES, b"")).items():
        if tag in nup.NACP_LANGS:
            titles.setdefault(nup.NACP_LANGS.index(tag), (text(name), publisher))
    if nup.TLV_TITLE_NAME in m:
        titles.setdefault(0, (text(m[nup.TLV_TITLE_NAME]), publisher))
    version = text(m.get(nup.TLV_DISPLAY_VERSION, b"")) or nacp.display_version
    if not titles and not version:
        return None
    return Nacp(titles, version)


def nup_titles(path: str) -> list[Title]:
    with nup.NupFile(path) as n:
        base = next((p for p in n.packages if p.kind == nup.KIND_BASE), None)
        out = []
        for p in n.packages:
            kind = NUP_KINDS.get(p.kind)
            if kind is None:
                continue
            t = Title(path, "nup", Cnmt(p.title_id, p.version, kind), "", 0, nup_index=p.index,
                      standalone=kind == "update" and (base is None or bool(
                          p.flags & nup.FLAG_STANDALONE_UPDATE)))
            t.nacp_raw = p.meta.get(nup.TLV_NACP)
            t.nacp = _nup_nacp(p.meta)
            icons = nup.decode_localized(p.meta.get(nup.TLV_LOCALIZED_ICONS, b""))
            t.icons = {nup.NACP_LANGS.index(tag): data for tag, data in icons.items()
                       if tag in nup.NACP_LANGS and data}
            t.icon = t.preferred_icon() or p.meta.get(nup.TLV_ICON)
            if kind != "game":
                # The add-on id of Switch 2 titles does not give back the game's id.
                t.group_id = base.title_id if base is not None else (
                    p.title_id if kind == "update" else None)
            out.append(t)
    if not out:
        raise FormatError(tr("{name}: il NUP non contiene giochi, aggiornamenti o DLC.",
                             name=os.path.basename(path)))
    return out


NX_KINDS = {nx.SNAP_BASE: "game", nx.SNAP_UPDATE: "update", nx.SNAP_STANDALONE: "update",
            nx.SNAP_DLC: "dlc"}


def nx_titles(path: str) -> list[Title]:
    with nx.NxArchive(path) as a:
        out = []
        for app in a.applications:
            for s in app.snapshots:
                kind = NX_KINDS.get(s.kind)
                if kind is None:
                    continue
                # executable snapshots: their program (several in multi-program games)
                tid = s.addon_title_id or s.program_id or app.application_id
                if kind == "dlc" and not s.addon_title_id:
                    # native DLC: its add-on id is not in the Catalog; keep it distinct
                    tid = app.application_id + 0x1000 + s.index
                t = Title(path, "nx", Cnmt(tid, s.version, kind), "", 0, nx_snapshot=s.index,
                          group_id=app.application_id, platform=app.platform,
                          standalone=s.kind == nx.SNAP_STANDALONE)
                m = app.meta_of(s.index)
                if m is not None:
                    titles = {}
                    for tag, name in m.localized.items():
                        if tag in nx.NACP_LANGS:
                            titles[nx.NACP_LANGS.index(tag)] = (name, m.publisher)
                    if m.title:  # the default title, unless American English is listed
                        titles.setdefault(0, (m.title, m.publisher))
                    if m.nacp_media is not None:
                        t.nacp_raw = a.media_bytes(m.nacp_media)
                    t.nacp = Nacp(titles, s.display_version)
                    t.icons = {nx.NACP_LANGS.index(tag): a.media_bytes(i)
                               for tag, i in m.localized_icons.items() if tag in nx.NACP_LANGS}
                    if m.icon_media is not None:
                        t.icons.setdefault(0, a.media_bytes(m.icon_media))
                    t.icon = t.preferred_icon()
                elif s.display_version:
                    t.nacp = Nacp({}, s.display_version)
                out.append(t)
    if not out:
        raise FormatError(tr("{name}: l'archivio NX non contiene giochi, aggiornamenti o DLC.",
                             name=os.path.basename(path)))
    return out


def read_titles(path: str, keys: Optional[KeySet]) -> list[Title]:
    if nx.is_nx(path):
        return nx_titles(path)
    if nup.is_nup(path):
        return nup_titles(path)
    if keys is None:
        raise KeysError(tr("File prod.keys non trovato. Ricavalo dalla tua console con "
                           "Lockpick_RCM e indicalo nelle impostazioni (o con --keys)."))
    with Container(path, keys) as c:
        return c.titles()


def collect_files(paths) -> list[str]:
    """Expand folders into the NSP/XCI files they contain."""
    found = []
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                found += [os.path.join(root, f) for f in sorted(files)
                          if f.lower().endswith(EXTENSIONS)]
        elif path.lower().endswith(EXTENSIONS):
            found.append(path)
    return found
