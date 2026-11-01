import argparse
import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CaptureContract:
    value_column: str = "value"
    radix: int = 10
    index_column: str | None = None
    time_column: str | None = None
    period_ns_column: str | None = None
    expected_period_ns: float | None = None
    expected_count: int | None = None
    expected_start_index: int = 0
    bits: int = 8
    skip_radix_row: bool = False
    expected_sha256: str | None = None
    reject_rail: bool = False


def finite_number(value, description):
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid " + description) from error
    if not math.isfinite(result):
        raise ValueError("Non-finite " + description)
    return result


def check_capture(path, contract):
    path = Path(path)
    if not 1 <= contract.bits <= 32 or contract.radix not in (2, 10, 16):
        raise ValueError("Invalid integer ADC contract")
    if contract.expected_count is not None and contract.expected_count < 1:
        raise ValueError("Expected count must be positive")
    if contract.expected_period_ns is not None:
        if finite_number(contract.expected_period_ns, "expected period") <= 0:
            raise ValueError("Expected period must be positive")
        if not (contract.period_ns_column or contract.time_column):
            raise ValueError("A period or time column is required to verify the expected clock")
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Expected a CSV file of at most 64 MiB")
    with path.open("rb") as stream:
        payload = stream.read(64 * 1024 * 1024 + 1)
    if len(payload) > 64 * 1024 * 1024:
        raise ValueError("CSV exceeds the file-size budget")
    digest = hashlib.sha256(payload).hexdigest()
    if contract.expected_sha256 is not None:
        expected = contract.expected_sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest != expected:
            raise ValueError("Original SHA256 mismatch")
    maximum_code = (1 << contract.bits) - 1
    values, periods, time_steps = [], [], []
    previous_time = None
    required = {name for name in (contract.value_column, contract.index_column,
                contract.time_column, contract.period_ns_column) if name}
    with io.StringIO(payload.decode("utf-8-sig"), newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Missing or repeated CSV columns")
        if not required <= set(reader.fieldnames):
            raise ValueError("CSV does not satisfy the declared column contract")
        for row_number, row in enumerate(reader):
            if None in row:
                raise ValueError("Unexpected extra CSV field")
            if contract.skip_radix_row and row_number == 0 and any(
                    (value or "").startswith("Radix -") for value in row.values()):
                continue
            if not any((value or "").strip() for value in row.values()):
                continue
            try:
                code = int(row[contract.value_column], contract.radix)
            except (ValueError, TypeError) as error:
                raise ValueError("Invalid unsigned ADC sample") from error
            if not 0 <= code <= maximum_code:
                raise ValueError("ADC sample outside declared bit width")
            if contract.index_column:
                try:
                    index = int(row[contract.index_column], 10)
                except (ValueError, TypeError) as error:
                    raise ValueError("Invalid sample index") from error
                if index != contract.expected_start_index + len(values):
                    raise ValueError("Sample indices are missing, repeated or reordered")
            if contract.period_ns_column:
                period = finite_number(row[contract.period_ns_column], "sample period")
                if period <= 0:
                    raise ValueError("Sample period must be positive")
                periods.append(period)
            if contract.time_column:
                current_time = finite_number(row[contract.time_column], "time in microseconds")
                if previous_time is not None:
                    interval_ns = (current_time - previous_time) * 1000
                    if interval_ns <= 0:
                        raise ValueError("Sample timestamps are not strictly increasing")
                    time_steps.append(interval_ns)
                previous_time = current_time
            values.append(code)
            if len(values) > 1_000_000:
                raise ValueError("CSV exceeds the sample-count budget")
    if not values or (contract.expected_count is not None and len(values) != contract.expected_count):
        raise ValueError("Sample count mismatch or empty capture")
    reference_period = contract.expected_period_ns
    if reference_period is not None and not (periods or time_steps):
        raise ValueError("Insufficient timestamp evidence to verify the declared clock")
    for group in (periods, time_steps):
        if group:
            if reference_period is None:
                reference_period = group[0]
            if any(not math.isclose(value, reference_period, rel_tol=1e-6, abs_tol=1e-6) for value in group):
                raise ValueError("Nonuniform or unexpected sample period")
    rails = sum(code in (0, maximum_code) for code in values)
    if contract.reject_rail and rails:
        raise ValueError("Rail samples rejected by the declared contract")
    return {"schema": "capture-integrity/1", "status": "PASS", "file": path.name,
            "sha256": digest, "original_sha256_checked": contract.expected_sha256 is not None,
            "sample_count": len(values), "minimum": min(values), "maximum": max(values),
            "rail_samples": rails, "period_ns": reference_period,
            "clock_evidence_present": bool(periods or time_steps),
            "physical_label_verified": False, "flaw_detection_verified": False}


def main():
    parser = argparse.ArgumentParser(description="Read-only integrity checks for uniformly sampled unsigned-integer CSV data")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--value-column", default="value")
    parser.add_argument("--radix", type=int, choices=(2, 10, 16), default=10)
    parser.add_argument("--index-column")
    parser.add_argument("--time-column", help="Timestamp column in microseconds")
    parser.add_argument("--period-ns-column")
    parser.add_argument("--expected-period-ns", type=float)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--expected-start-index", type=int, default=0)
    parser.add_argument("--bits", type=int, default=8)
    parser.add_argument("--skip-radix-row", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--reject-rail", action="store_true")
    arguments = vars(parser.parse_args())
    path = arguments.pop("csv")
    try:
        result = check_capture(path, CaptureContract(**arguments))
    except (OSError, ValueError, csv.Error) as error:
        print(json.dumps({"status": "FAIL", "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
