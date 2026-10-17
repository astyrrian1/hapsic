"""Compile the independent firmware observer, then replay real event traces."""

import json
import subprocess
from pathlib import Path

import pytest

from test_delivery_diagnostics import engine, inputs

ROOT = Path(__file__).parent


def test_firmware_uses_pinned_m5gfx_release_source_when_registry_version_is_missing():
    commit = "53a7184601f3667b030ba141c58b87ce2acfaa2a"  # upstream 0.2.19 tag
    for name in ("stamplc.yaml", "stamplc_desk.yaml", "components/hapsic/__init__.py"):
        assert commit in (ROOT / name).read_text(), "Unavailable registry pin needs its exact upstream release source"


def test_diagnostic_partition_preserves_factory_image_and_control_nvs_offsets():
    import csv

    rows = list(csv.reader(line for line in (ROOT / "diagnostic-partitions.csv").read_text().splitlines()
                           if not line.startswith("#")))
    partitions = {row[0]: row for row in rows}
    assert int(partitions["app0"][3], 16) == 0x10000, "ESPHome factory merge uses this application offset"
    assert int(partitions["nvs"][3], 16) == 0x9000
    assert int(partitions["nvs"][4], 16) == 0x5000
    assert partitions["hapsic_diag"][2] == "nvs"


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    header = ROOT / "components/hapsic_diagnostics/observer.h"
    assert header.exists(), "Independent firmware diagnostic observer is missing"
    binary = tmp_path_factory.mktemp("delivery-native") / "observer"
    subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-I", str(ROOT), str(ROOT / "tests/diagnostic_native.cpp"), "-o", str(binary)], check=True)

    def run(lines):
        result = subprocess.run([str(binary)], input="\n".join(lines), text=True, capture_output=True, check=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    return run


def test_native_psychrolib_point_and_signed_output(native):
    results = native(["measure 20 30 20 50 200 101325", "measure 20 30 20 20 200 101325"])
    assert results[0]["measurement"]["raw_kg_h"] == pytest.approx(0.6995387620211214, rel=1e-9)
    assert results[1]["measurement"]["raw_kg_h"] < 0


def test_native_warmup_stability_and_baseline_decisions_match_python(native):
    observer = engine()
    lines, expected = ["action baseline_start"], []
    observer.command("baseline_start")
    for period in range(4):
        start = 1000 + period * 1520
        for now in range(start, start + 1506, 5):
            observer.observe(inputs(now, 0, 30), now, now, synced=True)
            lines.append(f"tick {now} 0 20 30 20 30 200 101325")
        if period < 3:
            now = start + 1510
            observer.observe(inputs(now, 1, 30), now, now, synced=True)
            lines.append(f"tick {now} 1 20 30 20 30 200 101325")
        lines.append("snapshot")
        expected.append(observer.telemetry(now))
    actual = native(lines)
    for cpp, py in zip(actual, expected):
        assert cpp["qualification"]["reason"] == py["qualification"]["reason"]
        assert cpp["calibration"]["candidate"]["ready"] == py["calibration"]["candidate"]["ready"]
        assert len(cpp["calibration"]["candidate"]["windows"]) == len(py["calibration"]["candidate"]["windows"])


@pytest.mark.parametrize("known", [False, True])
def test_native_frozen_reference_performance_and_restart_match_python(native, known):
    observer = engine()
    lines = ["known"] if known else []
    now = 1000

    def action(name, revision=0):
        observer.command(name, revision, healthy=True)
        lines.append(f"action {name} {revision}")

    def advance(seconds, voltage=0, rh=30):
        nonlocal now
        stop = now + seconds
        while now <= stop:
            observer.observe(inputs(now, voltage, rh, known=known), now, now, synced=True)
            lines.append(f"tick {now} {voltage} 20 30 20 {rh} 200 101325")
            now += 5

    action("baseline_start")
    for _ in range(4):
        advance(1505)
        advance(5, 1)
    action("baseline_accept", 1)
    action("reference_start")
    for _ in range(3):
        advance(905)
        advance(1810, 9.5, 50)
    action("reference_accept", 2)
    expected = []
    for rh in (30, 50, 90):
        advance(905)
        advance(1810, 9.5, rh)
        lines.append("snapshot")
        expected.append(observer.telemetry(now - 5))
    if known:
        for rh in (30, 50):
            for _ in range(3):
                advance(905)
                advance(3605, 9.5, rh)
            lines.append("snapshot")
            expected.append(observer.telemetry(now - 5))
        assert [metric["alert"]["decision"] for metric in expected[-2:]] == ["warn", "clear"]
    observer.observe(inputs(now, 9.5, known=known), now + 0.002, now, synced=True)
    lines.extend([f"tick {now} 9.5 20 30 20 50 200 101325 {now + 0.002}", "snapshot"])
    expected.append(observer.telemetry(now))
    now += 5
    lines.append("restart")
    state = observer.export_state()
    observer = engine()
    observer.restore(state)
    advance(5, 9.5, 50)
    lines.append("snapshot")
    expected.append(observer.telemetry(now - 5))
    actual = native(lines)
    for cpp, py in zip(actual, expected):
        assert cpp["qualification"]["reason"] == py["qualification"]["reason"]
        assert cpp["alert"]["decision"] == py["alert"]["decision"]
        assert cpp["alert"]["episode"] == py["alert"]["episode"]
        if py["alert"]["evidence"] is not None:
            for field in ("value", "lower", "upper", "seconds"):
                assert cpp["alert"]["evidence"][field] == pytest.approx(py["alert"]["evidence"][field], abs=1e-8)
        for metric in ("delivery", "canister"):
            assert cpp[metric]["status"] == py[metric]["status"]
            if py[metric]["value"] is None: assert cpp[metric]["value"] is None
            else: assert cpp[metric]["value"] == pytest.approx(py[metric]["value"], rel=1e-9, abs=1e-8)


def test_native_command_identity_rejects_embedded_nul_without_truncating(native):
    assert native(["request abc\0one", "request abc", "request", "request " + "a" * 129]) == [False, True, False, False]


def test_native_clock_loss_and_outside_envelope_measurement_status(native):
    lines = ["known", "action baseline_start"]
    for period in range(4):
        start = 1000 + period * 1520
        lines.extend(f"tick {now} 0 20 30 20 30 200 101325" for now in range(start, start + 1506, 5))
        lines.append(f"tick {start + 1510} 1 20 30 20 30 200 101325")
    lines.extend(["action baseline_accept 1", "measure 20.1 30 20 50 200 101325",
                  "unsynced 7100 9.5 20 30 20 50 200 101325", "snapshot"])
    outside, clock_lost = native(lines)
    assert outside["measurement"]["status"] == "provisional"
    assert outside["measurement"]["reason"] == "OUTSIDE_BASELINE_ENVELOPE"
    assert clock_lost["measurement"]["corrected_kg_h"] is None
    assert clock_lost["measurement"]["status"] == "unavailable"


def test_native_detects_utc_step_even_when_both_offsets_are_within_anchor_tolerance(native):
    result = native(["tick 1000 9.5 20 30 20 50 200 101325 1000",
                     "tick 1001 9.5 20 30 20 50 200 101325 1005",
                     "tick 1014 9.5 20 30 20 50 200 101325 1010", "snapshot"])[0]
    assert result["clock_valid"] is False
    assert result["measurement"]["corrected_kg_h"] is None


def test_native_connection_history_break_restarts_active_warmup(native):
    lines = [f"tick {now} 9.5 20 30 20 50 200 101325" for now in range(1000, 2301, 5)]
    lines += ["snapshot", "break", "tick 2305 9.5 20 30 20 50 200 101325", "snapshot"]
    before, after = native(lines)
    assert before["qualification"]["reason"] == "QUALIFIED"
    assert after["qualification"]["reason"] == "WARMUP"
