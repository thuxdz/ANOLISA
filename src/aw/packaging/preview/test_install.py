"""Exercise package boundaries and installation without requiring Rust builds."""

import importlib.util
import json
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("installer", HERE / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        target = HERE.parents[1] / "target/preview"
        target.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="install-test-", dir=target)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.prefix = self.root / "installed"
        self.core = self.bundle("core", ["aw-core"])
        self.provider = self.bundle("provider", ["aw-provider-sec-core"])
        self.combined = self.bundle("combined", ["aw-core", "aw-provider-sec-core"])

    def bundle(self, directory, components, version="0.1.0-preview.1"):
        root = self.root / directory
        manifests = []
        for component in components:
            names = (["bin/aw"] if component == "aw-core" else [
                f"libexec/aw/providers/sec-core/{name}"
                for name in ("aw-provider-sec-core", "agent-sec-cli", "agent-sec-daemon")])
            files = {}
            for name in names:
                target = root / "payload" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"#!/bin/sh\necho {name}\n")
                files[name] = {"sha256": installer.digest(target), "mode": 0o755}
            manifests.append({"format": 1, "component": component, "version": version,
                              "source_commit": "a" * 40, "os": "linux",
                              "arch": platform.machine(), "provider_protocol": "aw-provider/v1alpha1",
                              "requires": {} if component == "aw-core" else {"aw-core": version},
                              "files": files})
        (root / "manifest.json").write_text(json.dumps({"format": 1, "components": manifests}))
        return root

    def test_split_matches_combined_and_reinstall_refused(self):
        installer.install(self.core, self.prefix)
        installer.install(self.provider, self.prefix)
        other = self.root / "combined-prefix"
        installer.install(self.combined, other)
        self.assertEqual(installer.read_installed(self.prefix), installer.read_installed(other))
        for path in (self.prefix / "libexec").rglob("*"):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (other / path.relative_to(self.prefix)).read_bytes())
        with self.assertRaisesRegex(ValueError, "already installed"):
            installer.install(self.combined, other)

    def test_missing_core_or_version_mismatch_changes_nothing(self):
        with self.assertRaisesRegex(ValueError, "requires matching"):
            installer.install(self.provider, self.prefix)
        self.assertFalse(self.prefix.exists())
        installer.install(self.core, self.prefix)
        wrong = self.bundle("wrong", ["aw-provider-sec-core"], "0.1.0-preview.2")
        with self.assertRaisesRegex(ValueError, "requires matching"):
            installer.install(wrong, self.prefix)
        self.assertEqual(set(installer.read_installed(self.prefix)), {"aw-core"})
        self.assertFalse((self.prefix / "libexec").exists())

    def test_tamper_and_symlink_are_rejected(self):
        binary = self.core / "payload/bin/aw"
        original = binary.read_bytes()
        binary.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            installer.install(self.core, self.prefix)
        binary.unlink()
        outside = self.root / "outside"
        outside.write_bytes(original)
        binary.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            installer.install(self.core, self.prefix)
        self.assertFalse(self.prefix.exists())

    def test_copy_failure_rolls_back_owned_files(self):
        with patch.object(installer.shutil, "copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                installer.install(self.combined, self.prefix)
        self.assertFalse(self.prefix.exists())

    def test_uninstall_preserves_external_and_unknown_files(self):
        installer.install(self.combined, self.prefix)
        unknown = self.prefix / "user-note"
        unknown.write_text("keep")
        external = self.root / "journal"
        external.write_text("keep")
        binary = self.prefix / "bin/aw"
        original = binary.read_bytes()
        binary.write_text("modified")
        with self.assertRaisesRegex(ValueError, "modified"):
            installer.uninstall(self.prefix)
        self.assertEqual(len(installer.read_installed(self.prefix)), 2)
        binary.write_bytes(original)
        installer.uninstall(self.prefix)
        self.assertEqual(unknown.read_text(), "keep")
        self.assertEqual(external.read_text(), "keep")
        self.assertFalse(binary.exists())

    def test_config_is_external_exclusive_and_maps_real_qoder_tool(self):
        installer.install(self.combined, self.prefix)
        qoder = self.root / "qoder"
        qoder.write_text("#!/bin/sh\necho 1.1.64\n")
        qoder.chmod(0o755)
        config = self.root / "aw.json"
        args = (self.prefix, config, self.root / "state", self.root / "sec.sock", qoder)
        installer.configure(*args)
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        spec = json.loads(config.read_text())["spec"]
        self.assertEqual(spec["providers"]["security"]["config"]["tools"]["Bash"]["input_pointer"], "/command")
        with self.assertRaises(FileExistsError):
            installer.configure(*args)
        with self.assertRaisesRegex(ValueError, "outside"):
            installer.configure(self.prefix, self.prefix / "config", *args[2:])


if __name__ == "__main__":
    unittest.main()
