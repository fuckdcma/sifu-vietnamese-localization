#!/usr/bin/env python3
"""Package source-root files into a versioned JSON envelope."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_PACKAGE_BYTES = 128 * 1024 * 1024
Artifact = Dict[str, Union[str, int]]


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    byte_size: int
    content_type: str


def non_empty_string(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("value must not be empty")
    return value


def non_negative_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def validate_input_path(requested_file: str) -> Path:
    """Require portable, traversal-free relative paths in package metadata."""
    if not requested_file or "\x00" in requested_file or ":" in requested_file:
        raise ValueError("input path is not portable: " + requested_file)
    if os.name != "nt" and "\\" in requested_file:
        raise ValueError("input path is not portable: " + requested_file)
    if any(part in ("", ".", "..") for part in re.split(r"[/\\]", requested_file)):
        raise ValueError("input path is not normalized: " + requested_file)
    input_path = Path(requested_file)
    if input_path.is_absolute():
        raise ValueError("input paths must be relative to the source root")
    return input_path


def inspect_source_files(
    source_root: Path, requested_files: List[str], max_file_bytes: int
) -> List[SourceFile]:
    try:
        resolved_root = source_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("source root must exist and resolve cleanly") from exc
    if not resolved_root.is_dir():
        raise ValueError("source root must be a directory")

    source_files: List[SourceFile] = []
    seen_paths = set()
    for requested_file in requested_files:
        input_path = validate_input_path(requested_file)

        try:
            resolved_path = (resolved_root / input_path).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(
                "input path must exist and resolve cleanly: " + requested_file
            ) from exc

        try:
            relative_path = resolved_path.relative_to(resolved_root).as_posix()
        except ValueError as exc:
            raise ValueError("input path escapes the source root: " + requested_file) from exc

        try:
            file_stat = resolved_path.stat()
        except OSError as exc:
            raise ValueError("could not stat input file: " + requested_file) from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("input path is not a regular file: " + requested_file)
        if file_stat.st_size > max_file_bytes:
            raise ValueError(
                "input exceeds --max-file-bytes: {} bytes > {} bytes ({})".format(
                    file_stat.st_size, max_file_bytes, relative_path
                )
            )
        if relative_path in seen_paths:
            raise ValueError("duplicate input path: " + relative_path)
        seen_paths.add(relative_path)

        content_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        source_files.append(
            SourceFile(
                path=resolved_path,
                relative_path=relative_path,
                byte_size=file_stat.st_size,
                content_type=content_type,
            )
        )

    return source_files


def read_source_bytes(source_file: SourceFile, max_file_bytes: int) -> bytes:
    with source_file.path.open("rb") as input_file:
        before = os.fstat(input_file.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input stopped being a regular file: " + source_file.relative_path)
        if before.st_size > max_file_bytes:
            raise ValueError(
                "input exceeds --max-file-bytes: {} bytes > {} bytes ({})".format(
                    before.st_size, max_file_bytes, source_file.relative_path
                )
            )
        if before.st_size != source_file.byte_size:
            raise ValueError("input changed after size check: " + source_file.relative_path)

        file_bytes = input_file.read(source_file.byte_size + 1)
        after = os.fstat(input_file.fileno())

    if (
        len(file_bytes) != source_file.byte_size
        or after.st_size != source_file.byte_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
        or before.st_dev != after.st_dev
    ):
        raise ValueError("input changed while being packaged: " + source_file.relative_path)
    return file_bytes


def artifacts_from_sources(
    source_files: List[SourceFile], max_file_bytes: int
) -> List[Artifact]:
    artifacts: List[Artifact] = []
    for source_file in source_files:
        file_bytes = read_source_bytes(source_file, max_file_bytes)
        artifacts.append(
            {
                "relative_path": source_file.relative_path,
                "byte_size": len(file_bytes),
                "raw_sha256": hashlib.sha256(file_bytes).hexdigest(),
                "content_type": source_file.content_type,
                "payload_base64": base64.b64encode(file_bytes).decode("ascii"),
            }
        )
    return artifacts


def collect_artifacts(
    source_root: Path,
    requested_files: List[str],
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> List[Artifact]:
    source_files = inspect_source_files(source_root, requested_files, max_file_bytes)
    return artifacts_from_sources(source_files, max_file_bytes)


def build_envelope(
    package_id: str,
    project_id: str,
    batch_id: Optional[str],
    artifacts: List[Artifact],
) -> Dict[str, object]:
    return {
        "schema_version": 1,
        "format": "prism-artifact-package",
        "package_id": package_id,
        "project_id": project_id,
        "batch_id": batch_id,
        "artifacts": artifacts,
    }


def serialize_envelope(envelope: Dict[str, object]) -> bytes:
    return (json.dumps(envelope, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def base64_size(byte_size: int) -> int:
    return 4 * ((byte_size + 2) // 3)


def estimate_package_size(
    package_id: str,
    project_id: str,
    batch_id: Optional[str],
    source_files: List[SourceFile],
    base64_output: bool,
) -> int:
    placeholders: List[Artifact] = [
        {
            "relative_path": source_file.relative_path,
            "byte_size": source_file.byte_size,
            "raw_sha256": "0" * 64,
            "content_type": source_file.content_type,
            "payload_base64": "",
        }
        for source_file in source_files
    ]
    json_template = serialize_envelope(
        build_envelope(package_id, project_id, batch_id, placeholders)
    )
    json_size = len(json_template) + sum(
        base64_size(source_file.byte_size) for source_file in source_files
    )
    return base64_size(json_size) if base64_output else json_size


def write_new_package(output_path: Path, package_bytes: bytes) -> None:
    created = False
    try:
        with output_path.open("xb") as output_file:
            created = True
            output_file.write(package_bytes)
    except FileExistsError as exc:
        raise ValueError("package already exists: " + str(output_path)) from exc
    except OSError:
        if created:
            try:
                output_path.unlink()
            except OSError:
                pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package files under a source root into a versioned JSON envelope."
    )
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--package-id", required=True, type=non_empty_string)
    parser.add_argument("--project-id", required=True, type=non_empty_string)
    parser.add_argument("--batch-id", type=non_empty_string, default=None)
    parser.add_argument(
        "--max-file-bytes",
        type=non_negative_integer,
        default=DEFAULT_MAX_FILE_BYTES,
        help="maximum source file size in bytes (default: %(default)s)",
    )
    parser.add_argument(
        "--max-package-bytes",
        type=non_negative_integer,
        default=DEFAULT_MAX_PACKAGE_BYTES,
        help="maximum output package size in bytes (default: %(default)s)",
    )
    parser.add_argument(
        "--base64-output",
        action="store_true",
        help="write the complete JSON envelope as one Base64 string",
    )
    parser.add_argument("files", nargs="+", metavar="FILE")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        source_files = inspect_source_files(
            args.source_root, args.files, args.max_file_bytes
        )
        estimated_size = estimate_package_size(
            args.package_id,
            args.project_id,
            args.batch_id,
            source_files,
            args.base64_output,
        )
        if estimated_size > args.max_package_bytes:
            raise ValueError(
                "package exceeds --max-package-bytes: estimated {} bytes > {} bytes".format(
                    estimated_size, args.max_package_bytes
                )
            )

        artifacts = artifacts_from_sources(source_files, args.max_file_bytes)
        envelope = build_envelope(
            args.package_id, args.project_id, args.batch_id, artifacts
        )
        json_bytes = serialize_envelope(envelope)
        package_bytes = base64.b64encode(json_bytes) if args.base64_output else json_bytes
        if len(package_bytes) != estimated_size:
            raise ValueError("package size estimate did not match serialized output")
        if len(package_bytes) > args.max_package_bytes:
            raise ValueError("package exceeds --max-package-bytes")
        write_new_package(args.output, package_bytes)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    output_format = "Base64 JSON" if args.base64_output else "JSON"
    print("Wrote {} artifact(s) as {} to {}".format(len(artifacts), output_format, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())