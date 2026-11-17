"""ตรวจรูปแบบที่อยู่กระเป๋าคริปโต (B5) ด้วยโค้ดในเครื่อง — ไม่เรียก blockchain explorer ภายนอก.

BTC: base58check (1…, 3…) และ bech32/bech32m (bc1…) · EVM (ETH/BSC/Polygon): 0x + 40 hex · TRON: base58check ขึ้นต้น T (0x41).
"""

from __future__ import annotations

import hashlib
import re

NETWORKS = {"btc": "Bitcoin (BTC)", "evm": "Ethereum/EVM (ETH, BSC, Polygon)", "tron": "TRON (TRX, USDT-TRC20)"}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def b58decode(s: str) -> bytes | None:
    n = 0
    for ch in s:
        i = B58.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def b58check(s: str) -> bytes | None:
    raw = b58decode(s)
    if not raw or len(raw) < 5:
        return None
    body, check = raw[:-4], raw[-4:]
    return body if hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] == check else None


def _polymod(values) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def bech32_ok(addr: str) -> bool:
    if addr.lower() != addr and addr.upper() != addr:
        return False
    addr = addr.lower()
    hrp, sep, data = addr.rpartition("1")
    if not sep or hrp not in ("bc", "tb") or len(data) < 6 or any(c not in BECH32 for c in data):
        return False
    values = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + [BECH32.index(c) for c in data]
    return _polymod(values) in (1, 0x2BC830A3)  # bech32 (segwit v0) or bech32m (taproot)


def detect(address: str) -> str:
    a = (address or "").strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", a):
        return "evm"
    if a.startswith("T") and len(a) == 34:
        return "tron"
    if a.lower().startswith(("bc1", "tb1")) or (a[:1] in "13mn2" and 26 <= len(a) <= 35):
        return "btc"
    return ""


def validate(network: str, address: str) -> tuple[bool, str]:
    """(ok, message in Thai)."""
    a = (address or "").strip()
    if network == "evm":
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", a):
            return False, "ที่อยู่ EVM ต้องขึ้นต้นด้วย 0x ตามด้วยเลขฐานสิบหก 40 ตัว"
        mixed = a[2:] != a[2:].lower() and a[2:] != a[2:].upper()
        return True, "รูปแบบถูกต้อง" + (" (ตัวพิมพ์เล็ก-ใหญ่ผสม: ตรวจ checksum EIP-55 ในเครื่องนี้ไม่ได้ ให้คัดลอกจากต้นทาง)" if mixed else "")
    if network == "tron":
        body = b58check(a)
        if not (a.startswith("T") and body and len(body) == 21 and body[0] == 0x41):
            return False, "ที่อยู่ TRON ต้องขึ้นต้นด้วย T ยาว 34 ตัว และ checksum ถูกต้อง"
        return True, "รูปแบบและ checksum ถูกต้อง"
    if network == "btc":
        if a.lower().startswith(("bc1", "tb1")):
            return (True, "รูปแบบและ checksum (bech32) ถูกต้อง") if bech32_ok(a) else (False, "ที่อยู่ bech32 (bc1…) checksum ไม่ถูกต้อง")
        body = b58check(a)
        if body and len(body) == 21 and body[0] in (0x00, 0x05, 0x6F, 0xC4):
            return True, "รูปแบบและ checksum ถูกต้อง"
        return False, "ที่อยู่ BTC ไม่ถูกต้อง (ขึ้นต้นด้วย 1, 3 หรือ bc1 และ checksum ต้องถูก)"
    return False, "ไม่รู้จักเครือข่าย"


def tx_hash_ok(network: str, tx: str) -> bool:
    t = (tx or "").strip()
    if network == "evm":
        return bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", t))
    return bool(re.fullmatch(r"(?:0x)?[0-9a-fA-F]{64}", t))
