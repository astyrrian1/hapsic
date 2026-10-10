"""Smoke tests for the actual-source firmware adapter (no live ESPHome services)."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_runner(tmp_path_factory):
    build = tmp_path_factory.mktemp("native-firmware")
    adapter = ROOT / "tests/native/adapter.cpp"
    assert adapter.is_file(), "actual-source native adapter is not implemented"
    binary = build / "hapsic-native"
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-g",
            "-O1",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-I",
            str(ROOT / "tests/native/include"),
            "-I",
            str(ROOT),
            str(adapter),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    def run(commands):
        result = subprocess.run([str(binary)], input=commands, text=True, capture_output=True, check=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    return run


def test_real_magnus_known_point(native_runner):
    value = native_runner("magnus 20 50 101.325\n")[0]
    assert value["valid"] is True
    assert value["dew_point_c"] == pytest.approx(9.26, abs=0.02)
    assert value["mixing_ratio_g_kg"] == pytest.approx(7.25, abs=0.03)


def test_setup_commands_both_outputs_off(native_runner):
    value = native_runner("setup\nsnapshot\n")[0]
    assert value["steam_level"] == value["fan_level"] == 0
    assert value["steam_writes"] >= 1
    assert value["fan_writes"] >= 1


def test_public_ticks_share_injected_clock_and_repeat_exactly(native_runner):
    commands = "clock 10000\nsetup\ntick 15500\nsnapshot\ntick 22500\nsnapshot\n"
    first = native_runner(commands)
    assert first == native_runner(commands)
    assert [item["now_ms"] for item in first] == [15500, 22500]
    assert [item["tick"] for item in first] == [1, 2]
    assert [item["dt"] for item in first] == [5, 7]


def test_wrong_snapshot_expectation_really_fails(native_runner):
    value = native_runner("setup\nsnapshot\n")[0]
    with pytest.raises(AssertionError):
        assert value["steam_level"] == 1.0


def test_streamed_reports_reach_real_sensor_processing(native_runner):
    value = native_runner(
        "setup\nsensor supply_flow 200\nsensor house_temp 20\nsensor house_rh 50\n"
        "sensor supply_temp 20\nsensor supply_rh 50\n"
        "sensor duct_temp 20\nsensor duct_rh 50\ntick 5500\nsnapshot\n"
    )[-1]
    assert value["duct_temp_c"] == pytest.approx(20)
    assert value["duct_rh"] == pytest.approx(50)
