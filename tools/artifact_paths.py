"""Cross-platform relative-path policy for artifact packages."""

import unicodedata
from pathlib import PureWindowsPath
from typing import Dict, Iterable, List, Tuple


_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
_WINDOWS_INVALID_CHARACTERS = set('<>:"|?*')


def validate_artifact_relative_path(value: str) -> Tuple[str, ...]:
    if not isinstance(value, str) or not value:
        raise ValueError("path is not portable: expected non-empty text")
    if any(ord(character) < 32 for character in value):
        raise ValueError("path is not portable: control characters are forbidden")
    if "\\" in value or ":" in value:
        raise ValueError("path is not portable: use forward slashes and no drive or stream syntax")

    windows_path = PureWindowsPath(value)
    if value.startswith("/") or windows_path.drive or windows_path.root or windows_path.is_absolute():
        raise ValueError("path is not portable: absolute and drive-qualified paths are forbidden")

    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("path traversal or path is not normalized")

    for part in parts:
        if part[-1] in (".", " "):
            raise ValueError("path is not portable: Windows components cannot end in dot or space")
        if any(character in _WINDOWS_INVALID_CHARACTERS for character in part):
            raise ValueError("path is not portable: Windows-reserved characters are forbidden")
        device_stem = part.split(".", 1)[0].rstrip(" .").upper()
        if device_stem in _WINDOWS_RESERVED_NAMES:
            raise ValueError("path is not portable: Windows-reserved device name")
        if device_stem.startswith(("COM", "LPT")):
            device_number = device_stem[3:]
            if device_stem[:3] in ("COM", "LPT") and device_number in (
                "1", "2", "3", "4", "5", "6", "7", "8", "9", "¹", "²", "³"
            ):
                raise ValueError("path is not portable: Windows-reserved device name")

    return tuple(parts)


def artifact_destination_key(parts: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)


def find_artifact_path_conflicts(
    paths: Iterable[Tuple[str, Tuple[str, ...]]]
) -> List[Tuple[int, str]]:
    path_list = list(paths)
    seen: Dict[Tuple[str, ...], str] = {}
    conflicts = []

    for index, (relative_path, parts) in enumerate(path_list):
        key = artifact_destination_key(parts)
        if key in seen:
            conflicts.append(
                (
                    index,
                    "duplicate destination conflicts with {}".format(seen[key]),
                )
            )
        else:
            seen[key] = relative_path

    for index, (_, parts) in enumerate(path_list):
        key = artifact_destination_key(parts)
        for prefix_length in range(1, len(key)):
            parent_key = key[:prefix_length]
            if parent_key in seen:
                conflicts.append(
                    (
                        index,
                        "destination conflicts with artifact file {}".format(
                            seen[parent_key]
                        ),
                    )
                )
                break

    return conflicts
