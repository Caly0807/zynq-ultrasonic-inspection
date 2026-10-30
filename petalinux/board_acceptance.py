import argparse
import base64
import hashlib
import ipaddress
import json
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ValueError("Board acceptance refuses redirects and never forwards credentials")


def validate_token(token):
    if not 32 <= len(token) <= 128 or any(not character.isascii() or not (character.isalnum() or character in "-_") for character in token):
        raise ValueError("Invalid existing operator credential")
    return token


def existing_token(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing a symlink credential")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "r", encoding="ascii") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
            raise ValueError("Operator credential must be a private regular file owned by the caller")
        return validate_token(stream.read(256).strip())


def wsl_token():
    if os.name != "nt":
        raise ValueError("Use --token-file when running within Linux")
    process = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04-PetaLinux", "-u", "fpga", "--", "cat",
                              "/home/fpga/logs/md1213_linux_net/jtag-smoke/operator.token"],
                             capture_output=True, text=True, timeout=15)
    if process.returncode:
        raise ValueError("Could not read the private existing WSL credential; it was not printed")
    return validate_token(process.stdout.strip())


class BoardClient:
    def __init__(self, address, port, token, *, allow_host_test=False):
        address = ipaddress.IPv4Address(address)
        if str(address) != "192.168.77.2" and not (allow_host_test and address.is_loopback):
            raise ValueError("Only the configured isolated board address or an explicit loopback test is accepted")
        if not 1024 <= port <= 65535:
            raise ValueError("Invalid service port")
        self.base = f"http://{address}:{port}"
        self.authorization = "Basic " + base64.b64encode(("operator:" + validate_token(token)).encode("ascii")).decode("ascii")
        self.allow_host_test = allow_host_test
        self.opener = build_opener(ProxyHandler({}), NoRedirects())

    def request(self, route, payload=None):
        if not route.startswith("/") or route.startswith("//"):
            raise ValueError("Expected a fixed relative API route")
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        request = Request(self.base + route, data=data, headers={"Authorization": self.authorization,
                          "Origin": self.base, "Content-Type": "application/json"})
        with self.opener.open(request, timeout=8) as response:
            body = response.read(8 * 1024 ** 2 + 1)
            if len(body) > 8 * 1024 ** 2:
                raise ValueError("Board response exceeded acceptance budget")
            return body

    def read_json(self, route, payload=None):
        def invalid(value):
            raise ValueError("Non-finite service response")
        result = json.loads(self.request(route, payload), parse_constant=invalid)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object from the board")
        return result

    def verify(self, run_demo=False):
        device = self.read_json("/api/device")
        runtime = device.get("runtime")
        if runtime != "ARM_LINUX" and not (self.allow_host_test and runtime == "HOST_TEST_ONLY"):
            raise ValueError("ARM Linux service runtime was not reported; host simulation is not board acceptance")
        if device.get("transmit_enabled") is not False or device.get("motion_enabled") is not False:
            raise ValueError("Unexpected transmit or motion state; no further acceptance operations")
        state = self.read_json("/api/state")
        if state.get("schema") != "ut-manual-map/1" or state.get("source") not in ("DEMO", "RECORDED", "LIVE"):
            raise ValueError("Unexpected board session schema or source")
        result = {"schema": "ut-board-acceptance/1", "runtime_reported": runtime,
                  "host_test_only": runtime != "ARM_LINUX", "source": state["source"],
                  "authenticated_http": True, "transmit_enabled": False, "motion_enabled": False,
                  "demo_transaction_verified": False, "camera_verified": False,
                  "physical_touch_verified": False, "real_ultrasound_verified": False,
                  "hole_shape_reconstructed": False}
        if not run_demo:
            result["operations"] = "READ_ONLY_HTTP"
            return result
        if state["source"] != "DEMO" or state.get("capture_adapter_configured") is not False:
            raise ValueError("A diagnostic POST is allowed only in an explicitly synthetic DEMO session")
        spec = state["spec"]
        payload = {"session_id": state["session_id"], "submission_id": "acceptance-" + uuid.uuid4().hex,
                   "position_mm": [min(spec["grid_mm"], spec["length_mm"]) / 2,
                                   min(spec["grid_mm"], spec["width_mm"]) / 2], "position_confirmed": True}
        first = self.read_json("/api/point", payload)
        second = self.read_json("/api/point", payload)
        if first.get("ok") is not True or first.get("replayed") is not False or second.get("replayed") is not True or first["point"]["point_id"] != second["point"]["point_id"]:
            raise ValueError("DEMO submission idempotency failed; do not automatically retry a different ID")
        if first["point"]["source"] != "DEMO":
            raise ValueError("Synthetic acceptance point lost its DEMO label")
        committed = self.read_json("/api/state")
        if committed["point_count"] != state["point_count"] + 1:
            raise ValueError("Unexpected concurrent point count; review the session manually")
        exported_map = self.request("/export/map.csv")
        exported_report = self.request("/export/report.html")
        if b",DEMO," not in exported_map or b"DEMO" not in exported_report:
            raise ValueError("Exported map/report lost the DEMO label")
        result.update({"operations": "EXPLICIT_SYNTHETIC_DEMO_TRANSACTION", "demo_transaction_verified": True,
                       "point_count_delta": 1, "idempotency_verified": True,
                       "map_sha256": hashlib.sha256(exported_map).hexdigest(),
                       "report_sha256": hashlib.sha256(exported_report).hexdigest()})
        return result

    def dma_selftest(self, hv_off_confirmed=False):
        if hv_off_confirmed is not True:
            raise ValueError("Confirm the current HV-OFF condition before requesting the FPGA DMA TEST")
        device = self.read_json("/api/device")
        if device.get("runtime") != "ARM_LINUX" or device.get("transmit_enabled") is not False or device.get("motion_enabled") is not False:
            raise ValueError("DMA TEST requires the safe ARM Linux runtime; host fixtures are not board evidence")
        before = self.read_json("/api/state")
        record = self.read_json("/api/transport/selftest", {"hv_off_confirmed": True, "test": "FPGA_RAMP_4096"})
        test = record.get("selftest", {})
        expected_hash = hashlib.sha256(bytes(range(256)) * 16).hexdigest()
        if (record.get("schema") != "ut-linux-transport/1" or record.get("transmit_enabled") is not False or
                test.get("schema") != "ut-dma-selftest/1" or test.get("source") != "FPGA_DMA_TEST_NOT_ULTRASOUND" or
                test.get("sample_count") != 4096 or test.get("sample_period_ns") != 40 or
                test.get("sample_sha256") != expected_hash or test.get("measurement_authorized") is not False or
                test.get("transmit_enabled") is not False or test.get("motion_enabled") is not False):
            raise ValueError("FPGA DMA ramp TEST response failed ABI/source/safety checks")
        after = self.read_json("/api/state")
        if (before["session_id"], before["point_count"]) != (after["session_id"], after["point_count"]):
            raise ValueError("The measurement session changed during TEST; inspect it before retrying")
        return record


