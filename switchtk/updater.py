"""In-app update: look for a newer GitHub release, download it, swap it in, restart.

Supported for the published Windows build (PyInstaller .exe). Running from source, or on
another system, the user is pointed to the release page.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from . import __version__
from .i18n import tr

REPO = "Nanobuo/Switch-Toolkit"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "SwitchToolkit-windows-x64.exe"
CHUNK = 1 << 20


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str        # "1.4.0"
    tag: str            # "v1.4.0"
    notes: str          # release description (Markdown)
    page_url: str
    asset_name: Optional[str] = None
    asset_url: Optional[str] = None
    asset_size: int = 0
    asset_sha256: Optional[str] = None


def asset_name(platform: str = sys.platform) -> Optional[str]:
    """Name of the release file for this platform (same name on every release)."""
    return ASSET_NAME if platform.startswith("win") else None


def version_key(version: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def is_newer(latest: str, current: str = __version__) -> bool:
    return version_key(latest) > version_key(current)


def release_from_json(data: dict, platform: str = sys.platform) -> Release:
    tag = str(data.get("tag_name") or "")
    release = Release(version=tag.lstrip("vV"), tag=tag, notes=str(data.get("body") or ""),
                      page_url=str(data.get("html_url") or RELEASES_PAGE))
    wanted = asset_name(platform)
    for asset in data.get("assets") or []:
        if wanted and asset.get("name") == wanted:
            release.asset_name = wanted
            release.asset_url = asset.get("browser_download_url")
            release.asset_size = int(asset.get("size") or 0)
            digest = str(asset.get("digest") or "")
            if digest.startswith("sha256:"):
                release.asset_sha256 = digest.split(":", 1)[1].lower()
    return release


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"SwitchToolkit/{__version__}",
    })
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_latest(url: str = API_URL, timeout: float = 10) -> Release:
    try:
        with _open(url, timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            raise UpdateError(tr("Nessuna release trovata (il repository potrebbe essere "
                                 "privato).")) from ex
        raise UpdateError(tr("GitHub ha risposto con l'errore {code}.", code=ex.code)) from ex
    except (urllib.error.URLError, OSError, ValueError) as ex:
        raise UpdateError(tr("Impossibile contattare GitHub: {error}", error=ex)) from ex
    return release_from_json(data)


# -- where the running program lives -------------------------------------------------

def install_target() -> Optional[str]:
    """The .exe to replace, or None when self-update is not possible."""
    if getattr(sys, "frozen", False) and sys.platform.startswith("win"):
        return sys.executable
    return None


def cleanup_previous(target: Optional[str] = None) -> None:
    """Remove the old .exe the last update left behind."""
    target = target or install_target()
    if target:
        _remove(target + ".old")


# -- download and swap --------------------------------------------------------------

def download(release: Release, dest: str,
             progress: Optional[Callable[[int, int], None]] = None,
             should_cancel: Optional[Callable[[], bool]] = None,
             timeout: float = 30) -> str:
    """Download the release file to dest and check its SHA-256 (when GitHub provides it)."""
    if not release.asset_url:
        raise UpdateError(tr("Questa release non contiene il file per il tuo sistema."))
    h = hashlib.sha256()
    done = 0
    try:
        with _open(release.asset_url, timeout) as resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or release.asset_size or 0)
            while True:
                if should_cancel and should_cancel():
                    raise UpdateError(tr("Aggiornamento annullato."))
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if release.asset_size and done != release.asset_size:
            raise UpdateError(tr("Download incompleto."))
        if release.asset_sha256 and h.hexdigest() != release.asset_sha256:
            raise UpdateError(tr("Il file scaricato è danneggiato (SHA-256 diverso)."))
    except UpdateError:
        _remove(dest)
        raise
    except (urllib.error.URLError, OSError) as ex:
        _remove(dest)
        raise UpdateError(tr("Download non riuscito: {error}", error=ex)) from ex
    return dest


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def staging_path(target: str) -> str:
    """Where to download: next to the target, so the final rename stays on one disk."""
    return os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.download")


def apply_update(downloaded: str, target: str) -> None:
    """Swap the downloaded .exe in place of target. A running .exe cannot be overwritten
    but can be renamed, so the running copy keeps working until the restart."""
    old = target + ".old"
    _remove(old)
    os.replace(target, old)
    try:
        os.replace(downloaded, target)
    except OSError:
        os.replace(old, target)
        raise


def relaunch(target: str) -> None:
    """Start the updated program; the caller then quits."""
    env = dict(os.environ)
    # A PyInstaller program started by another one must unpack itself afresh.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([target], env=env, creationflags=flags, close_fds=True)
