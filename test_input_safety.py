"""Actual-source input safety regressions for both shipped controllers."""
import importlib.util
import math
from pathlib import Path

import pytest

from safety_test_support import ControllerRig

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hapsic_native_tests', ROOT / 'tests/test_native_adapter.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
native_runner = module.native_runner

ENTITIES = {
    'supply_flow': 'sensor.hapsic_supply_flow',
    'extract_flow': 'sensor.hapsic_extract_flow',
    'bypass': 'sensor.zehnder_comfoair_q_a4cb9c_bypass_state',
    'max_capacity': 'input_number.humidifier_max_capacity',
    'target_dp': 'input_number.target_dew_point',
}
NATIVE_DEFAULTS = dict(supply_flow=400, extract_flow=400, bypass=0,
    house_temp=20, house_rh=30, extract_temp=20, extract_rh=30,
    supply_temp=20, supply_rh=30, duct_temp=20, duct_rh=35,
    outdoor_temp=15, outdoor_rh=50, target_dp=10, max_capacity=1.2246984)


def native_setup(**overrides):
    reports = {**NATIVE_DEFAULTS, **overrides}
    return 'setup\n' + ''.join(f'sensor {key} {value}\n' for key, value in reports.items())


def native_ticks(start=5500, stop=60500):
    return ''.join(f'tick {now}\nsnapshot\n' for now in range(start, stop, 5000))


def python_tick(rig):
    """Inject an independent device report of the last service request."""
    rig.clock.advance(5)
    requests = [c for c in rig.commands if c['service'].startswith('light/')]
    last = requests[-1]
    if last.get('callback'):
        last['callback']({'success': True})
    rig.report(rig.controller.SHELLY_LIGHT_ENTITY,
               'on' if last['service'] == 'light/turn_on' else 'off',
               brightness=last.get('brightness', 0))
    rig.controller.master_tick({})


def test_python_baseline_reaches_active_through_real_ticks(monkeypatch):
    rig = ControllerRig(monkeypatch)
    for _ in range(2):
        python_tick(rig)
    assert rig.controller.fsm_state == 'ACTIVE_CRUISE'
    assert rig.controller.steam_voltage > 0


@pytest.mark.parametrize('field', list(ENTITIES))
@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', 'malformed'])
def test_python_invalid_first_sample_never_requests_on(monkeypatch, field, value):
    rig = ControllerRig(monkeypatch, initialize=False)
    rig.report(ENTITIES[field], value)
    rig.controller.initialize()
    for _ in range(24):
        python_tick(rig)
    assert not [c for c in rig.commands if c['service'] == 'light/turn_on']
    assert math.isfinite(rig.controller.steam_voltage)


@pytest.mark.parametrize('value', [0, -1])
def test_python_nonpositive_nameplate_never_requests_on(monkeypatch, value):
    rig = ControllerRig(monkeypatch, initialize=False)
    rig.report(ENTITIES['max_capacity'], value)
    rig.controller.initialize()
    for _ in range(24):
        python_tick(rig)
    assert not [c for c in rig.commands if c['service'] == 'light/turn_on']


@pytest.mark.parametrize('field', list(ENTITIES))
@pytest.mark.parametrize('value', ['nan', 'inf', '-inf'])
def test_native_invalid_first_sample_never_enables_output(native_runner, field, value):
    snapshots = native_runner(native_setup(**{field: value}) + native_ticks(stop=125500))
    assert all(s['steam_level'] == 0 for s in snapshots), snapshots


@pytest.mark.parametrize('value', [0, -1])
def test_native_nonpositive_nameplate_never_enables_output(native_runner, value):
    snapshots = native_runner(native_setup(max_capacity=value) + native_ticks(stop=125500))
    assert all(s['steam_level'] == 0 for s in snapshots), snapshots


@pytest.mark.parametrize('field,value', [('supply_flow', 0), ('bypass', 100)])
def test_python_fresh_hazard_wins_over_expired_extract(monkeypatch, field, value):
    rig = ControllerRig(monkeypatch)
    # Keep demand off while extract ages, then naturally activate inside the
    # last-valid grace window. No device-completion capability is invented.
    rig.report(ENTITIES['target_dp'], -5)
    python_tick(rig)
    rig.report(ENTITIES['extract_flow'], 'unavailable')
    for _ in range(10):
        python_tick(rig)
    rig.report(ENTITIES['target_dp'], 50)
    python_tick(rig)
    assert rig.controller.steam_voltage > 0
    rig.report(ENTITIES[field], value)
    rig.commands.clear()
    # Preserve a genuine actuator report while isolating this tick's requests.
    rig.clock.advance(5)
    rig.report(rig.controller.SHELLY_LIGHT_ENTITY, 'on', brightness=rig.controller.last_brightness)
    rig.controller.master_tick({})
    assert rig.controller.steam_voltage == 0
    assert any(c['service'] == 'light/turn_off' for c in rig.commands)
    assert not any(c['service'] == 'light/turn_on' for c in rig.commands)