def main(argv=None):
    parser = argparse.ArgumentParser(description="Authenticated board HTTP acceptance; no JTAG, HV, motion, SD or Flash commands")
    parser.add_argument("--host", default="192.168.77.2")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--allow-host-test", action="store_true")
    parser.add_argument("--run-demo-transaction", action="store_true")
    parser.add_argument("--run-dma-selftest", action="store_true")
    parser.add_argument("--hv-off-confirmed", action="store_true")
    parser.add_argument("--out", type=Path)
    tokens = parser.add_mutually_exclusive_group(required=True)
    tokens.add_argument("--token-file", type=Path)
    tokens.add_argument("--wsl-token", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.run_dma_selftest and not arguments.hv_off_confirmed:
        raise ValueError("Confirm HV OFF before reading credentials or running the DMA TEST")
    token = wsl_token() if arguments.wsl_token else existing_token(arguments.token_file)
    client = BoardClient(arguments.host, arguments.port, token, allow_host_test=arguments.allow_host_test)
    record = client.verify(arguments.run_demo_transaction)
    if arguments.run_dma_selftest:
        record["dma_test"] = client.dma_selftest(arguments.hv_off_confirmed)
        record["dma_selftest_verified"] = True
        record["operations"] = "EXPLICIT_FPGA_DMA_TEST" + ("_AND_SYNTHETIC_DEMO" if arguments.run_demo_transaction else "")
    encoded = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    if arguments.out:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, HTTPError, URLError, TimeoutError) as error:
        print("BOARD_ACCEPTANCE_FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(2)
