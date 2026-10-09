#!/usr/bin/env python3
"""Safely extract version 1 Prism artifact packages."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from artifact_paths import (
    artifact_destination_key,
    find_artifact_path_conflicts,
    validate_artifact_relative_path,
)

DEFAULT_MAX_INPUT_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_PAYLOAD_BYTES = 96 * 1024 * 1024
_BASE64_PATTERN = re.compile(
    rb"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
)


class UnpackError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedArtifact:
    relative_path: str
    path_parts: Tuple[str, ...]
    payload: bytes


def non_negative_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _artifact_error(relative_path: str, message: str) -> UnpackError:
    return UnpackError("{}: {}".format(relative_path, message))


def read_package_file(package_path: Path, max_input_bytes: int) -> bytes:
    try:
        resolved_path = package_path.resolve(strict=True)
        initial_stat = resolved_path.stat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnpackError("package input must exist and resolve cleanly") from exc
    if not stat.S_ISREG(initial_stat.st_mode):
        raise UnpackError("package input must be a regular file")
    if initial_stat.st_size > max_input_bytes:
        raise UnpackError(
            "package input exceeds --max-input-bytes: {} bytes > {} bytes".format(
                initial_stat.st_size, max_input_bytes
            )
        )

    try:
        with resolved_path.open("rb") as package_file:
            before = os.fstat(package_file.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise UnpackError("package input stopped being a regular file")
            if before.st_size > max_input_bytes:
                raise UnpackError(
                    "package input exceeds --max-input-bytes: {} bytes > {} bytes".format(
                        before.st_size, max_input_bytes
                    )
                )
            if before.st_size != initial_stat.st_size:
                raise UnpackError("package input changed after size check")
            package_bytes = package_file.read(before.st_size + 1)
            after = os.fstat(package_file.fileno())
    except OSError as exc:
        raise UnpackError("could not read package input: {}".format(exc)) from exc

    if (
        len(package_bytes) != before.st_size
        or after.st_size != before.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
        or before.st_dev != after.st_dev
    ):
        raise UnpackError("package input changed while being read")
    return package_bytes


def decode_package_container(package_bytes: bytes) -> bytes:
    if package_bytes.lstrip(b" \t\r\n").startswith(b"{"):
        return package_bytes

    try:
        encoded = package_bytes.decode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeDecodeError, binascii.Error, ValueError) as exc:
        raise UnpackError("input is neither a JSON envelope nor valid Base64-wrapped JSON") from exc
    if base64.b64encode(decoded) != package_bytes:
        raise UnpackError("Base64-wrapped JSON is not canonical Base64")
    return decoded


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UnpackError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _reject_json_constant(value: str):
    raise UnpackError("invalid JSON constant: " + value)


def parse_json_envelope(json_bytes: bytes) -> Dict[str, object]:
    try:
        text = json_bytes.decode("utf-8")
        envelope = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, UnpackError) as exc:
        raise UnpackError("invalid UTF-8 JSON envelope: {}".format(exc)) from exc
    if not isinstance(envelope, dict):
        raise UnpackError("JSON envelope must be an object")
    return envelope


def validate_relative_path(relative_path: object, location: str) -> Tuple[str, ...]:
    try:
        return validate_artifact_relative_path(relative_path)
    except ValueError as exc:
        raise _artifact_error(
            location,
            "relative_path {!r} is invalid: {}".format(relative_path, exc),
        ) from exc


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnpackError("{} must be a non-empty string".format(name))
    return value


def validate_envelope(
    envelope: Dict[str, object], max_payload_bytes: int
) -> List[ValidatedArtifact]:
    required_envelope_keys = {
        "schema_version",
        "format",
        "package_id",
        "project_id",
        "batch_id",
        "artifacts",
    }
    schema_errors = []
    artifact_errors = []
    if set(envelope) != required_envelope_keys:
        missing = sorted(required_envelope_keys - set(envelope))
        extra = sorted(set(envelope) - required_envelope_keys)
        schema_errors.append(
            "schema: envelope fields mismatch (missing={!r}, extra={!r})".format(
                missing, extra
            )
        )

    schema_version = envelope.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        schema_errors.append("schema: schema_version must be 1")
    if envelope.get("format") != "prism-artifact-package":
        schema_errors.append("schema: format must be prism-artifact-package")
    for field in ("package_id", "project_id"):
        try:
            _require_string(envelope.get(field), field)
        except UnpackError as exc:
            schema_errors.append("schema: {}".format(exc))
    batch_id = envelope.get("batch_id")
    if batch_id is not None:
        try:
            _require_string(batch_id, "batch_id")
        except UnpackError as exc:
            schema_errors.append("schema: {}".format(exc))

    artifacts = envelope.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        schema_errors.append("schema: artifacts must be a non-empty array")
        artifacts = []

    required_artifact_keys = {
        "relative_path",
        "byte_size",
        "raw_sha256",
        "content_type",
        "payload_base64",
    }
    candidates: List[Dict[str, object]] = []
    path_records = []

    for index, artifact in enumerate(artifacts):
        index_label = "artifacts[{}]".format(index)
        candidate: Dict[str, object] = {
            "index_label": index_label,
            "relative_path": None,
            "path_parts": None,
            "byte_size": None,
            "raw_sha256": None,
            "payload_base64": None,
            "encoded_payload": None,
            "payload": None,
            "errors": [],
        }
        candidates.append(candidate)
        if not isinstance(artifact, dict):
            candidate["errors"].append(
                str(_artifact_error(index_label, "artifact must be an object"))
            )
            continue

        relative_value = artifact.get("relative_path")
        try:
            path_parts = validate_relative_path(relative_value, index_label)
        except UnpackError as exc:
            candidate["errors"].append(str(exc))
            location = index_label
        else:
            candidate["relative_path"] = relative_value
            candidate["path_parts"] = path_parts
            location = relative_value
            path_records.append((index, relative_value, path_parts))

        if set(artifact) != required_artifact_keys:
            missing = sorted(required_artifact_keys - set(artifact))
            extra = sorted(set(artifact) - required_artifact_keys)
            candidate["errors"].append(
                str(
                    _artifact_error(
                        location,
                        "artifact fields mismatch (missing={!r}, extra={!r})".format(
                            missing, extra
                        ),
                    )
                )
            )

        if "byte_size" in artifact:
            byte_size = artifact["byte_size"]
            if (
                isinstance(byte_size, bool)
                or not isinstance(byte_size, int)
                or byte_size < 0
            ):
                candidate["errors"].append(
                    str(_artifact_error(location, "byte_size must be a non-negative integer"))
                )
            else:
                candidate["byte_size"] = byte_size
                if byte_size > max_payload_bytes:
                    candidate["errors"].append(
                        str(
                            _artifact_error(
                                location,
                                "decoded payload exceeds --max-payload-bytes ({})".format(
                                    max_payload_bytes
                                ),
                            )
                        )
                    )

        if "raw_sha256" in artifact:
            raw_sha256 = artifact["raw_sha256"]
            if not isinstance(raw_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", raw_sha256
            ):
                candidate["errors"].append(
                    str(
                        _artifact_error(
                            location,
                            "raw_sha256 must be 64 lowercase hexadecimal characters",
                        )
                    )
                )
            else:
                candidate["raw_sha256"] = raw_sha256

        if "content_type" in artifact:
            content_type = artifact["content_type"]
            if not isinstance(content_type, str) or not content_type.strip():
                candidate["errors"].append(
                    str(_artifact_error(location, "content_type must be a non-empty string"))
                )

        if "payload_base64" in artifact:
            payload_base64 = artifact["payload_base64"]
            if not isinstance(payload_base64, str):
                candidate["errors"].append(
                    str(_artifact_error(location, "payload_base64 must be a string"))
                )
            else:
                candidate["payload_base64"] = payload_base64
                try:
                    encoded_payload = payload_base64.encode("ascii")
                except UnicodeEncodeError:
                    candidate["errors"].append(
                        str(_artifact_error(location, "payload_base64 is invalid"))
                    )
                else:
                    if not _BASE64_PATTERN.fullmatch(encoded_payload):
                        candidate["errors"].append(
                            str(_artifact_error(location, "payload_base64 is invalid"))
                        )
                    else:
                        candidate["encoded_payload"] = encoded_payload

        byte_size = candidate["byte_size"]
        encoded_payload = candidate["encoded_payload"]
        if isinstance(byte_size, int) and not isinstance(byte_size, bool):
            if isinstance(encoded_payload, bytes):
                expected_base64_size = 4 * ((byte_size + 2) // 3)
                if len(encoded_payload) != expected_base64_size:
                    candidate["errors"].append(
                        str(
                            _artifact_error(
                                location,
                                "payload_base64 length does not match byte_size",
                            )
                        )
                    )

    path_conflicts = find_artifact_path_conflicts(
        [(record[1], record[2]) for record in path_records]
    )
    for path_record_index, message in path_conflicts:
        source_artifact_index = path_records[path_record_index][0]
        candidate = candidates[source_artifact_index]
        candidate["errors"].append(
            str(_artifact_error(candidate["relative_path"], message))
        )

    total_payload_bytes = sum(
        candidate["byte_size"]
        for candidate in candidates
        if isinstance(candidate["byte_size"], int)
        and not isinstance(candidate["byte_size"], bool)
    )
    if total_payload_bytes > max_payload_bytes and not any(
        isinstance(candidate["byte_size"], int)
        and candidate["byte_size"] > max_payload_bytes
        for candidate in candidates
    ):
        running_total = 0
        for candidate in candidates:
            byte_size = candidate["byte_size"]
            if not isinstance(byte_size, int) or isinstance(byte_size, bool):
                continue
            running_total += byte_size
            if running_total > max_payload_bytes:
                location = candidate["relative_path"] or candidate["index_label"]
                candidate["errors"].append(
                    str(
                        _artifact_error(
                            location,
                            "combined decoded payload exceeds --max-payload-bytes ({})".format(
                                max_payload_bytes
                            ),
                        )
                    )
                )
                break

    for candidate in candidates:
        byte_size = candidate["byte_size"]
        encoded_payload = candidate["encoded_payload"]
        if (
            not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size > max_payload_bytes
            or not isinstance(encoded_payload, bytes)
            or len(encoded_payload) != 4 * ((byte_size + 2) // 3)
        ):
            continue

        location = candidate["relative_path"] or candidate["index_label"]
        try:
            payload = base64.b64decode(encoded_payload, validate=True)
        except (binascii.Error, ValueError):
            candidate["errors"].append(
                str(_artifact_error(location, "payload_base64 is invalid"))
            )
            continue
        if base64.b64encode(payload) != encoded_payload:
            candidate["errors"].append(
                str(_artifact_error(location, "payload_base64 is not canonical Base64"))
            )
            continue
        if len(payload) != byte_size:
            candidate["errors"].append(
                str(_artifact_error(location, "decoded payload size does not match byte_size"))
            )
            continue

        raw_sha256 = candidate["raw_sha256"]
        if isinstance(raw_sha256, str) and hashlib_sha256(payload) != raw_sha256:
            candidate["errors"].append(
                str(_artifact_error(location, "raw_sha256 does not match decoded payload"))
            )
        if total_payload_bytes <= max_payload_bytes:
            candidate["payload"] = payload

    for candidate in candidates:
        artifact_errors.extend(candidate["errors"])

    if schema_errors or artifact_errors:
        all_errors = schema_errors + artifact_errors
        raise UnpackError(
            "package validation failed:\n- " + "\n- ".join(all_errors)
        )

    validated = []
    for candidate in candidates:
        validated.append(
            ValidatedArtifact(
                relative_path=candidate["relative_path"],
                path_parts=candidate["path_parts"],
                payload=candidate["payload"],
            )
        )
    return validated


def hashlib_sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def validate_output_destinations(
    output_root: Path, artifacts: List[ValidatedArtifact]
) -> Path:
    try:
        resolved_root = output_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnpackError("output root must exist and resolve cleanly") from exc
    if not resolved_root.is_dir():
        raise UnpackError("output root must be a directory")

    for artifact in artifacts:
        current = resolved_root
        last_index = len(artifact.path_parts) - 1
        for index, component in enumerate(artifact.path_parts):
            if current.is_symlink():
                raise _artifact_error(artifact.relative_path, "symlink output paths are forbidden")
            if current.exists() and current.is_dir():
                component_key = artifact_destination_key((component,))[0]
                try:
                    children = current.iterdir()
                    for child in children:
                        child_key = artifact_destination_key((child.name,))[0]
                        if child_key == component_key and child.name != component:
                            raise _artifact_error(
                                artifact.relative_path,
                                "destination collides with existing path {}".format(child.name),
                            )
                except OSError as exc:
                    raise _artifact_error(
                        artifact.relative_path, "could not inspect destination directory: {}".format(exc)
                    ) from exc

            candidate = current / component
            if candidate.is_symlink():
                raise _artifact_error(artifact.relative_path, "symlink output paths are forbidden")
            if candidate.exists():
                if index == last_index:
                    raise _artifact_error(artifact.relative_path, "destination already exists")
                if not candidate.is_dir():
                    raise _artifact_error(
                        artifact.relative_path, "destination parent is not a directory"
                    )
            current = candidate

    return resolved_root


def _ensure_parent_directories(
    output_root: Path,
    artifact: ValidatedArtifact,
    created_directories: List[Path],
) -> Path:
    current = output_root
    for component in artifact.path_parts[:-1]:
        candidate = current / component
        if candidate.is_symlink():
            raise _artifact_error(artifact.relative_path, "symlink output paths are forbidden")
        if candidate.exists():
            if not candidate.is_dir():
                raise _artifact_error(
                    artifact.relative_path, "destination parent is not a directory"
                )
        else:
            try:
                candidate.mkdir()
                created_directories.append(candidate)
            except FileExistsError:
                if candidate.is_symlink() or not candidate.is_dir():
                    raise _artifact_error(
                        artifact.relative_path, "destination parent changed during extraction"
                    )
            except OSError as exc:
                raise _artifact_error(
                    artifact.relative_path,
                    "could not create destination directory: {}".format(exc),
                ) from exc

        try:
            candidate.resolve(strict=True).relative_to(output_root)
        except (OSError, ValueError) as exc:
            raise _artifact_error(
                artifact.relative_path, "destination directory escapes output root"
            ) from exc
        current = candidate

    return current / artifact.path_parts[-1]


def write_payload(
    destination: Path,
    payload: bytes,
    relative_path: str,
    created_files: List[Path],
) -> None:
    try:
        with destination.open("xb") as output_file:
            created_files.append(destination)
            written = output_file.write(payload)
            if written != len(payload):
                raise OSError("short write: {} of {} bytes".format(written, len(payload)))
    except FileExistsError as exc:
        raise _artifact_error(relative_path, "destination appeared during extraction") from exc
    except OSError as exc:
        raise _artifact_error(relative_path, "write failed: {}".format(exc)) from exc


def rollback_outputs(created_files: List[Path], created_directories: List[Path]) -> List[str]:
    failures = []
    for output_file in reversed(created_files):
        try:
            output_file.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            failures.append("{} ({})".format(output_file, exc))
    for directory in reversed(created_directories):
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            failures.append("{} ({})".format(directory, exc))
    return failures


def unpack_package(
    package_path: Path,
    output_root: Path,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> int:
    package_bytes = read_package_file(package_path, max_input_bytes)
    json_bytes = decode_package_container(package_bytes)
    envelope = parse_json_envelope(json_bytes)
    artifacts = validate_envelope(envelope, max_payload_bytes)
    resolved_root = validate_output_destinations(output_root, artifacts)

    created_files: List[Path] = []
    created_directories: List[Path] = []
    try:
        for artifact in artifacts:
            destination = _ensure_parent_directories(
                resolved_root, artifact, created_directories
            )
            write_payload(destination, artifact.payload, artifact.relative_path, created_files)
    except Exception as exc:
        rollback_failures = rollback_outputs(created_files, created_directories)
        if rollback_failures:
            raise UnpackError(
                "extraction failed: {}; rollback incomplete: {}".format(
                    exc, "; ".join(rollback_failures)
                )
            ) from exc
        raise UnpackError("extraction failed: {}; rollback completed".format(exc)) from exc

    return len(artifacts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract a schema-version-1 Prism artifact package without overwriting files."
    )
    parser.add_argument("package", type=Path, help="JSON envelope or Base64-wrapped JSON")
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--max-input-bytes",
        type=non_negative_integer,
        default=DEFAULT_MAX_INPUT_BYTES,
        help="maximum package file size in bytes (default: %(default)s)",
    )
    parser.add_argument(
        "--max-payload-bytes",
        type=non_negative_integer,
        default=DEFAULT_MAX_PAYLOAD_BYTES,
        help="maximum combined decoded artifact size in bytes (default: %(default)s)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        extracted_count = unpack_package(
            args.package,
            args.output_root,
            max_input_bytes=args.max_input_bytes,
            max_payload_bytes=args.max_payload_bytes,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("Extracted {} artifact(s) to {}".format(extracted_count, args.output_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())