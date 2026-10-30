import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ElementTree

PETALINUX = Path(__file__).resolve().parents[3] / "petalinux"
sys.path.insert(0, str(PETALINUX))
SPECIFICATION = importlib.util.spec_from_file_location("touch_metadata", PETALINUX / "check_md1213_touch.py")
auditor = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(auditor)


class TouchMetadataTests(unittest.TestCase):
    def setUp(self):
        self.root = ElementTree.fromstring("<SYSTEM><MODULES/><EXTERNALPORTS/><MEMRANGES/></SYSTEM>")
        self.gpio = ElementTree.SubElement(self.root.find("MODULES"), "MODULE", INSTANCE="touch_gpio", MODTYPE="axi_gpio")
        parameters = ElementTree.SubElement(self.gpio, "PARAMETERS")
        self.parameters = {}
        for name, value in {"C_GPIO_WIDTH": "4", "C_ALL_INPUTS": "0", "C_ALL_OUTPUTS": "0", "C_TRI_DEFAULT": "0xFFFFFFFF",
                            "C_DOUT_DEFAULT": "0x00000000", "C_IS_DUAL": "0", "C_INTERRUPT_PRESENT": "1"}.items():
            self.parameters[name] = ElementTree.SubElement(parameters, "PARAMETER", NAME=name, VALUE=value)
        self.range = ElementTree.SubElement(self.root.find("MEMRANGES"), "MEMRANGE", INSTANCE="touch_gpio", BASEVALUE="0x41210000", HIGHVALUE="0x4121FFFF")
        self.ports = {}
        for name, direction in {"touch_input": "I", "touch_output": "O", "touch_tristate": "O"}.items():
            self.ports[name] = ElementTree.SubElement(self.root.find("EXTERNALPORTS"), "PORT", NAME=name, DIR=direction, LEFT="3", RIGHT="0")
        interrupts = ElementTree.SubElement(self.root.find("MODULES"), "MODULE", INSTANCE="interrupts")
        pin = ElementTree.SubElement(ElementTree.SubElement(interrupts, "PORTS"), "PORT", NAME="In4")
        self.connection = ElementTree.SubElement(ElementTree.SubElement(pin, "CONNECTIONS"), "CONNECTION", INSTANCE="touch_gpio", PORT="ip2intc_irpt")

    def audit(self):
        report = {"checks": [], "failures": [], "passed": True, "sha256": "test-only", "has_bitstream": False}
        with patch.object(auditor, "audit_hardware", return_value=report), patch.object(auditor, "read_hardware", return_value=(self.root, False)):
            return auditor.audit_touch(Path("test-only.hwh"))

    def test_correct_extension_passes_thirteen_additional_checks(self):
        report = self.audit()
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["checks"]), 13)

    def test_wrong_width_is_rejected(self):
        self.parameters["C_GPIO_WIDTH"].set("VALUE", "3")
        self.assertFalse(self.audit()["passed"])

    def test_unsafe_output_enable_default_is_rejected(self):
        self.parameters["C_TRI_DEFAULT"].set("VALUE", "0x00000000")
        self.assertFalse(self.audit()["passed"])

    def test_missing_interrupt_support_is_rejected(self):
        self.parameters["C_INTERRUPT_PRESENT"].set("VALUE", "0")
        self.assertFalse(self.audit()["passed"])

    def test_wrong_address_is_rejected(self):
        self.range.set("BASEVALUE", "0x41200000")
        self.assertFalse(self.audit()["passed"])

    def test_wrong_direction_is_rejected(self):
        self.ports["touch_input"].set("DIR", "O")
        self.assertFalse(self.audit()["passed"])

    def test_wrong_interrupt_route_is_rejected(self):
        self.connection.set("INSTANCE", "backlight")
        self.assertFalse(self.audit()["passed"])

    def test_duplicate_gpio_is_rejected(self):
        ElementTree.SubElement(self.root.find("MODULES"), "MODULE", INSTANCE="touch_gpio", MODTYPE="axi_gpio")
        self.assertFalse(self.audit()["passed"])


if __name__ == "__main__":
    unittest.main()