@pytest.mark.parametrize('field,value', [('supply_flow', 0), ('bypass', 100)])
def test_native_fresh_hazard_wins_over_expired_extract(native_runner, field, value):
    snapshots = native_runner(native_setup() + native_ticks()
        + 'sensor extract_flow nan\n' + native_ticks(start=60500, stop=120500)
        + f'sensor {field} {value}\ntick 120500\nsnapshot\n')
    assert snapshots[11]['steam_level'] > 0
    assert snapshots[-1]['steam_level'] == 0


def test_python_legitimate_negative_temperature_and_target(monkeypatch):
    rig = ControllerRig(monkeypatch)
    rig.report('sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature', -4)
    rig.report(ENTITIES['target_dp'], -5)
    assert rig.controller.read_and_validate_sensors()
    assert rig.controller.target_room_dp == -5
    assert rig.controller.sensor_value_cache['sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature']['value'] == -4


def test_native_legitimate_negative_temperatures_can_support_demand(native_runner):
    snapshots = native_runner(native_setup(house_temp=-10, extract_temp=-10,
        supply_temp=-10, outdoor_temp=-20, target_dp=-5) + native_ticks())
    assert snapshots[-1]['state'] == 'ACTIVE_CRUISE'
    assert snapshots[-1]['steam_level'] > 0


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', 'malformed'])
def test_python_parser_rejects_invalid_numbers(monkeypatch, value):
    rig = ControllerRig(monkeypatch)
    assert rig.controller.parse_sensor_float(value) is None


def test_native_negative_target_can_remove_demand(native_runner):
    snapshots = native_runner(native_setup(house_temp=-10, extract_temp=-10,
        supply_temp=-10, outdoor_temp=-20, target_dp=-5) + native_ticks()
        + 'sensor target_dp -30\ntick 60500\nsnapshot\n')
    assert snapshots[-2]['steam_level'] > 0
    assert snapshots[-1]['steam_level'] == 0


@pytest.mark.parametrize('field', ['max_capacity', 'target_dp'])
@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', 'malformed', True, False])
def test_python_invalid_configuration_stops_existing_demand(monkeypatch, field, value):
    rig = ControllerRig(monkeypatch)
    python_tick(rig)
    python_tick(rig)
    assert rig.controller.steam_voltage > 0
    rig.report(ENTITIES[field], value)
    python_tick(rig)
    assert rig.controller.steam_voltage == 0
    assert [c for c in rig.commands if c['service'].startswith('light/')][-1]['service'] == 'light/turn_off'


@pytest.mark.parametrize('field', ['max_capacity', 'target_dp'])
@pytest.mark.parametrize('value', ['nan', 'inf', '-inf'])
def test_native_invalid_configuration_stops_existing_demand(native_runner, field, value):
    snapshots = native_runner(native_setup() + native_ticks()
        + f'sensor {field} {value}\ntick 60500\nsnapshot\n')
    assert snapshots[-2]['steam_level'] > 0
    assert snapshots[-1]['steam_level'] == 0


@pytest.mark.parametrize('field', ['supply_flow', 'extract_flow', 'bypass'])
def test_python_negative_critical_input_inhibits(monkeypatch, field):
    rig = ControllerRig(monkeypatch)
    rig.report(ENTITIES[field], -1)
    python_tick(rig)
    assert rig.controller.steam_voltage == 0
    assert not any(c['service'] == 'light/turn_on' for c in rig.commands)


@pytest.mark.parametrize('field', ['supply_flow', 'extract_flow', 'bypass'])
def test_native_negative_critical_input_inhibits(native_runner, field):
    snapshots = native_runner(native_setup(**{field: -1}) + native_ticks())
    assert all(s['steam_level'] == 0 for s in snapshots)


def test_native_finite_deep_negative_target_removes_demand_without_fault(native_runner):
    snapshots = native_runner(native_setup() + native_ticks()
        + 'sensor target_dp -50\ntick 60500\nsnapshot\n')
    assert snapshots[-2]['steam_level'] > 0
    assert snapshots[-1]['steam_level'] == 0
    assert snapshots[-1]['state'] == 'STANDBY'


@pytest.mark.parametrize('value', [True, False])
def test_python_parser_rejects_boolean_sensor_values(monkeypatch, value):
    assert ControllerRig(monkeypatch).controller.parse_sensor_float(value) is None
