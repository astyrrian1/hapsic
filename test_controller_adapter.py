"""Exercise the shipped controller using deterministic boundary doubles."""

import pytest

from safety_test_support import ControllerRig, assert_snapshots_match


def test_clock_and_boundary_calls_are_deterministic(monkeypatch):
    first = ControllerRig(monkeypatch)
    assert any(command["service"] == "light/turn_off" for command in first.commands)
    first.advance(5)
    assert first.controller.dt == 5
    snapshot = first.snapshot()
    second = ControllerRig(monkeypatch)
    second.advance(5)
    assert_snapshots_match(second.snapshot(), snapshot)
    assert first.controller is not second.controller


def test_reports_keep_their_original_identity(monkeypatch):
    rig = ControllerRig(monkeypatch)
    entity = rig.controller.SHELLY_LIGHT_ENTITY
    original = rig.get_state(entity, attribute="all")
    rig.clock.advance(30)
    assert rig.get_state(entity, attribute="all") == original
    rig.report(entity, "off")
    assert rig.get_state(entity, attribute="all")["last_reported"] != original["last_reported"]


def test_interleaved_rigs_have_independent_clocks(monkeypatch):
    first = ControllerRig(monkeypatch)
    second = ControllerRig(monkeypatch)
    first.advance(7)
    second.advance(3)
    assert first.controller.dt == 7
    assert second.controller.dt == 3
    first.advance(2)
    assert first.controller.dt == 2


@pytest.mark.parametrize("actual", [{"output": 999.0}, {"output": float("nan")}, {}, {"output": 0, "extra": 1}])
def test_snapshot_comparison_rejects_divergence(actual):
    with pytest.raises(AssertionError):
        assert_snapshots_match(actual, {"output": 0.0})
