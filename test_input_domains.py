"""Actual-source input-domain regressions. Boundary injection only: no forced controller states."""

import math

import pytest

import test_input_safety
from safety_test_support import ControllerRig

native_domain_runner = test_input_safety.native_runner


DEFAULTS = dict(supply_flow=400, extract_flow=400, bypass=0,
    house_temp=20, house_rh=30, extract_temp=20, extract_rh=30,
    supply_temp=20, supply_rh=30, duct_temp=20, duct_rh=35,
    outdoor_temp=15, outdoor_rh=50, target_dp=10, max_capacity=1.2246984)
PAIRS = {
    "room": (("sensor.hapsic_room_average_temp", "sensor.hapsic_room_average_rh"),
             ("sensor.hapsic_cleansed_inside_temp", "sensor.hapsic_cleansed_inside_rh")),
    "supply": (("sensor.hapsic_cleansed_supply_temp", "sensor.hapsic_cleansed_supply_rh"),),
    "duct": (("sensor.hapsic_cleansed_post_steam_temp", "sensor.hapsic_cleansed_post_steam_rh"),),
}
NATIVE_PAIRS = {
    "room": (("house_temp", "house_rh"), ("extract_temp", "extract_rh")),
    "supply": (("supply_temp", "supply_rh"),),
    "duct": (("duct_temp", "duct_rh"),),
}


def native_setup(**overrides):
    return "setup\n" + "".join(f"sensor {k} {v}\n" for k, v in {**DEFAULTS, **overrides}.items())


def native_ticks():
    return "".join(f"tick {now}\nsnapshot\n" for now in range(5500, 125500, 5000))


@pytest.mark.parametrize("rh_value", [-1, 101])
@pytest.mark.parametrize("group", list(PAIRS))
def test_python_out_of_domain_first_rh_never_requests_steam(monkeypatch, group, rh_value):
    rig = ControllerRig(monkeypatch, initialize=False)
    for _, rh in PAIRS[group]:
        rig.report(rh, rh_value)
    rig.controller.initialize()
    rig.advance()
    rig.advance()
    assert not any(c["service"] == "light/turn_on" for c in rig.commands)
    assert rig.controller.steam_voltage == 0


@pytest.mark.parametrize("rh_value", [-1, 101])
@pytest.mark.parametrize("group", list(PAIRS))
def test_native_out_of_domain_first_rh_never_enables_steam(native_domain_runner, group, rh_value):
    overrides = {rh: rh_value for _, rh in NATIVE_PAIRS[group]}
    snapshots = native_domain_runner(native_setup(**overrides) + native_ticks())
    assert all(s["steam_level"] == 0 for s in snapshots), snapshots


@pytest.mark.parametrize("group", list(PAIRS))
def test_python_all_critical_sources_missing_at_boot_remain_off(monkeypatch, group):
    rig = ControllerRig(monkeypatch, initialize=False)
    for _, entity in PAIRS[group]:
        rig.report(entity, "unavailable")
    rig.controller.initialize()
    rig.advance()
    rig.advance()
    assert not any(c["service"] == "light/turn_on" for c in rig.commands)


@pytest.mark.parametrize("group", list(PAIRS))
def test_native_all_critical_sources_missing_at_boot_remain_off(native_domain_runner, group):
    overrides = {entity: "nan" for _, entity in NATIVE_PAIRS[group]}
    snapshots = native_domain_runner(native_setup(**overrides) + native_ticks())
    assert all(s["steam_level"] == 0 for s in snapshots)


INVALID_MATH = [
    (20, -1, 88.6), (20, 101, 88.6), (100, 100, 88.6),
    (20, 50, 0), (20, 50, -1), (20, 50, float("nan")), (20, 50, float("inf")),
    (float("nan"), 50, 88.6), (float("inf"), 50, 88.6),
    (20, float("nan"), 88.6), (20, float("inf"), 88.6),
    (-244, 50, 88.6),
]


@pytest.mark.parametrize("temp_c,rh,pressure", INVALID_MATH)
def test_python_invalid_math_returns_none_without_throwing(monkeypatch, temp_c, rh, pressure):
    controller = ControllerRig(monkeypatch).controller
    controller.P_ATM = pressure  # Input to this math API is stored on the instance.
    assert controller.calc_psychrometrics(temp_c * 9 / 5 + 32, rh) == (None, None)


@pytest.mark.parametrize("temp_c,rh,pressure", INVALID_MATH)
def test_native_invalid_math_sets_invalid_without_nonfinite_results(native_domain_runner, temp_c, rh, pressure):
    result = native_domain_runner(f"magnus {temp_c} {rh} {pressure}\n")[0]
    assert result["valid"] is False
    assert math.isfinite(result["dew_point_c"])
    assert math.isfinite(result["mixing_ratio_g_kg"])


@pytest.mark.parametrize("temp_c,rh", [(-10, 50), (20, 0), (20, 100)])
def test_python_valid_negative_temperature_and_rh_endpoints(monkeypatch, temp_c, rh):
    controller = ControllerRig(monkeypatch).controller
    dewpoint, mixing_ratio = controller.calc_psychrometrics(temp_c * 9 / 5 + 32, rh)
    assert math.isfinite(dewpoint)
    assert math.isfinite(mixing_ratio) and mixing_ratio >= 0
    if rh == 0:
        assert mixing_ratio == 0
    if temp_c < 0:
        assert dewpoint < 32


