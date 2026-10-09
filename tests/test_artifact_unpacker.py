import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WORKSPACE = Path(__file__).resolve().parents[1]
PACKAGER = WORKSPACE / "tools" / "artifact_packager.py"
UNPACKER = WORKSPACE / "tools" / "artifact_unpacker.py"
sys.path.insert(0, str(WORKSPACE / "tools"))
import artifact_unpacker as unpacker_module
sys.path.pop(0)


class ArtifactUnpackerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(dir=WORKSPACE)
        self.temporary_path = Path(self.temporary_directory.name)
        self.output_root = self.temporary_path / "output"
        self.output_root.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_artifact(self, relative_path, payload, **overrides):
        artifact = {
            "relative_path": relative_path,
            "byte_size": len(payload),
            "raw_sha256": hashlib.sha256(payload).hexdigest(),
            "content_type": "application/octet-stream",
            "payload_base64": base64.b64encode(payload).decode("ascii"),
        }
        artifact.update(overrides)
        return artifact

    def make_envelope(self, artifacts, **overrides):
        envelope = {
            "schema_version": 1,
            "format": "prism-artifact-package",
            "package_id": "synthetic-package-001",
            "project_id": "synthetic-project",
            "batch_id": None,
            "artifacts": artifacts,
        }
        envelope.update(overrides)
        return envelope

    def write_package(self, envelope, base64_wrapped=False, filename="package.json"):
        package_path = self.temporary_path / filename
        package_bytes = (json.dumps(envelope, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        if base64_wrapped:
            package_bytes = base64.b64encode(package_bytes)
        package_path.write_bytes(package_bytes)
        return package_path

    def run_unpacker(self, package_path, options=(), output_root=None):
        target_root = output_root or self.output_root
        command = [
            sys.executable,
            str(UNPACKER),
            str(package_path),
            "--output-root",
            str(target_root),
            *options,
        ]
        return subprocess.run(
            command,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
        )

    def assert_output_empty(self, output_root=None):
        target_root = output_root or self.output_root
        self.assertEqual(list(target_root.iterdir()), [])

    def test_json_extraction(self):
        payload = b"json envelope payload"
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("note.txt", payload)])
        )

        result = self.run_unpacker(package_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output_root / "note.txt").read_bytes(), payload)

    def test_base64_extraction(self):
        payload = b"base64 wrapped payload"
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("wrapped.bin", payload)]),
            base64_wrapped=True,
        )

        result = self.run_unpacker(package_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output_root / "wrapped.bin").read_bytes(), payload)

    def test_multiple_files(self):
        first = b"first file"
        second = b"second file"
        package_path = self.write_package(
            self.make_envelope(
                [
                    self.make_artifact("first.bin", first),
                    self.make_artifact("nested/second.bin", second),
                ]
            )
        )

        result = self.run_unpacker(package_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output_root / "first.bin").read_bytes(), first)
        self.assertEqual((self.output_root / "nested/second.bin").read_bytes(), second)

    def test_unicode_vietnamese_is_preserved(self):
        payload = "Tiếng Việt: ă â đ ê ô ơ ư; dấu ấ ề ộ ữ.".encode("utf-8")
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("unicode.txt", payload)])
        )

        result = self.run_unpacker(package_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output_root / "unicode.txt").read_bytes(), payload)

    def test_crlf_and_lf_are_preserved(self):
        payload = b"row one\r\nrow two\nrow three\r\n"
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("line-endings.csv", payload)])
        )

        result = self.run_unpacker(package_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output_root / "line-endings.csv").read_bytes(), payload)

    def test_binary_payload_is_preserved(self):
        payload = bytes(range(256)) + b"\x00\xff\x80\x10"
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("binary.dat", payload)])
        )

        result = self.run_unpacker(package_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output_root / "binary.dat").read_bytes(), payload)

    def test_invalid_sha256_is_rejected(self):
        artifact = self.make_artifact("bad-hash.bin", b"payload", raw_sha256="0" * 64)
        package_path = self.write_package(self.make_envelope([artifact]))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bad-hash.bin", result.stderr)
        self.assertIn("raw_sha256 does not match", result.stderr)
        self.assert_output_empty()

    def test_invalid_byte_size_is_rejected(self):
        payload = b"nine-byte"
        artifact = self.make_artifact("bad-size.bin", payload, byte_size=len(payload) - 1)
        package_path = self.write_package(self.make_envelope([artifact]))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bad-size.bin", result.stderr)
        self.assertIn("decoded payload size does not match byte_size", result.stderr)
        self.assert_output_empty()

    def test_invalid_base64_is_rejected(self):
        artifact = self.make_artifact("bad-base64.bin", b"abc", payload_base64="!!!!")
        package_path = self.write_package(self.make_envelope([artifact]))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bad-base64.bin", result.stderr)
        self.assertIn("payload_base64 is invalid", result.stderr)
        self.assert_output_empty()

    def test_invalid_schema_is_rejected(self):
        package_path = self.write_package(
            self.make_envelope(
                [self.make_artifact("must-not-extract.bin", b"payload")],
                schema_version=2,
            )
        )

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema_version must be 1", result.stderr)
        self.assert_output_empty()

    def test_duplicate_destinations_are_rejected(self):
        artifacts = [
            self.make_artifact("same.bin", b"first"),
            self.make_artifact("same.bin", b"second"),
        ]
        package_path = self.write_package(self.make_envelope(artifacts))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate destination", result.stderr)
        self.assert_output_empty()

    def test_case_insensitive_duplicate_destinations_are_rejected(self):
        artifacts = [
            self.make_artifact("Folder/File.bin", b"first"),
            self.make_artifact("folder/file.bin", b"second"),
        ]
        package_path = self.write_package(self.make_envelope(artifacts))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate destination", result.stderr)
        self.assert_output_empty()

    def test_path_traversal_is_rejected(self):
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("../escape.bin", b"outside")])
        )

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("../escape.bin", result.stderr)
        self.assertIn("path traversal", result.stderr)
        self.assert_output_empty()

    def test_dangerous_windows_paths_are_rejected(self):
        unsafe_paths = (
            "C:/escape.bin",
            "C:\\escape.bin",
            "\\\\server\\share\\escape.bin",
            "NUL.txt",
            "folder/file:stream",
            "folder//file.bin",
            "folder/./file.bin",
        )
        for index, unsafe_path in enumerate(unsafe_paths):
            with self.subTest(path=unsafe_path):
                package_path = self.write_package(
                    self.make_envelope([self.make_artifact(unsafe_path, b"payload")]),
                    filename="unsafe-{}.json".format(index),
                )
                result = self.run_unpacker(package_path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(repr(unsafe_path), result.stderr)
                self.assertIn("artifacts[0]", result.stderr)
                self.assert_output_empty()

    def test_invalid_relative_path_reports_its_artifact_index(self):
        artifacts = [
            self.make_artifact("valid.bin", b"valid"),
            self.make_artifact("NUL.txt", b"invalid path"),
        ]
        package_path = self.write_package(self.make_envelope(artifacts))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("artifacts[1]", result.stderr)
        self.assertIn("NUL.txt", result.stderr)
        self.assert_output_empty()

    def test_symlink_escape_is_rejected(self):
        outside_root = self.temporary_path / "outside"
        outside_root.mkdir()
        try:
            (self.output_root / "escape").symlink_to(outside_root, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest("symlinks are unavailable: {}".format(exc))
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("escape/payload.bin", b"outside")])
        )

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("escape/payload.bin", result.stderr)
        self.assertIn("symlink", result.stderr)
        self.assertFalse((outside_root / "payload.bin").exists())
        self.assertTrue((self.output_root / "escape").is_symlink())

    def test_existing_destination_prevents_all_extraction(self):
        existing = self.output_root / "already-there.bin"
        existing.write_bytes(b"preserve me")
        artifacts = [
            self.make_artifact("new-file.bin", b"new"),
            self.make_artifact("already-there.bin", b"replace"),
        ]
        package_path = self.write_package(self.make_envelope(artifacts))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already-there.bin", result.stderr)
        self.assertIn("destination already exists", result.stderr)
        self.assertFalse((self.output_root / "new-file.bin").exists())
        self.assertEqual(existing.read_bytes(), b"preserve me")

    def test_one_invalid_entry_prevents_all_extraction(self):
        artifacts = [
            self.make_artifact("valid-first.bin", b"valid"),
            self.make_artifact("invalid-second.bin", b"invalid", raw_sha256="f" * 64),
        ]
        package_path = self.write_package(self.make_envelope(artifacts))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid-second.bin", result.stderr)
        self.assertFalse((self.output_root / "valid-first.bin").exists())
        self.assertFalse((self.output_root / "invalid-second.bin").exists())
        self.assert_output_empty()

    def test_reports_multiple_artifact_errors_separately_from_schema_errors(self):
        artifacts = [
            self.make_artifact("bad-first.bin", b"first", raw_sha256="f" * 64),
            self.make_artifact("bad-second.bin", b"second", raw_sha256="e" * 64),
        ]
        package_path = self.write_package(
            self.make_envelope(artifacts, schema_version=2)
        )

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema: schema_version must be 1", result.stderr)
        self.assertIn("bad-first.bin: raw_sha256 does not match", result.stderr)
        self.assertIn("bad-second.bin: raw_sha256 does not match", result.stderr)
        self.assertEqual(result.stderr.count("raw_sha256 does not match"), 2)
        self.assert_output_empty()

    def test_oversized_package_input_is_rejected(self):
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("payload.bin", b"x")])
        )

        result = self.run_unpacker(package_path, options=("--max-input-bytes", "8"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exceeds --max-input-bytes", result.stderr)
        self.assert_output_empty()

    def test_oversized_decoded_payload_is_rejected(self):
        package_path = self.write_package(
            self.make_envelope([self.make_artifact("payload.bin", b"12345")])
        )

        result = self.run_unpacker(package_path, options=("--max-payload-bytes", "4"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("payload.bin", result.stderr)
        self.assertIn("exceeds --max-payload-bytes", result.stderr)
        self.assert_output_empty()

    def test_file_directory_prefix_conflict_is_rejected(self):
        artifacts = [
            self.make_artifact("conflict", b"file"),
            self.make_artifact("conflict/child.bin", b"child"),
        ]
        package_path = self.write_package(self.make_envelope(artifacts))

        result = self.run_unpacker(package_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("destination conflicts with artifact file", result.stderr)
        self.assert_output_empty()

    def test_write_failure_rolls_back_created_files_and_directories(self):
        artifacts = [
            self.make_artifact("nested/first.bin", b"first payload"),
            self.make_artifact("nested/second.bin", b"second payload"),
        ]
        package_path = self.write_package(self.make_envelope(artifacts))
        actual_writer = unpacker_module.write_payload

        def fail_after_partial_write(destination, payload, relative_path, created_files):
            if relative_path == "nested/second.bin":
                with destination.open("xb") as output_file:
                    created_files.append(destination)
                    output_file.write(payload[:3])
                raise OSError("synthetic write failure")
            actual_writer(destination, payload, relative_path, created_files)

        with mock.patch.object(
            unpacker_module, "write_payload", side_effect=fail_after_partial_write
        ):
            with self.assertRaisesRegex(unpacker_module.UnpackError, "rollback completed"):
                unpacker_module.unpack_package(package_path, self.output_root)

        self.assertFalse((self.output_root / "nested").exists())
        self.assert_output_empty()

    def test_packager_unpacker_roundtrip_matches_sha256(self):
        source_root = self.temporary_path / "source-files"
        source_root.mkdir()
        source_data = {
            "nested/vietnamese.txt": "Sifu giả lập: ă, â, đ, ê, ô, ơ, ư.\r\nDòng hai\n".encode(
                "utf-8"
            ),
            "binary.bin": bytes(range(64)) + b"\x00\xff",
        }
        for relative_path, payload in source_data.items():
            file_path = source_root / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(payload)

        package_path = self.temporary_path / "roundtrip.b64"
        pack_command = [
            sys.executable,
            str(PACKAGER),
            "--source-root",
            str(source_root),
            "--output",
            str(package_path),
            "--package-id",
            "roundtrip-package",
            "--project-id",
            "synthetic-roundtrip",
            "--batch-id",
            "test-batch",
            "--base64-output",
            *source_data.keys(),
        ]
        packed = subprocess.run(pack_command, cwd=WORKSPACE, capture_output=True, text=True)
        self.assertEqual(packed.returncode, 0, packed.stderr)

        unpacked = self.run_unpacker(package_path)
        self.assertEqual(unpacked.returncode, 0, unpacked.stderr)
        for relative_path, original_bytes in source_data.items():
            extracted_bytes = (self.output_root / relative_path).read_bytes()
            self.assertEqual(extracted_bytes, original_bytes)
            self.assertEqual(
                hashlib.sha256(extracted_bytes).hexdigest(),
                hashlib.sha256(original_bytes).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()