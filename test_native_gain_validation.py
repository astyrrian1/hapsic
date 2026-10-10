"""Next native-only slice: connected gains require finite nonnegative samples.

An absent optional Number reference keeps documented defaults. A configured
reference with a missing/invalid value inhibits. Zero coefficients remain valid.
Only public Number setters and natural firmware ticks are used.
"""

import math

import pytest

import test_input_domains

native_domain_runner = test_input_domains.native_domain_runner
native_setup = test_input_domains.native_setup
native_ticks = test_input_domains.native_ticks


GAINS = ["kp_a", "ki_a", "kp_b", "ki_b"]
INVALID = ["missing", "nan", "inf", "-inf", "-1"]


@pytest.mark.parametrize("gain", GAINS)
@pytest.mark.parametrize("value", INVALID)
def test_configured_invalid_gain_never_enables_output(native_domain_runner, gain, value):
    snapshots = native_domain_runner(native_setup() + f"number {gain} {value}\n" + native_ticks())
    assert all(s["steam_level"] == 0 for s in snapshots), snapshots


@pytest.mark.parametrize("gain", GAINS)
@pytest.mark.parametrize("value", INVALID)
def test_invalid_gain_stops_existing_output_on_same_tick(native_domain_runner, gain, value):
    initial = {"kp_a": 2.0, "ki_a": 0.1, "kp_b": 0.1, "ki_b": 0.02}[gain]
    snapshots = native_domain_runner(native_setup() + f"number {gain} {initial}\n" + native_ticks()
        + f"number {gain} {value}\ntick 125500\nsnapshot\n")
    assert snapshots[-2]["state"] == "ACTIVE_CRUISE"
    assert snapshots[-2]["steam_level"] > 0
    assert snapshots[-1]["steam_level"] == 0, snapshots[-1]


def test_unconfigured_optional_gains_use_defaults(native_domain_runner):
    snapshots = native_domain_runner(native_setup() + native_ticks())
    assert snapshots[-1]["state"] == "ACTIVE_CRUISE"
    assert snapshots[-1]["steam_level"] > 0


@pytest.mark.parametrize("gains", [[gain] for gain in GAINS] + [GAINS])
def test_zero_configured_coefficients_are_valid(native_domain_runner, gains):
    configuration = "".join(f"number {gain} 0\n" for gain in gains)
    snapshots = native_domain_runner(native_setup() + configuration + native_ticks())
    assert snapshots[-1]["state"] == "ACTIVE_CRUISE"
    assert any(s["steam_level"] > 0 for s in snapshots)
    assert all(math.isfinite(s["steam_level"]) for s in snapshots)