@pytest.mark.parametrize("temp_c,rh", [(-10, 50), (20, 0), (20, 100)])
def test_native_valid_negative_temperature_and_rh_endpoints(native_domain_runner, temp_c, rh):
    result = native_domain_runner(f"magnus {temp_c} {rh} 88.6\n")[0]
    assert result["valid"] is True
    assert math.isfinite(result["dew_point_c"])
    assert math.isfinite(result["mixing_ratio_g_kg"]) and result["mixing_ratio_g_kg"] >= 0
    if rh == 0:
        assert result["mixing_ratio_g_kg"] == 0
    if temp_c < 0:
        assert result["dew_point_c"] < 0


@pytest.mark.parametrize("rh", [88, 101])
def test_fresh_high_duct_rh_stops_python_before_any_cache_fallback(monkeypatch, rh):
    rig = ControllerRig(monkeypatch)
    rig.advance()
    rig.advance()
    assert rig.controller.steam_voltage > 0
    rig.report("sensor.hapsic_cleansed_post_steam_rh", rh)
    rig.commands.clear()
    rig.advance()
    assert rig.controller.steam_voltage == 0
    assert not any(c["service"] == "light/turn_on" for c in rig.commands)


@pytest.mark.parametrize("rh", [88, 101])
def test_fresh_high_duct_rh_stops_native_before_any_cache_fallback(native_domain_runner, rh):
    reports = native_domain_runner(native_setup() + native_ticks()
        + f"sensor duct_rh {rh}\ntick 125500\nsnapshot\n")
    assert reports[-2]["steam_level"] > 0
    assert reports[-1]["steam_level"] == 0


@pytest.mark.parametrize("rh", [-1, 101])
@pytest.mark.parametrize("group", list(PAIRS))
def test_fresh_invalid_rh_stops_active_python_same_tick(monkeypatch, group, rh):
    rig = ControllerRig(monkeypatch)
    rig.advance()
    rig.advance()
    assert rig.controller.steam_voltage > 0
    oldest_on = rig.controller.output_commands.oldest_on_at
    for _, entity in PAIRS[group]:
        rig.report(entity, rh)
    rig.commands.clear()
    rig.advance()
    assert rig.clock.monotonic() - oldest_on < 15
    assert rig.controller.steam_voltage == 0
    assert any(c["service"] == "light/turn_off" for c in rig.commands)
    assert not any(c["service"] == "light/turn_on" for c in rig.commands)


@pytest.mark.parametrize("rh", [-1, 101])
@pytest.mark.parametrize("group", list(PAIRS))
def test_fresh_invalid_rh_stops_active_native_same_tick(native_domain_runner, group, rh):
    updates = "".join(f"sensor {entity} {rh}\n" for _, entity in NATIVE_PAIRS[group])
    reports = native_domain_runner(native_setup() + native_ticks()
        + updates + "tick 125500\nsnapshot\n")
    assert reports[-2]["steam_level"] > 0
    assert reports[-1]["steam_level"] == 0


@pytest.mark.parametrize('group,attributes', [
    ('room', ('room_dp', 'room_w', 'last_valid_room_dp_time')),
    ('supply', ('supply_dp', 'supply_w', 'last_valid_supply_w_time')),
    ('duct', ('duct_dp', 'duct_w', 'last_valid_duct_time')),
    ('outdoor', ('outdoor_dp', 'outdoor_w', 'last_valid_outdoor_time')),
])
def test_invalid_psychrometrics_cannot_replace_last_valid_observation(monkeypatch, group, attributes):
    rig = ControllerRig(monkeypatch)
    assert rig.controller.read_and_validate_sensors()
    previous = [getattr(rig.controller, field) for field in attributes]
    pairs = PAIRS.get(group, ((
        'sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature',
        'sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity'),))
    rig.clock.advance(5)
    for temp, rh in pairs:
        rig.report(temp, 212)
        rig.report(rh, 100)
    assert not rig.controller.read_and_validate_sensors()
    assert [getattr(rig.controller, field) for field in attributes] == previous


def test_invalid_unused_room_fallback_stops_python(monkeypatch):
    rig = ControllerRig(monkeypatch)
    rig.advance()
    assert rig.controller.steam_voltage > 0
    rig.report('sensor.hapsic_cleansed_inside_rh', 101)
    rig.advance()
    assert rig.controller.steam_voltage == 0


def test_invalid_unused_room_fallback_stops_native(native_domain_runner):
    reports = native_domain_runner(native_setup() + native_ticks()
        + 'sensor extract_rh 101\ntick 125500\nsnapshot\n')
    assert reports[-2]['steam_level'] > 0
    assert reports[-1]['steam_level'] == 0


def test_inverse_magnus_roundoff_cannot_divide_by_zero(monkeypatch):
    controller = ControllerRig(monkeypatch).controller
    controller.P_ATM = 1e10
    assert controller.calc_psychrometrics(1e20, 100) == (None, None)


def test_native_inverse_magnus_roundoff_is_invalid(native_domain_runner):
    value = native_domain_runner('magnus 1e20 100 1e10\n')[0]
    assert value['valid'] is False
    assert math.isfinite(value['dew_point_c'])
    assert math.isfinite(value['mixing_ratio_g_kg'])
