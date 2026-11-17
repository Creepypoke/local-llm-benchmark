import json
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from llbench.common import ROOT
from llbench.install import detect_environment, download, extract, select_assets


class ReleaseInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (ROOT / "config/llama-release.json").read_text(encoding="utf-8")
        )

    def select(self, system, arch, backend, driver=None):
        return select_assets(
            self.manifest,
            {"os": system, "arch": arch, "backend": backend, "driver_cuda": driver},
        )

    def test_macos_metal(self):
        assets, cuda = self.select("Darwin", "arm64", "metal")
        self.assertEqual(assets[0]["name"], "llama-b12359-bin-macos-arm64.tar.gz")
        self.assertIsNone(cuda)

    def test_windows_cuda_includes_runtime_without_toolkit(self):
        assets, cuda = self.select("Windows", "x64", "cuda", (13, 5))
        self.assertEqual(cuda, "13.5")
        self.assertEqual(len(assets), 2)
        self.assertEqual(assets[1]["name"], "cudart-llama-bin-win-cuda-13.5-x64.zip")

    def test_older_windows_driver_selects_compatible_archive(self):
        assets, cuda = self.select("Windows", "x64", "cuda", (12, 8))
        self.assertEqual(cuda, "12.4")
        self.assertIn("cuda-12.4", assets[0]["name"])

    def test_linux_cuda_has_runtime(self):
        assets, cuda = self.select("Linux", "x64", "cuda", (12, 9))
        self.assertEqual(cuda, "12.8")
        self.assertEqual(assets[1]["name"], "cudart-" + assets[0]["name"])

    def test_cpu_platforms(self):
        for system in ["Darwin", "Windows", "Linux"]:
            for arch in ["x64", "arm64"]:
                assets, cuda = self.select(system, arch, "cpu")
                self.assertEqual(len(assets), 1)
                self.assertIsNone(cuda)

    def test_missing_cuda_archive_is_error_no_cpu_fallback(self):
        with self.assertRaises(RuntimeError):
            self.select("Linux", "arm64", "cuda", (12, 8))

    def test_too_old_driver_is_error(self):
        with self.assertRaises(RuntimeError):
            self.select("Windows", "x64", "cuda", (11, 8))

    @patch("llbench.install.platform.machine", return_value="AMD64")
    @patch("llbench.install.platform.system", return_value="Windows")
    @patch("llbench.install.shutil.which", return_value="nvidia-smi.exe")
    @patch(
        "llbench.install.subprocess.run",
        return_value=Mock(returncode=0, stdout="NVIDIA-SMI | CUDA Version: 13.5 |"),
    )
    def test_detect_cuda_from_driver_without_nvcc(self, *_):
        env = detect_environment()
        self.assertEqual(env["backend"], "cuda")
        self.assertEqual(env["arch"], "x64")
        self.assertEqual(env["driver_cuda"], (13, 5))

    def test_zip_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../outside.txt", "bad")
            with self.assertRaises(RuntimeError):
                extract(archive, root / "output")
            self.assertFalse((root / "outside.txt").exists())

    def test_assets_have_official_urls_and_sha256(self):
        for asset in self.manifest["assets"]:
            self.assertTrue(
                asset["url"].startswith(
                    "https://github.com/ggml-org/llama.cpp/releases/download/b12359/"
                )
            )
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")


class ArchiveChecksumTests(unittest.TestCase):
    def test_valid_cached_archive_does_not_download_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / ".cache/downloads"
            cache.mkdir(parents=True)
            archive = cache / "fixture.zip"
            archive.write_bytes(b"archive fixture")
            asset = {
                "name": archive.name,
                "url": "https://github.com/ggml-org/llama.cpp/fixture",
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
            with (
                patch("llbench.install.ROOT", root),
                patch("llbench.install.httpx.stream") as network,
            ):
                self.assertEqual(download(asset), archive)
                network.assert_not_called()

    def test_bad_checksum_does_not_activate_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = {
                "name": "fixture.zip",
                "url": "https://github.com/ggml-org/llama.cpp/fixture",
                "sha256": "0" * 64,
            }
            response = Mock()
            response.iter_bytes.return_value = iter([b"corrupt archive"])
            with (
                patch("llbench.install.ROOT", root),
                patch("llbench.install.httpx.stream") as network,
            ):
                network.return_value.__enter__.return_value = response
                with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                    download(asset)
            self.assertFalse((root / ".cache/downloads/fixture.zip").exists())
            self.assertFalse(list((root / ".cache/downloads").glob("*.part")))
