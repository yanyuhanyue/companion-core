"""Pinned MIT GET hkey compatibility subset; see THIRD_PARTY_NOTICES.txt.

Adapted from astrbot_plugin_xiaoheihe_adapter@90d3c9d4, request_signing.py.
The upstream port credits heybox-core@2fd1bbed. MD5 is an upstream wire-format
requirement, never an authentication, integrity, or password-storage primitive here.
"""

import hashlib

_HKEY_ALPHABET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"


def generate_hkey(path: str, timestamp: int, nonce: str) -> str:
    normalized_path = f"/{'/'.join(part for part in str(path).split('/') if part)}/"
    parts = (
        _map_to_alphabet(str(timestamp), _HKEY_ALPHABET[:-2]),
        _map_to_alphabet(normalized_path, _HKEY_ALPHABET),
        _map_to_alphabet(str(nonce), _HKEY_ALPHABET),
    )
    interleaved = "".join(
        part[index]
        for index in range(max(len(part) for part in parts))
        for part in parts
        if index < len(part)
    )[:20]
    digest = hashlib.md5(interleaved.encode(), usedforsecurity=False).hexdigest()
    mixed = _mix_tail([ord(char) for char in digest[-6:]])
    suffix = str(sum(mixed) % 100).zfill(2)
    prefix = _map_to_alphabet(digest[:5], _HKEY_ALPHABET[:-4])
    return f"{prefix}{suffix}"


def _map_to_alphabet(value: str, alphabet: str) -> str:
    return "".join(alphabet[ord(char) % len(alphabet)] for char in value)


def _xtime(value: int) -> int:
    return (255 & ((value << 1) ^ 27)) if value & 128 else value << 1


def _mul3(value: int) -> int:
    return _xtime(value) ^ value


def _mul4(value: int) -> int:
    return _mul3(_xtime(value))


def _mul8(value: int) -> int:
    return _mul4(_mul3(_xtime(value)))


def _mul14(value: int) -> int:
    return _mul8(value) ^ _mul4(value) ^ _mul3(value)


def _mix_tail(values: list[int]) -> list[int]:
    first, second, third, fourth = values[:4]
    return [
        _mul14(first) ^ _mul8(second) ^ _mul4(third) ^ _mul3(fourth),
        _mul3(first) ^ _mul14(second) ^ _mul8(third) ^ _mul4(fourth),
        _mul4(first) ^ _mul3(second) ^ _mul14(third) ^ _mul8(fourth),
        _mul8(first) ^ _mul4(second) ^ _mul3(third) ^ _mul14(fourth),
        *values[4:],
    ]
