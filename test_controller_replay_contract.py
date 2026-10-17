"""The replay boundary must preserve units and distinguish constructed data."""

import json

import pytest

from controller_replay import FIELDS, native_line, validate_continuity, validate_units
from test_offline_parity import fixture


def test_replay_validates_current_and_historical_units(tmp_path):
    fixture(tmp_path)
    history = json.loads((tmp_path / "history.json").read_text())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    validate_units(history, manifest)
    assert manifest["recording_type"] == "synthetic"
    history[0][0]["attributes"]["unit_of_measurement"] = "°C"
    with pytest.raises(ValueError, match="Historical unit change"):
        validate_units(history, manifest)
    manifest["states"][FIELDS[2]]["attributes"]["unit_of_measurement"] = "cfm"
    with pytest.raises(ValueError, match="Unsupported input unit"):
        validate_units([], manifest)


def test_replay_converts_flow_and_capacity_once():
    state = dict.fromkeys(FIELDS, 1)
    state[FIELDS[0]] = 68
    state[FIELDS[2]] = 200
    state[FIELDS[11]] = 4.61
    row = native_line("F", 1000, state).split()
    assert float(row[2]) == 20
    assert float(row[4]) == 200
    assert float(row[13]) == pytest.approx(4.61 * 0.453592)


def test_recorded_history_gap_is_not_assumed_fresh():
    history = [
        [
            dict(entity_id=FIELDS[0], last_changed="2026-01-01T00:00:00+00:00"),
            dict(entity_id=FIELDS[0], last_changed="2026-01-01T01:00:00+00:00"),
        ]
    ]
    with pytest.raises(ValueError, match="continuity"):
        validate_continuity(history, {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T01:00:00+00:00"})
    validate_continuity(history, {"recording_type": "synthetic"})
    with pytest.raises(ValueError, match="continuity"):
        validate_continuity([], {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T00:01:00+00:00"})


@pytest.mark.parametrize("start,end", [("00:00:00", "00:10:00"), ("00:10:00", "00:00:00"), ("00:00:00", "00:00:00")])
def test_recording_boundary_gaps_and_inverted_windows_are_rejected(start, end):
    manifest = {key: f"2026-01-01T{value}+00:00" for key, value in (("start", start), ("end", end))}
    history = [[dict(entity_id=FIELDS[0], last_changed=manifest["start"])]]
    with pytest.raises(ValueError, match="continuity|window"):
        validate_continuity(history, manifest)


def test_command_units_are_required(tmp_path):
    fixture(tmp_path)
    history = json.loads((tmp_path / "history.json").read_text())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    key = "sensor.hapsic_steam_voltage"
    manifest["states"][key] = {"attributes": {"unit_of_measurement": "%"}}
    with pytest.raises(ValueError, match="Unsupported input unit"):
        validate_units(history, manifest)
    manifest["states"][key]["attributes"]["unit_of_measurement"] = "V"
    history[-1][0]["attributes"] = {"unit_of_measurement": "%"}
    with pytest.raises(ValueError, match="Historical unit change"):
        validate_units(history, manifest)


def test_replay_reports_fault_and_limiter_transitions():
    from controller_replay import behavior_summary

    rows = [
        {"fsm": {"state": "ACTIVE_CRUISE", "fault_reason": "NONE"}, "limiters": {"active_limit": "NORMAL"}},
        {"fsm": {"state": "ACTIVE_CRUISE", "fault_reason": "NONE"}, "limiters": {"active_limit": "FINAL_SAFETY_CLAMP"}},
        {"fsm": {"state": "FAULT", "fault_reason": "Sensor Failure"},
         "limiters": {"active_limit": "FINAL_SAFETY_CLAMP"}},
    ]
    summary = behavior_summary(rows)
    assert summary["states"] == {"ACTIVE_CRUISE": 2, "FAULT": 1}
    assert summary["faults"] == {"Sensor Failure": 1}
    assert summary["limiters"] == {"NORMAL": 1, "FINAL_SAFETY_CLAMP": 2}
    assert summary["transitions"] == {"ACTIVE_CRUISE -> FAULT": 1}
