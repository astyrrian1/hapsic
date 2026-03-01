"""
HAPSIC Desk Validation Test Harness
====================================
Shared base class for Mode C (Shadow Integrator) and Mode D (Component Parity)
validation tests. Connects to MQTT, collects paired production/desk telemetry
frames, and provides assertion helpers with summary reporting.

Usage:
    Subclass HapsicTestHarness, implement run_tests(), call self.execute().
"""

import sys
import json
import time
import yaml
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

TOPIC_PROD = "hapsic/telemetry/state"
TOPIC_DESK = "hapsic-desk/telemetry/state"


def load_secrets():
    """Load MQTT credentials from secrets.yaml."""
    import os
    secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.yaml")
    with open(secrets_path, "r") as f:
        return yaml.safe_load(f)


def c_to_f(c):
    """Celsius to Fahrenheit."""
    return (c * 9.0 / 5.0) + 32.0


def f_to_c(f):
    """Fahrenheit to Celsius."""
    return (f - 32.0) * 5.0 / 9.0


class HapsicTestHarness:
    """Base class for live MQTT validation tests."""

    def __init__(self, name="HapsicTest"):
        self.name = name
        self.secrets = load_secrets()
        self.broker = self.secrets.get("mqtt_broker", "homeassistant.local")
        self.username = self.secrets.get("mqtt_username", "")
        self.password = self.secrets.get("mqtt_password", "")

        # Collected data
        self.prod_frames = []
        self.desk_frames = []
        self.paired_frames = []  # List of (prod_dict, desk_dict) tuples
        self._last_prod = None
        self._last_desk = None

        # Assertion tracking
        self.pass_count = 0
        self.fail_count = 0
        self.results = []  # List of (label, passed, detail)

    # ------------------------------------------------------------------
    # MQTT collection
    # ------------------------------------------------------------------

    def collect_paired_frames(self, n=10, timeout=120):
        """
        Connect to MQTT broker and collect n paired (prod, desk) frames.
        A pair is formed whenever both topics have delivered at least one
        message since the last pair was recorded.
        """
        self.paired_frames = []
        self._last_prod = None
        self._last_desk = None
        start = time.time()

        def on_connect(client, userdata, flags, rc, properties=None):
            client.subscribe([(TOPIC_PROD, 0), (TOPIC_DESK, 0)])

        def on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except json.JSONDecodeError:
                return

            if msg.topic == TOPIC_PROD:
                self._last_prod = payload
                self.prod_frames.append(payload)
            elif msg.topic == TOPIC_DESK:
                self._last_desk = payload
                self.desk_frames.append(payload)

            if self._last_prod and self._last_desk:
                self.paired_frames.append((self._last_prod, self._last_desk))
                self._last_prod = None
                self._last_desk = None
                if len(self.paired_frames) >= n:
                    client.disconnect()

        client = mqtt.Client(CallbackAPIVersion.VERSION2, f"hapsic_test_{self.name}")
        if self.username:
            client.username_pw_set(self.username, self.password)
        client.on_connect = on_connect
        client.on_message = on_message

        print(f"[{self.name}] Connecting to {self.broker}...")
        client.connect(self.broker, 1883, 60)

        # Loop with timeout
        while len(self.paired_frames) < n and (time.time() - start) < timeout:
            client.loop(timeout=1.0)

        client.disconnect()
        print(f"[{self.name}] Collected {len(self.paired_frames)} paired frames in {time.time() - start:.0f}s")
        return self.paired_frames

    # ------------------------------------------------------------------
    # Assertion helpers
    # ------------------------------------------------------------------

    def assert_parity(self, py_val, cpp_val, tolerance, label):
        """Assert two values are within tolerance. Records result."""
        diff = abs(py_val - cpp_val)
        passed = diff <= tolerance
        detail = f"Py={py_val:.3f}  C++={cpp_val:.3f}  Δ={diff:.3f}  tol={tolerance}"
        self.results.append((label, passed, detail))
        if passed:
            self.pass_count += 1
        else:
            self.fail_count += 1
        return passed

    def assert_equal(self, py_val, cpp_val, label):
        """Assert two values are exactly equal (for strings, enums)."""
        passed = py_val == cpp_val
        detail = f"Py={py_val}  C++={cpp_val}"
        self.results.append((label, passed, detail))
        if passed:
            self.pass_count += 1
        else:
            self.fail_count += 1
        return passed

    def assert_sign_match(self, py_val, cpp_val, label):
        """Assert two values have the same sign (both positive, negative, or zero)."""
        def sign(x):
            if x > 0:
                return 1
            elif x < 0:
                return -1
            return 0
        passed = sign(py_val) == sign(cpp_val)
        detail = f"Py={py_val:.3f} (sign={sign(py_val)})  C++={cpp_val:.3f} (sign={sign(cpp_val)})"
        self.results.append((label, passed, detail))
        if passed:
            self.pass_count += 1
        else:
            self.fail_count += 1
        return passed

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def print_report(self):
        """Print a summary of all assertions."""
        width = 60
        print("\n" + "=" * width)
        print(f"  {self.name} — TEST RESULTS")
        print("=" * width)

        for label, passed, detail in self.results:
            icon = "✅" if passed else "❌"
            print(f"  {icon} {label}")
            if not passed:
                print(f"     ↳ {detail}")

        print("-" * width)
        total = self.pass_count + self.fail_count
        print(f"  TOTAL: {self.pass_count}/{total} passed")
        if self.fail_count > 0:
            print(f"  ❌ {self.fail_count} FAILURES")
        else:
            print(f"  ✅ ALL TESTS PASSED")
        print("=" * width)

    def execute(self):
        """Run tests and exit with appropriate code."""
        try:
            self.run_tests()
        except Exception as e:
            print(f"\n❌ Test harness crashed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(2)

        self.print_report()
        sys.exit(1 if self.fail_count > 0 else 0)

    def run_tests(self):
        """Override in subclass."""
        raise NotImplementedError("Subclass must implement run_tests()")
