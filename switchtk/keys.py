"""prod.keys / title.keys loading. The keys come from the user's own console (Lockpick_RCM).

The program contains no keys: it looks for prod.keys in the usual places, or uses the file
chosen in the settings / given with --keys.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from .crypto import ecb_decrypt
from .i18n import tr

KEY_AREA_NAMES = ("application", "ocean", "system")


class KeysError(Exception):
    """Missing or invalid prod.keys."""


def parse_keyfile(path: str) -> dict[str, bytes]:
    """'name = hex' lines; comments (# or ;) and malformed lines are ignored."""
    out: dict[str, bytes] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.split("#", 1)[0].split(";", 1)[0].strip()
            if "=" not in line and "," not in line:
                continue
            name, _, value = line.replace(",", "=", 1).partition("=")
            name, value = name.strip().lower(), value.strip().replace(" ", "")
            try:
                out[name] = bytes.fromhex(value)
            except ValueError:
                continue
    return out


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "SwitchToolkit")


def candidate_paths(name: str = "prod.keys") -> list[str]:
    """Where prod.keys/title.keys are looked for, in order."""
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", os.path.join(home, ".config"))
    return [
        os.path.join(_app_dir(), name),
        os.path.join(config_dir(), name),
        os.path.join(home, ".switch", name),
        os.path.join(appdata, "yuzu", "keys", name),
        os.path.join(appdata, "suyu", "keys", name),
        os.path.join(appdata, "sudachi", "keys", name),
        os.path.join(appdata, "citron", "keys", name),
        os.path.join(appdata, "eden", "keys", name),
        os.path.join(appdata, "Ryujinx", "system", name),
    ]


def find_keyfile(name: str = "prod.keys") -> Optional[str]:
    env = os.environ.get("SWITCHTK_KEYS") if name == "prod.keys" else None
    for path in ([env] if env else []) + candidate_paths(name):
        if path and os.path.isfile(path):
            return path
    return None


def _generate_kek(src: bytes, master: bytes, kek_seed: bytes, key_seed: Optional[bytes]) -> bytes:
    kek = ecb_decrypt(master, kek_seed)
    src_kek = ecb_decrypt(kek, src)
    return ecb_decrypt(src_kek, key_seed) if key_seed else src_kek


class KeySet:
    def __init__(self, keys: Optional[dict[str, bytes]] = None,
                 title_keys: Optional[dict[bytes, bytes]] = None, path: str = ""):
        self.keys = dict(keys or {})
        self.title_keys: dict[bytes, bytes] = dict(title_keys or {})  # rights id -> plain key
        self.path = path
        self._derive()

    @classmethod
    def load(cls, path: Optional[str] = None, title_keys_path: Optional[str] = None) -> "KeySet":
        path = path or find_keyfile("prod.keys")
        if not path:
            raise KeysError(tr("File prod.keys non trovato. Ricavalo dalla tua console con "
                               "Lockpick_RCM e indicalo nelle impostazioni (o con --keys)."))
        try:
            keys = parse_keyfile(path)
        except OSError as ex:
            raise KeysError(tr("Impossibile leggere {path}: {error}", path=path, error=ex)) from ex
        ks = cls(keys, path=path)
        tk_path = title_keys_path or find_keyfile("title.keys")
        if not title_keys_path and tk_path is None:
            sibling = os.path.join(os.path.dirname(path), "title.keys")
            tk_path = sibling if os.path.isfile(sibling) else None
        if tk_path:
            try:
                for rid, key in parse_keyfile(tk_path).items():
                    if len(key) == 16:
                        ks.title_keys[bytes.fromhex(rid)] = key
            except (OSError, ValueError):
                pass
        if "header_key" not in ks.keys:
            raise KeysError(tr("{path} non contiene header_key: il file non è un prod.keys "
                               "valido.", path=path))
        return ks

    def _derive(self) -> None:
        """Fill derived keys missing from a minimal prod.keys (only master keys + sources)."""
        k = self.keys
        kek_seed, key_seed = k.get("aes_kek_generation_source"), k.get("aes_key_generation_source")
        for gen in range(0x20):
            master = k.get(f"master_key_{gen:02x}")
            if not master:
                continue
            if f"titlekek_{gen:02x}" not in k and "titlekek_source" in k:
                k[f"titlekek_{gen:02x}"] = ecb_decrypt(master, k["titlekek_source"])
            if not (kek_seed and key_seed):
                continue
            for area in KEY_AREA_NAMES:
                name, src = f"key_area_key_{area}_{gen:02x}", k.get(f"key_area_key_{area}_source")
                if name not in k and src:
                    k[name] = _generate_kek(src, master, kek_seed, key_seed)
        if ("header_key" not in k and "header_key_source" in k and "header_kek_source" in k
                and "master_key_00" in k and kek_seed and key_seed):
            kek = _generate_kek(k["header_kek_source"], k["master_key_00"], kek_seed, key_seed)
            k["header_key"] = ecb_decrypt(kek, k["header_key_source"])

    def get(self, name: str) -> bytes:
        try:
            return self.keys[name]
        except KeyError:
            raise KeysError(tr("Manca la chiave {name} in prod.keys: probabilmente il gioco "
                               "richiede un firmware più recente di quello da cui sono state "
                               "ricavate le chiavi.", name=name)) from None

    @property
    def header_key(self) -> bytes:
        return self.get("header_key")

    def key_area_key(self, index: int, generation: int) -> bytes:
        area = KEY_AREA_NAMES[index] if index < len(KEY_AREA_NAMES) else str(index)
        return self.get(f"key_area_key_{area}_{generation:02x}")

    def titlekek(self, generation: int) -> bytes:
        return self.get(f"titlekek_{generation:02x}")
