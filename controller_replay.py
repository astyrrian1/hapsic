"""Deterministic actual-controller replay. Production inputs are private JSON exports.

The numeric lane supplies equivalent processed state and historical command.
Its shared flags establish arithmetic parity only. The free-running lane retains
actual control logic; outputs are counterfactual after departing from history.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from conftest import _mod as fixed  # Stubs only the AppDaemon runtime boundary.

ROOT = Path(__file__).parent
FIELDS = [
    "sensor.hapsic_cleansed_post_steam_temp",
    "sensor.hapsic_cleansed_post_steam_rh",
    "sensor.hapsic_supply_flow",
    "sensor.hapsic_extract_flow",
    "sensor.hapsic_room_average_temp",
    "sensor.hapsic_room_average_rh",
    "sensor.hapsic_cleansed_supply_temp",
    "sensor.hapsic_cleansed_supply_rh",
    "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature",
    "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity",
    "input_number.target_dew_point",
    "input_number.humidifier_max_capacity",
    "sensor.zehnder_comfoair_q_a4cb9c_bypass_state",
]


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else math.nan
    except (ValueError, TypeError):
        return math.nan


def native_line(mode, now, state, command=0, boil=False, stasis=False, processed=None):
    values = [number(state.get(key)) for key in FIELDS]
    for index in (0, 4, 6, 8, 10):
        values[index] = (values[index] - 32) * 5 / 9
    values[11] *= 0.453592  # HA lb/h -> configured native sensor kg/h, exactly once.
    tail = [] if processed is None else [v / 7 for v in processed[:4]] + [processed[4]]  # grains/lb -> g/kg.
    return " ".join(map(str, [mode, now, *values, command, int(boil), int(stasis), *tail]))


def snapshot(controller):
    return dict(
        voltage=controller.steam_voltage,
        state=controller.fsm_state,
        chi=controller.chi_ema,
        curve=list(controller.boiler_curve),
        counts=list(controller.boiler_curve_counts),
        eligible=getattr(controller, "chi_eligible", False),
        measured=getattr(controller, "_last_measured_steam", 0),
        fault=controller.fault_reason,
    )


def execute_native(binary, lines):
    run = subprocess.run([str(binary), "replay"], input="\n".join(lines), text=True, capture_output=True, check=True)
    rows = [json.loads(line) for line in run.stdout.splitlines()]
    if len(rows) != len(lines):
        raise RuntimeError("Native replay dropped frames")
    return rows


def simulate(history, manifest, old_source, data_dir):
    spec = importlib.util.spec_from_file_location("hapsic_replay_baseline", old_source)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    start, end = stamp(manifest["start"]), stamp(manifest["end"])
    events = []
    for series in history:
        if not series:
            continue
        entity = series[0]["entity_id"]
        for row in series:
            events.append((max(start, stamp(row.get("last_updated", row["last_changed"]))), entity, row["state"]))
    events.sort(key=lambda item: (item[0], item[1]))
    # One batch per timestamp before a tick; no interpolation or invented reports.
    current = {}
    index = 0
    while index < len(events) and events[index][0] <= start:
        _, key, value = events[index]
        current[key] = value
        index += 1
    clock = [1000.0]
    real_clock = fixed.time.time
    fixed.time.time = lambda: clock[0]
    controllers = []
    packets = []

    def create(module, name, forced=False):
        controller = module.HapsicController()
        states = dict(current)
        states["input_text.hapsic_boiler_curve"] = (
            json.dumps(dict(v=2, curve=[0] * 4, counts=[0] * 4)) if module is fixed else "[0,0,0,0]"
        )
        states["input_number.hapsic_chi_ema"] = "1.0"
        controller.args = dict(learning_backup_path=str(data_dir / (name + "-backup.json")))
        controller.get_state = lambda entity, **kwargs: (
            255
            if entity == "input_text.hapsic_boiler_curve" and kwargs.get("attribute") == "max"
            else states.get(entity)
        )
        controller.call_service = lambda name, **kwargs: packets.append(kwargs) if name == "mqtt/publish" else None
        controller.initialize()
        if forced:
            original = controller.write_output

            def apply_historical_command():
                command = number(states.get("sensor.hapsic_steam_voltage"))
                controller.steam_voltage = command if math.isfinite(command) and 0 <= command <= 9.5 else 0
                if not math.isfinite(command):
                    controller.boil_achieved = False
                original()

            controller.write_output = apply_historical_command
        controllers.append((controller, states))
        return controller

    current_control = create(fixed, "fixed")
    old_control = create(old, "baseline")
    recorded = create(fixed, "recorded", True)
    full_lines, math_lines, expected, observations, states_seen = [], [], [], [], set()
    try:
        for offset in range(0, int(end - start) + 1, 5):
            utc = start + offset
            while index < len(events) and events[index][0] <= utc:
                _, key, value = events[index]
                current[key] = value
                index += 1
            clock[0] = 1000 + offset
            for control, states in controllers:
                states.update(
                    {
                        k: v
                        for k, v in current.items()
                        if k not in ("input_text.hapsic_boiler_curve", "input_number.hapsic_chi_ema")
                    }
                )
                control.master_tick({})
                assert math.isfinite(control.steam_voltage) and 0 <= control.steam_voltage <= 9.5
                if control.fsm_state == "FAULT":
                    assert control.steam_voltage == 0
            states_seen.add(current_control.fsm_state)
            full_lines.append(native_line("F", clock[0], current))
            observed = number(current.get("sensor.hapsic_steam_voltage"))
            observations.append(
                dict(fixed=snapshot(current_control), baseline=snapshot(old_control), historical=observed)
            )
            if recorded.chi_eligible and math.isfinite(observed) and 0 <= observed <= 9.5:
                processed = (
                    recorded.supply_w,
                    recorded.duct_w,
                    recorded.room_w,
                    recorded.outdoor_w,
                    recorded.supply_flow,
                )
                math_lines.append(
                    native_line(
                        "M",
                        clock[0],
                        current,
                        recorded.steam_voltage,
                        recorded.boil_achieved,
                        recorded.stasis_active,
                        processed,
                    )
                )
                expected.append(
                    dict(
                        measured=recorded._last_measured_steam,
                        ratio=recorded.chi_instant,
                        counts=list(recorded.boiler_curve_counts),
                        curve=list(recorded.boiler_curve),
                        chi_ema=recorded.chi_ema,
                        capacity=recorded.get_effective_max_capacity(),
                        loss=recorded.calc_loss_vent,
                        flux=recorded.calc_flux,
                    )
                )
    finally:
        fixed.time.time = real_clock
    return full_lines, math_lines, expected, observations, sorted(states_seen)


def behavior_summary(rows):
    states, faults, limiters, transitions = Counter(), Counter(), Counter(), Counter()
    previous = None
    for row in rows:
        fsm = row.get("fsm", row)
        state = fsm["state"]
        states[state] += 1
        if previous is not None and previous != state:
            transitions[f"{previous} -> {state}"] += 1
        previous = state
        if state == "FAULT":
            faults[fsm.get("fault_reason", fsm.get("fault", "UNKNOWN"))] += 1
        if "limiters" in row:
            limiters[row["limiters"]["active_limit"]] += 1
    return dict(states=dict(states), faults=dict(faults), limiters=dict(limiters), transitions=dict(transitions))


def validate_continuity(history, manifest):
    if manifest.get("recording_type") == "synthetic":
        return  # Generated holds are known; never inferred for production recordings.
    start, end = stamp(manifest["start"]), stamp(manifest["end"])
    if end <= start:
        raise ValueError("Recording window must have positive duration")
    times = sorted(
        {
            stamp(row.get("last_updated", row["last_changed"]))
            for series in history
            if series and series[0]["entity_id"] in FIELDS
            for row in series
        }
    )
    times = [value for value in times if start <= value <= end]
    if not times:
        raise ValueError("Recording continuity unknown: no observations in window")
    times = [start, *times, end]
    if any(right - left > 300 for left, right in zip(times, times[1:])):
        raise ValueError("Recording continuity unknown: split intervals at source-event gaps over 300 seconds")


def validate_units(history, manifest):
    contracts = {
        key: ("°F" if index in (0, 4, 6, 8, 10) else "lbs/hr" if index == 11 else "m³/h" if index in (2, 3) else "%")
        for index, key in enumerate(FIELDS)
    }
    contracts["sensor.hapsic_steam_voltage"] = "V"
    for key, unit in contracts.items():
        actual = manifest["states"].get(key, {}).get("attributes", {}).get("unit_of_measurement")
        if actual != unit:
            raise ValueError(f"Unsupported input unit for {key}: {actual!r}; expected {unit}")
    for series in history:
        if not series:
            continue
        key = series[0]["entity_id"]
        if key not in contracts:
            continue
        for row in series:
            unit = row.get("attributes", {}).get("unit_of_measurement")
            if unit is not None and unit != contracts[key]:
                raise ValueError(f"Historical unit change for {key}: {unit}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--baseline-binary", type=Path, required=True)
    parser.add_argument("--baseline", default="322e9ed")
    args = parser.parse_args()
    raw = (args.input / "history.json").read_bytes()
    manifest = json.loads((args.input / "manifest.json").read_text())
    assert hashlib.sha256(raw).hexdigest() == manifest["sha256"]
    validate_units(json.loads(raw), manifest)
    validate_continuity(json.loads(raw), manifest)
    with tempfile.TemporaryDirectory(prefix="hapsic-controller-replay-") as folder:
        source = Path(folder) / "baseline.py"
        source.write_bytes(
            subprocess.check_output(["git", "show", args.baseline + ":apps/hapsic-controller/hapsic_controller.py"])
        )
        lines, maths, expected, observed, states = simulate(json.loads(raw), manifest, source, Path(folder))
    if not maths:
        raise SystemExit("Replay coverage incomplete: no eligible historical-command interval")
    native = execute_native(args.binary, lines)
    baseline = execute_native(args.baseline_binary, lines)
    numeric = execute_native(args.binary, maths)
    maximum_error = 0
    for got, wanted in zip(numeric, expected):
        error = abs(got["health"]["measured_steam_lbs_hr"] - wanted["measured"])
        maximum_error = max(maximum_error, error)
        assert error <= max(1e-5, abs(wanted["measured"]) * 1e-5), (got["health"], wanted)
        assert abs(got["health"]["chi_ratio"] - wanted["ratio"]) <= 1e-5
        assert got["health"]["boiler_curve_samples"] == wanted["counts"]
        for actual, expected_value in zip(got["health"]["boiler_curve"], wanted["curve"]):
            assert abs(actual - expected_value) <= max(0.0005 + 1e-5, abs(expected_value) * 1e-3)
        assert abs(got["health"]["chi_ema"] - wanted["chi_ema"]) <= 1e-3
        assert abs(got["health"]["effective_max_capacity"] - wanted["capacity"]) <= max(
            0.0005 + 1e-5, wanted["capacity"] * 1e-3
        )
        for field, key in (("loss_vent", "loss"), ("flux_net", "flux")):
            assert abs(got["physics"][field] - wanted[key]) <= max(1e-5, abs(wanted[key]) * 1e-5)

    changes = [abs(a["io"]["volts_out"] - b["io"]["volts_out"]) for a, b in zip(native, baseline)]
    violations = [
        i
        for i, row in enumerate(native)
        if not 0 <= row["io"]["volts_out"] <= 9.5 or (row["fsm"]["state"] == "FAULT" and row["io"]["volts_out"] != 0)
    ]
    baseline_violations = [
        i
        for i, row in enumerate(baseline)
        if not 0 <= row["io"]["volts_out"] <= 9.5 or (row["fsm"]["state"] == "FAULT" and row["io"]["volts_out"] != 0)
    ]
    report = dict(
        recording_type=manifest.get("recording_type", "recorded"),
        source_sha256=manifest["sha256"],
        baseline=args.baseline,
        candidate_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        candidate_dirty=bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        candidate_source_sha256={
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in ("apps/hapsic-controller/hapsic_controller.py", "components/hapsic/hapsic.cpp",
                         "components/hapsic/hapsic.h", "controller_replay.py", "tests/legacy_replay_native.cpp")
        },
        native_binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        baseline_native_binary_sha256=hashlib.sha256(args.baseline_binary.read_bytes()).hexdigest(),
        recorded_producer_revision=manifest.get("producer_revision", "not recorded; no exact incident replay claimed"),
        ha_version=manifest.get("ha_version", "synthetic"),
        initial_state="constructed fresh/migrated boot; filters and integrators reset",
        input_provenance=manifest["native_substitution"],
        processing=manifest["processing"],
        frames=len(lines),
        native_safety_violation_ticks=len(violations),
        baseline_native_safety_violation_ticks=len(baseline_violations),
        maximum_native_voltage=max(row["io"]["volts_out"] for row in native),
        maximum_baseline_native_voltage=max(row["io"]["volts_out"] for row in baseline),
        eligible_recorded_command_frames=len(maths),
        eligible_command_seconds=len(maths) * 5,
        maximum_mass_error_lb_h=maximum_error,
        native_command_change_max_v=max(changes),
        native_command_change_mean_v=statistics.mean(changes),
        native_command_changed_ticks=sum(x > 0.01 for x in changes),
        python_command_changed_ticks=sum(
            abs(x["fixed"]["voltage"] - x["baseline"]["voltage"]) > 0.01 for x in observed
        ),
        python_states=states,
        native_states=sorted({row["fsm"]["state"] for row in native}),
        behavior={
            "native_repaired": behavior_summary(native),
            "native_baseline": behavior_summary(baseline),
            "python_repaired": behavior_summary([row["fixed"] for row in observed]),
            "python_baseline": behavior_summary([row["baseline"] for row in observed]),
        },
        first_native_departure_from_recorded_tick=next(
            (
                i
                for i, (r, o) in enumerate(zip(native, observed))
                if math.isfinite(o["historical"]) and abs(r["io"]["volts_out"] - o["historical"]) > 0.01
            ),
            None,
        ),
        limitations=[
            "Shared reconstructed eligibility proves arithmetic only; independent gate tests are separate.",
            "Recorder history has no independent report heartbeat; unchanged values are held as HA control inputs.",
            "Native GPIO/CAN inputs are HA substitutes; native physical input coverage is not established.",
            "After command departure, learning is counterfactual; no prediction of house humidity response.",
            "Input units and processing stage are recorded; no raw-native RH safety validation is inferred.",
        ],
    )
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if violations:
        raise SystemExit("Native safety invariant failed; see report. Candidate is not validated.")


if __name__ == "__main__":
    main()
