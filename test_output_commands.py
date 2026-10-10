"""Command acknowledgement is separate from a fresh actuator report."""

import hapsic_controller
import pytest

from safety_test_support import Clock


def tracker():
    clock = Clock()
    return clock, hapsic_controller.OutputCommandTracker(clock.monotonic)


def test_service_acceptance_alone_does_not_confirm_output():
    clock, output = tracker()
    command = output.sent(150)
    output.acknowledge(command, True)
    assert output.reported_brightness is None
    assert output.pending_on
    clock.advance(15)
    output.poll()
    assert output.uncertain


def test_retries_and_target_changes_do_not_extend_deadline():
    clock, output = tracker()
    output.sent(150)
    clock.advance(5)
    output.sent(200)
    clock.advance(5)
    output.sent(200)
    clock.advance(5)
    output.poll()
    assert output.uncertain
    assert output.oldest_on_at == 1000
    with pytest.raises(ValueError):
        output.sent(150)


def test_matching_report_cannot_resolve_uncorrelated_accepted_commands():
    clock, output = tracker()
    first = output.sent(150)
    clock.advance(5)
    second = output.sent(200)
    output.acknowledge(second, True)
    clock.advance(1)
    output.observe(200, "report-1", clock.monotonic())
    assert output.pending_on
    output.acknowledge(first, True)
    clock.advance(1)
    output.observe(200, "report-2", clock.monotonic())
    assert output.pending_on
    assert output.oldest_on_at == 1000
    clock.advance(8)
    output.poll()
    assert output.uncertain


def test_cancellation_cannot_hide_an_unresolved_on():
    clock, output = tracker()
    command = output.sent(150)
    output.sent(0)
    clock.advance(1)
    output.observe(0, "off-1", clock.monotonic())
    assert output.off_since is None
    clock.advance(15)
    output.poll()
    assert not output.rearm(healthy_seconds=60)
    output.acknowledge(command, True)
    clock.advance(1)
    output.sent(0)
    clock.advance(1)
    output.observe(0, "off-2", clock.monotonic())
    assert output.pending_on
    assert output.off_since is None
    assert output.uncertain
    assert not output.rearm(healthy_seconds=60)


def test_stale_off_and_late_on_cannot_qualify_restart():
    clock, output = tracker()
    output.sent(0)
    output.observe(0, "old", clock.monotonic() - 1)
    assert output.off_since is None
    clock.advance(1)
    output.observe(0, "fresh", clock.monotonic())
    assert output.off_since is not None
    clock.advance(1)
    output.observe(150, "late-on", clock.monotonic())
    assert output.off_since is None
    assert output.uncertain


def test_replayed_report_does_not_renew_freshness():
    clock, output = tracker()
    output.sent(0)
    clock.advance(1)
    timestamp = clock.monotonic()
    output.observe(0, "off", timestamp)
    clock.advance(61)
    output.observe(0, "off", timestamp)
    output.poll()
    assert output.off_since is None


@pytest.mark.parametrize("brightness", [-1, 256, float("nan"), float("inf")])
def test_invalid_report_cannot_confirm_output(brightness):
    clock, output = tracker()
    output.sent(0)
    clock.advance(1)
    output.observe(brightness, "invalid", clock.monotonic())
    assert output.reported_brightness is None
    assert output.off_since is None


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1, "60", None, True])
def test_rearm_rejects_invalid_healthy_duration(duration):
    clock, output = tracker()
    output.sent(0)
    clock.advance(1)
    output.observe(150, "unexpected-on", clock.monotonic())
    clock.advance(1)
    output.observe(0, "fresh-off", clock.monotonic())
    assert output.uncertain
    assert not output.rearm(healthy_seconds=duration)
    assert output.uncertain


def test_device_completion_and_later_matching_report_resolve_delivery():
    clock, output = tracker()
    command = output.sent(150)
    output.acknowledge(command, True)
    clock.advance(1)
    output.device_completed(command)
    assert output.pending_on
    clock.advance(1)
    output.observe(150, "device-report", clock.monotonic())
    assert not output.pending_on
    assert output.oldest_on_at is None
    assert output.off_since is None


def test_completed_newer_command_does_not_resolve_older_on():
    clock, output = tracker()
    first = output.sent(150)
    clock.advance(1)
    second = output.sent(200)
    output.device_completed(second)
    clock.advance(1)
    output.observe(200, "newer-completed", clock.monotonic())
    assert first in output.pending_on
    clock.advance(13)
    output.poll()
    assert output.uncertain


def test_late_completion_requires_new_off_report_and_manual_rearm():
    clock, output = tracker()
    command = output.sent(150)
    output.sent(0)
    clock.advance(15)
    output.observe(0, "off-before-completion", clock.monotonic())
    output.device_completed(command)
    assert output.uncertain
    assert output.off_since is None
    assert not output.rearm(healthy_seconds=60)
    clock.advance(1)
    output.observe(0, "off-after-completion", clock.monotonic())
    assert not output.pending_on
    assert output.uncertain
    assert output.rearm(healthy_seconds=60)


def test_mismatched_report_after_device_completion_keeps_original_deadline():
    clock, output = tracker()
    command = output.sent(150)
    output.device_completed(command)
    clock.advance(1)
    output.observe(100, "mismatch", clock.monotonic())
    clock.advance(14)
    output.poll()
    assert output.uncertain
    assert output.pending_on


def test_unknown_device_completion_cannot_resolve_pending_command():
    clock, output = tracker()
    command = output.sent(150)
    output.device_completed(command + 1)
    clock.advance(1)
    output.observe(150, "uncorrelated", clock.monotonic())
    assert output.pending_on
