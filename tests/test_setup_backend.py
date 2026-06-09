"""Tests for the engine-build selection in scripts/setup.py.

The installer picks a llama-cpp-python build from the detected GPU vendor(s):
CUDA for NVIDIA when a wheel is installable, the vendor-neutral Vulkan build for
any other GPU (AMD/Intel, or NVIDIA on a Python without a CUDA wheel), CPU
otherwise. ``_choose_backend`` is pure, so we test the whole matrix; we also pin
the version gate of ``_cuda_wheel_available``. setup.py lives under scripts/, so
we load it by path.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mocca_setup", _ROOT / "scripts" / "setup.py")
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)


class TestChooseBackend(unittest.TestCase):
    def test_nvidia_with_cuda_wheel_gets_cuda(self):
        self.assertEqual(setup._choose_backend({"nvidia"}, cuda_ok=True), "cuda")

    def test_nvidia_without_cuda_wheel_gets_vulkan(self):
        # e.g. NVIDIA on Python 3.13/3.14 - no CUDA wheel, but Vulkan installs.
        self.assertEqual(setup._choose_backend({"nvidia"}, cuda_ok=False), "vulkan")

    def test_amd_gets_vulkan(self):
        self.assertEqual(setup._choose_backend({"amd"}, cuda_ok=True), "vulkan")

    def test_intel_gets_vulkan(self):
        self.assertEqual(setup._choose_backend({"intel"}, cuda_ok=True), "vulkan")

    def test_nvidia_plus_amd_prefers_cuda_when_available(self):
        self.assertEqual(setup._choose_backend({"nvidia", "amd"}, cuda_ok=True), "cuda")

    def test_no_gpu_gets_cpu(self):
        self.assertEqual(setup._choose_backend(set(), cuda_ok=True), "cpu")
        self.assertEqual(setup._choose_backend(set(), cuda_ok=False), "cpu")


class TestCudaWheelAvailable(unittest.TestCase):
    def test_new_python_has_no_cuda_wheel(self):
        # No prebuilt CUDA wheels for 3.13/3.14 regardless of platform.
        self.assertFalse(setup._cuda_wheel_available((3, 13)))
        self.assertFalse(setup._cuda_wheel_available((3, 14)))

    def test_supported_python_tracks_platform(self):
        # On a prebuilt platform a 3.10-3.12 interpreter has a wheel; the result
        # equals the platform flag either way (and is always a bool).
        for py in ((3, 10), (3, 11), (3, 12)):
            self.assertEqual(setup._cuda_wheel_available(py), setup._HAS_PREBUILT)


class TestFallbackChain(unittest.TestCase):
    def test_chains_step_down(self):
        self.assertEqual(setup._FALLBACK["cuda"], ["cuda", "vulkan", "cpu"])
        self.assertEqual(setup._FALLBACK["vulkan"], ["vulkan", "cpu"])
        self.assertEqual(setup._FALLBACK["cpu"], ["cpu"])

    def test_every_backend_has_an_index_and_wheel(self):
        for backend in ("cuda", "vulkan", "cpu"):
            index, wheel = setup._BACKENDS[backend]
            self.assertTrue(index.startswith("https://"))
            self.assertIn("llama-cpp-python==", wheel)


if __name__ == "__main__":
    unittest.main()
