"""AES primitives as the Switch uses them (ECB key wrapping, XTS headers, CTR sections)."""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

NCA_SECTOR = 0x200


def ecb_decrypt(key: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(data) + dec.finalize()


def ecb_encrypt(key: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


def _xts(key: bytes, data: bytes, first_sector: int, encrypt: bool,
         sector_size: int = NCA_SECTOR) -> bytes:
    """AES-128-XTS with Nintendo's tweak: the sector number in big endian."""
    aes = algorithms.AES(key)
    out = bytearray(len(data))
    for i in range(0, len(data), sector_size):
        tweak = (first_sector + i // sector_size).to_bytes(16, "big")
        c = Cipher(aes, modes.XTS(tweak))
        op = c.encryptor() if encrypt else c.decryptor()
        out[i:i + sector_size] = op.update(data[i:i + sector_size]) + op.finalize()
    return bytes(out)


def xts_decrypt(key: bytes, data: bytes, first_sector: int = 0) -> bytes:
    return _xts(key, data, first_sector, False)


def xts_encrypt(key: bytes, data: bytes, first_sector: int = 0) -> bytes:
    return _xts(key, data, first_sector, True)


def ctr_counter(upper: bytes, offset: int) -> bytes:
    """16-byte counter: 8 fixed bytes, then the 16-byte block index in big endian."""
    return upper + (offset >> 4).to_bytes(8, "big")


def ctr_crypt(key: bytes, upper: bytes, offset: int, data: bytes) -> bytes:
    """AES-CTR at an absolute offset (must be 16-byte aligned); same for both directions."""
    c = Cipher(algorithms.AES(key), modes.CTR(ctr_counter(upper, offset))).decryptor()
    return c.update(data) + c.finalize()
