"""
HAPSIC Mode C: Shadow Integrator Validation
=============================================
Validates that the C++ desk unit converges to the same voltage output
as the Python production system when running in Shadow Integrator mode.

The Shadow Integrator works by:
1. Desk subscribes to production MQTT `hapsic/telemetry/state`
2. Extracts `io.steam_volts` (Python's actual output)
3. Back-computes integrator_b so ideal_voltage matches production
4. Desk voltage tracks toward production voltage via normal slew dynamics

Test Scenarios:
    1. Steady-state convergence: desk voltage reaches production ±0.5V
    2. Integrator sign: integrator_b should be negative when V_FF > output
    3. FSM state parity: both should be in same FSM state
    4. Stasis bypass: after stasis shatters, shadow should engage

Run:
    python3 test_shadow_integrator.py

Requires:
    - Production (Python) unit online and publishing MQTT
    - Desk (C++) unit online with SHADOW_MODE compiled in
    - paho-mqtt, pyyaml installed
"""

from test_harness import HapsicTestHarness


class ShadowIntegratorTest(HapsicTestHarness):
    """Validate Shadow Integrator voltage convergence."""

    def __init__(self):
        super().__init__(name="Mode C: Shadow Integrator")

    def run_tests(self):
        print(f"\n{'='*60}")
        print("  HAPSIC Mode C: Shadow Integrator Validation")
        print("  Collecting 20 paired MQTT frames (~2 minutes)...")
        print("  (First frames may show divergence during stasis)")
        print(f"{'='*60}\n")

        pairs = self.collect_paired_frames(n=20, timeout=180)
        if len(pairs) < 5:
            print("❌ Not enough paired frames. Are both units online?")
            self.fail_count += 1
            self.results.append(("frame_collection", False, f"Only {len(pairs)} frames"))
            return

        # ------------------------------------------------------------------
        # Test 1: Voltage Convergence (use last 5 frames = ~25-50s of data)
        # ------------------------------------------------------------------
        print("\n--- Test 1: Voltage Convergence ---")
        last_frames = pairs[-5:]
        convergence_count = 0

        for i, (prod, desk) in enumerate(last_frames):
            py_volts = prod.get("io", {}).get("steam_volts", 0.0)
            cpp_volts = desk.get("io", {}).get("volts_out", 0.0)
            diff = abs(py_volts - cpp_volts)
            converged = diff <= 0.5
            if converged:
                convergence_count += 1
            status = '✅' if converged else '⏳'
            print(f"  Frame {i+1}: Py={py_volts:.1f}V  C++={cpp_volts:.1f}V  Δ={diff:.1f}V  {status}")

        # At least 3 of last 5 frames should show convergence
        self.results.append((
            "voltage_convergence",
            convergence_count >= 3,
            f"{convergence_count}/5 frames within ±0.5V"
        ))
        if convergence_count >= 3:
            self.pass_count += 1
        else:
            self.fail_count += 1

        # ------------------------------------------------------------------
        # Test 2: Integrator Sign Correctness
        # ------------------------------------------------------------------
        print("\n--- Test 2: Integrator Sign ---")
        # When V_FF > actual output, integrator_b should be <= 0
        last_desk = pairs[-1][1]
        cpp_vff = last_desk.get("loop_b", {}).get("v_ff", 0.0)
        cpp_integ = last_desk.get("loop_b", {}).get("integrator", 0.0)
        cpp_volts = last_desk.get("io", {}).get("volts_out", 0.0)

        if cpp_vff > cpp_volts + 0.5:
            # V_FF exceeds actual output → integrator should be negative
            integ_correct = cpp_integ <= 0.0
            self.results.append((
                "integrator_sign_negative",
                integ_correct,
                f"V_FF={cpp_vff:.1f}V > Out={cpp_volts:.1f}V → integrator={cpp_integ:.1f} (expected ≤0)"
            ))
            if integ_correct:
                self.pass_count += 1
            else:
                self.fail_count += 1
        else:
            # V_FF close to output → integrator sign is neutral
            self.results.append((
                "integrator_sign_neutral",
                True,
                f"V_FF={cpp_vff:.1f}V ≈ Out={cpp_volts:.1f}V → integrator check N/A"
            ))
            self.pass_count += 1

        # ------------------------------------------------------------------
        # Test 3: FSM State Parity
        # ------------------------------------------------------------------
        print("\n--- Test 3: FSM State Parity ---")
        for i, (prod, desk) in enumerate(last_frames):
            py_fsm = prod.get("fsm", {}).get("state", "N/A")
            cpp_fsm = desk.get("fsm", {}).get("state", "N/A")
            self.assert_equal(py_fsm, cpp_fsm, f"fsm_state_frame_{i+1}")

        # ------------------------------------------------------------------
        # Test 4: Stasis Correctly Resolved
        # ------------------------------------------------------------------
        print("\n--- Test 4: Post-Stasis State ---")
        last_desk_batch = pairs[-1][1].get("batch", {})
        stasis_active = last_desk_batch.get("stasis_active", True)
        boil_achieved = last_desk_batch.get("boil_achieved", False)

        # After enough time, stasis should have shattered
        self.results.append((
            "stasis_resolved",
            not stasis_active,
            f"stasis_active={stasis_active}"
        ))
        if not stasis_active:
            self.pass_count += 1
        else:
            self.fail_count += 1

        self.results.append((
            "boil_achieved",
            boil_achieved,
            f"boil_achieved={boil_achieved}"
        ))
        if boil_achieved:
            self.pass_count += 1
        else:
            self.fail_count += 1

        # ------------------------------------------------------------------
        # Test 5: Voltage Tracking Trend
        # ------------------------------------------------------------------
        print("\n--- Test 5: Voltage Tracking Trend ---")
        # Over time, the desk voltage should be moving toward production
        if len(pairs) >= 10:
            early_diff = abs(
                pairs[2][0].get("io", {}).get("steam_volts", 0) -
                pairs[2][1].get("io", {}).get("volts_out", 0)
            )
            late_diff = abs(
                pairs[-1][0].get("io", {}).get("steam_volts", 0) -
                pairs[-1][1].get("io", {}).get("volts_out", 0)
            )
            trending = late_diff <= early_diff + 0.5  # Allow small noise
            self.results.append((
                "voltage_trend_converging",
                trending,
                f"Early Δ={early_diff:.1f}V → Late Δ={late_diff:.1f}V"
            ))
            if trending:
                self.pass_count += 1
            else:
                self.fail_count += 1


if __name__ == "__main__":
    test = ShadowIntegratorTest()
    test.execute()
