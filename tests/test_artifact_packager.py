import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
PACKAGER = WORKSPACE / "tools" / "artifact_packager.py"


class ArtifactPackagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(dir=WORKSPACE)
        self.temporary_path = Path(self.temporary_directory.name)
        self.source_root = self.temporary_path / "source"
        self.source_root.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_source(self, relative_path, content):
        source_path = self.source_root / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(content)
        return source_path

    def run_packager(self, files, options=(), output_name="package.json"):
        output_path = self.temporary_path / output_name
        command = [
            sys.executable,
            str(PACKAGER),
            "--source-root",
            str(self.source_root),
            "--output",
            str(output_path),
            "--package-id",
            "package-test-001",
            "--project-id",
            "synthetic-test-project",
            *options,
            *files,
        ]
        result = subprocess.run(
            command,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
        )
        return result, output_path

    def load_json_package(self, output_path):
        return json.loads(output_path.read_text(encoding="utf-8"))

    def test_json_envelope_schema_and_metadata(self):
        raw_bytes = b"synthetic payload"
        self.write_source("sample.txt", raw_bytes)

        result, output_path = self.run_packager(
            ["sample.txt"], options=("--batch-id", "batch-test-002")
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = self.load_json_package(output_path)
        self.assertEqual(envelope["schema_version"], 1)
        self.assertEqual(envelope["format"], "prism-artifact-package")
        self.assertEqual(envelope["package_id"], "package-test-001")
        self.assertEqual(envelope["project_id"], "synthetic-test-project")
        self.assertEqual(envelope["batch_id"], "batch-test-002")
        self.assertEqual(len(envelope["artifacts"]), 1)

        artifact = envelope["artifacts"][0]
        self.assertEqual(
            set(artifact),
            {"relative_path", "byte_size", "raw_sha256", "content_type", "payload_base64"},
        )
        self.assertEqual(artifact["relative_path"], "sample.txt")
        self.assertEqual(artifact["byte_size"], len(raw_bytes))
        self.assertEqual(artifact["raw_sha256"], hashlib.sha256(raw_bytes).hexdigest())
        self.assertEqual(artifact["content_type"], "text/plain")
        self.assertEqual(base64.b64decode(artifact["payload_base64"], validate=True), raw_bytes)

    def test_batch_id_defaults_to_null(self):
        self.write_source("empty.bin", b"")
        result, output_path = self.run_packager(["empty.bin"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.load_json_package(output_path)["batch_id"])

    def test_multiple_files_are_included(self):
        self.write_source("first.bin", b"first")
        self.write_source("nested/second.bin", b"second")
        result, output_path = self.run_packager(["first.bin", "nested/second.bin"])

        self.assertEqual(result.returncode, 0, result.stderr)
        artifacts = self.load_json_package(output_path)["artifacts"]
        self.assertEqual(
            {artifact["relative_path"] for artifact in artifacts},
            {"first.bin", "nested/second.bin"},
        )

    def test_base64_output_encodes_the_complete_json_envelope(self):
        self.write_source("payload.bin", b"base64 test")
        result, output_path = self.run_packager(
            ["payload.bin"], options=("--base64-output",), output_name="package.b64"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        decoded_json = base64.b64decode(output_path.read_bytes(), validate=True)
        envelope = json.loads(decoded_json.decode("utf-8"))
        self.assertEqual(envelope["format"], "prism-artifact-package")
        self.assertEqual(envelope["artifacts"][0]["relative_path"], "payload.bin")

    def test_utf8_vietnamese_and_crlf_lf_bytes_are_preserved(self):
        raw_bytes = "Tiếng Việt: ă â đ ê ô ơ ư, dấu ấ ề.\r\nCRLF rồi LF\nKết thúc.".encode(
            "utf-8"
        )
        self.write_source("vietnamese.csv", raw_bytes)
        result, output_path = self.run_packager(["vietnamese.csv"])

        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = self.load_json_package(output_path)["artifacts"][0]
        self.assertEqual(
            base64.b64decode(artifact["payload_base64"], validate=True), raw_bytes
        )
        self.assertIn(b"\r\n", raw_bytes)
        self.assertIn(b"\n", raw_bytes.replace(b"\r\n", b""))

    def test_binary_bytes_are_preserved(self):
        raw_bytes = bytes(range(256)) + b"\x00\xff\x80"
        self.write_source("binary.dat", raw_bytes)
        result, output_path = self.run_packager(["binary.dat"])

        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = self.load_json_package(output_path)["artifacts"][0]
        self.assertEqual(base64.b64decode(artifact["payload_base64"], validate=True), raw_bytes)

    def test_byte_size_and_sha256_match_raw_input(self):
        raw_bytes = b"hash-and-size-check\x00"
        self.write_source("digest.bin", raw_bytes)
        result, output_path = self.run_packager(["digest.bin"])

        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = self.load_json_package(output_path)["artifacts"][0]
        self.assertEqual(artifact["byte_size"], len(raw_bytes))
        self.assertEqual(artifact["raw_sha256"], hashlib.sha256(raw_bytes).hexdigest())

    def test_duplicate_file_is_rejected(self):
        self.write_source("duplicate.bin", b"same")
        result, output_path = self.run_packager(["duplicate.bin", "duplicate.bin"])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate input path", result.stderr)
        self.assertFalse(output_path.exists())

    def test_existing_output_is_not_overwritten(self):
        self.write_source("input.bin", b"new content")
        output_path = self.temporary_path / "existing.json"
        original_bytes = b"existing package must remain untouched"
        output_path.write_bytes(original_bytes)

        result, returned_path = self.run_packager(["input.bin"], output_name="existing.json")

        self.assertEqual(returned_path, output_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("package already exists", result.stderr)
        self.assertEqual(output_path.read_bytes(), original_bytes)

    def test_path_traversal_is_rejected(self):
        outside_path = self.temporary_path / "outside.txt"
        outside_path.write_bytes(b"outside source root")
        result, output_path = self.run_packager(["../outside.txt"])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not normalized", result.stderr)
        self.assertFalse(output_path.exists())

    def test_windows_style_traversal_is_rejected_on_all_platforms(self):
        result, output_path = self.run_packager([r"..\outside.txt"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output_path.exists())

    def test_drive_designator_is_rejected_on_all_platforms(self):
        result, output_path = self.run_packager(["C:/escape.txt"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not portable", result.stderr)
        self.assertFalse(output_path.exists())

    def test_internal_dotdot_is_rejected(self):
        self.write_source("safe.txt", b"safe")
        (self.source_root / "sub").mkdir()
        result, output_path = self.run_packager(["sub/../safe.txt"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not normalized", result.stderr)
        self.assertFalse(output_path.exists())

    def test_symlink_escape_is_rejected(self):
        outside_path = self.temporary_path / "outside-link-target.bin"
        outside_path.write_bytes(b"outside source root")
        link_path = self.source_root / "escape-link.bin"
        try:
            link_path.symlink_to(outside_path)
        except (NotImplementedError, OSError) as exc:
            self.skipTest("symlinks are unavailable: {}".format(exc))

        result, output_path = self.run_packager(["escape-link.bin"])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("escapes the source root", result.stderr)
        self.assertFalse(output_path.exists())

    def test_oversized_file_is_rejected_before_package_creation(self):
        self.write_source("too-large.bin", b"1234")
        result, output_path = self.run_packager(
            ["too-large.bin"], options=("--max-file-bytes", "3")
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exceeds --max-file-bytes", result.stderr)
        self.assertFalse(output_path.exists())

    def test_oversized_json_package_is_rejected(self):
        self.write_source("small.bin", b"x")
        result, output_path = self.run_packager(
            ["small.bin"], options=("--max-package-bytes", "1")
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exceeds --max-package-bytes", result.stderr)
        self.assertFalse(output_path.exists())

    def test_package_limit_accounts_for_outer_base64_size(self):
        self.write_source("small.bin", b"x")
        json_result, json_path = self.run_packager(["small.bin"], output_name="plain.json")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        json_size = json_path.stat().st_size

        result, output_path = self.run_packager(
            ["small.bin"],
            options=("--base64-output", "--max-package-bytes", str(json_size)),
            output_name="wrapped.b64",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exceeds --max-package-bytes", result.stderr)
        self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()