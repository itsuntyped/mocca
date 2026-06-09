"""Tests for multi-vendor GPU detection (hardware.py).

Mocca now detects NVIDIA, AMD, and Intel GPUs so the installer can pick the right
engine build and the UI can show the card. We can't exercise real nvidia-smi /
registry / sysfs in the offline suite, so we test the pure classification and
selection logic, and the nvidia-smi parser against a faked subprocess.
"""

from __future__ import annotations

import types
import unittest
from unittest import mock

from src import hardware


class TestVendorClassification(unittest.TestCase):
    def test_from_name(self):
        self.assertEqual(hardware._vendor_from_name("NVIDIA GeForce RTX 3070"), "nvidia")
        self.assertEqual(hardware._vendor_from_name("AMD Radeon RX 6700 XT"), "amd")
        self.assertEqual(hardware._vendor_from_name("Intel(R) Arc(TM) A770"), "intel")
        self.assertEqual(hardware._vendor_from_name("Intel(R) UHD Graphics 770"), "intel")
        self.assertIsNone(hardware._vendor_from_name("Microsoft Basic Display Adapter"))
        self.assertIsNone(hardware._vendor_from_name(None))

    def test_from_pci(self):
        # Windows MatchingDeviceId style and Linux sysfs vendor style.
        self.assertEqual(hardware._vendor_from_pci(r"PCI\VEN_10DE&DEV_2484"), "nvidia")
        self.assertEqual(hardware._vendor_from_pci("0x1002"), "amd")
        self.assertEqual(hardware._vendor_from_pci("0x8086"), "intel")
        self.assertIsNone(hardware._vendor_from_pci("0x1234"))
        self.assertIsNone(hardware._vendor_from_pci(None))


class TestDetectGpu(unittest.TestCase):
    def test_nvidia_smi_preferred(self):
        with mock.patch.object(hardware, "_nvidia_smi", return_value=(True, "RTX 3070", 8.0)), \
             mock.patch.object(hardware, "_enumerate_gpus", return_value=[("ignored", 99.0, "amd")]):
            self.assertEqual(hardware._detect_gpu(), (True, "RTX 3070", 8.0, "nvidia"))

    def test_falls_back_to_enumeration_for_amd_intel(self):
        # No nvidia-smi; a discrete AMD card should win over an Intel iGPU.
        with mock.patch.object(hardware, "_nvidia_smi", return_value=(False, None, None)), \
             mock.patch.object(hardware, "_enumerate_gpus", return_value=[
                 ("Intel UHD Graphics", None, "intel"),
                 ("AMD Radeon RX 6700 XT", 12.0, "amd"),
             ]):
            self.assertEqual(hardware._detect_gpu(), (True, "AMD Radeon RX 6700 XT", 12.0, "amd"))

    def test_ties_break_on_vram(self):
        with mock.patch.object(hardware, "_nvidia_smi", return_value=(False, None, None)), \
             mock.patch.object(hardware, "_enumerate_gpus", return_value=[
                 ("Radeon A", 8.0, "amd"),
                 ("Radeon B", 16.0, "amd"),
             ]):
            _, name, vram, _ = hardware._detect_gpu()
            self.assertEqual((name, vram), ("Radeon B", 16.0))

    def test_no_gpu(self):
        with mock.patch.object(hardware, "_nvidia_smi", return_value=(False, None, None)), \
             mock.patch.object(hardware, "_enumerate_gpus", return_value=[]):
            self.assertEqual(hardware._detect_gpu(), (False, None, None, None))


class TestNvidiaSmiParse(unittest.TestCase):
    def test_parses_name_and_vram(self):
        fake = types.SimpleNamespace(returncode=0, stdout="NVIDIA GeForce RTX 3070, 8192\n")
        with mock.patch.object(hardware.shutil, "which", return_value="nvidia-smi"), \
             mock.patch.object(hardware.subprocess, "run", return_value=fake):
            self.assertEqual(hardware._nvidia_smi(), (True, "NVIDIA GeForce RTX 3070", 8.0))

    def test_absent_smi(self):
        with mock.patch.object(hardware.shutil, "which", return_value=None):
            self.assertEqual(hardware._nvidia_smi(), (False, None, None))


class TestGpuVendors(unittest.TestCase):
    def test_union_of_smi_and_enumeration(self):
        with mock.patch.object(hardware, "_nvidia_smi", return_value=(True, "RTX", 8.0)), \
             mock.patch.object(hardware, "_enumerate_gpus", return_value=[
                 ("Intel UHD", None, "intel"), ("NVIDIA", 8.0, "nvidia")]):
            self.assertEqual(hardware.gpu_vendors(), {"nvidia", "intel"})

    def test_empty_when_no_gpu(self):
        with mock.patch.object(hardware, "_nvidia_smi", return_value=(False, None, None)), \
             mock.patch.object(hardware, "_enumerate_gpus", return_value=[]):
            self.assertEqual(hardware.gpu_vendors(), set())


if __name__ == "__main__":
    unittest.main()
