import json
import math
import time
import traceback

import appdaemon.plugins.hass.hassapi as hass


class HapsicController(hass.Hass):

    def initialize(self):
        """
        HAPSIC 2.3.0
        Includes:
        1. Absolute Dew Point Setpoint Paradigm.
        2. Real-time Building Physics Mass Balance (1380 CFM50 / 17.0 N-Factor).
        3. 15-Minute Rolling Buffer for Structure Velocity.
        4. Loop A Feasibility Clamp & Loop B Sliding Safety Ceiling.
        5. Smart V_FF Cold-Start Jump & Derivative Boil-Detect Release.
        6. State-Based Asymmetric Batch Manager (Fast Down / Slow Up).
        7. [v2.2.4] Paradox Resolved: Loop A evaluates raw User Target.
        8. [v2.2.4] Thermodynamic Memory & 10.0V Ignition Strike.
        9. [v2.2.4] Bumpless Handoff Prime & Universal Directional Freezing.
        10. [v2.3.0] Native Notification Blueprints Integration Support.
        """
        # --- 2.1 Environmental Constants ---
        self.P_ATM = 88.6               # kPa (Hardcoded for stability)
        self.RHO = 0.065                # lbs/ft^3

        try:
            self.MAX_CAPACITY = float(self.get_state("input_number.humidifier_max_capacity"))
        except (ValueError, TypeError):
            self.MAX_CAPACITY = 2.7     # Fallback default

        self.MAX_DUCT_DP = 60.0         # °F
        self.MAX_VOLTAGE = 10.0         # V

        # --- State & Memory ---
        self.fsm_state = "STANDBY"
        self.steam_voltage = 0.0
        self.integrator_a = 0.0
        self.integrator_b = 0.0
        self.last_tick_ts = time.time()
        self.startup_ts = time.time()
        self.room_dp_buffer = []        # 15-minute rolling ring-buffer
        self.duct_dp_buffer = []        # 60-second rolling ring-buffer

        # --- 2.2 Signal Conditioning Memory (EMA) ---
        self.ema_alpha = 0.1
        self.ema_sensors = {
            "round_room": {"t": None, "rh": None},
            "kitchen": {"t": None, "rh": None},
            "element": {"t": None, "rh": None}
        }
        # Duct Plume Filter
        self.duct_ema_t = None
        self.duct_ema_rh = None

        # --- Operational Variables ---
        self.target_duct_dp = 0.0
        self.room_dp = 0.0
        self.target_room_dp = 0.0
        self.room_temp_avg = 0.0
        self.room_rh_avg = 0.0
        self.outdoor_dp = 0.0
        self.outdoor_w = 0.0
        self.room_w = 0.0
        self.supply_t = 0.0
        self.supply_rh = 0.0
        self.supply_w = 0.0
        self.supply_dp = 0.0
        self.duct_w = 0.0
        self.bypass_state = 0.0
        self.last_valid_room_dp_time = 0
        self.last_valid_supply_w_time = 0
        self.last_valid_outdoor_time = 0
        self.last_valid_duct_time = 0
        self.last_valid_flow_time = 0

        self.purge_ticks = 0
        self.turbo_wait_ticks = 0
        self.tick_counter = 0
        self.last_brightness = -1
        self.turbo_lockout_ticks = 0
        self.fault_clear_ticks = 0
        self.fault_reason = "NONE"
        self.clogged_filter_ticks = 0

        # --- Batch Manager & Thermodynamic Memory ---
        self.stasis_active = False
        self.stasis_timer = 0
        self.duct_derivative = 0.0
        self.upward_rate_ticks = 0
        self.downward_rate_ticks = 0
        self.boil_achieved = False
        self.zero_volt_ticks = 0

        # --- SCADA & Diagnostics Memory ---
        self.active_cruise_ticks = 0
        self.calc_steam_mass = 0.0
        self.calc_loss_vent = 0.0
        self.calc_flux = 0.0
        self.max_achievable_dp = 0.0
        self.is_target_infeasible = False
        self.chi_instant = 0.0

        # Restore CHI EMA from persistent storage
        try:
            stored_chi = float(self.get_state("input_number.hapsic_chi_ema"))
            self.chi_ema = stored_chi if stored_chi > 0 else 1.0
        except (ValueError, TypeError):
            self.chi_ema = 1.0

        self.chi_alpha = 0.00006

        # --- Online Boiler Characterization Curve ---
        # 4 voltage bins: [2-4V), [4-6V), [6-8V), [8-10V]
        # Each stores EMA of measured steam delivery in lbs/hr
        self.BOILER_CURVE_BINS = 4
        self.BOILER_CURVE_V_MIN = 2.0     # Ignore sub-2V (no meaningful steam)
        self.BOILER_CURVE_V_STEP = 2.0    # 2V per bin
        self.BOILER_CURVE_MIN_SAMPLES = 50  # Gate: don't use until 50 data points
        self.boiler_curve_alpha = 0.002   # ~6 min half-life at 5s ticks
        self.boiler_curve_counts = [0] * self.BOILER_CURVE_BINS

        # Restore boiler curve from persistent storage
        try:
            stored_curve = json.loads(
                self.get_state("input_text.hapsic_boiler_curve"))
            if isinstance(stored_curve, list) and len(stored_curve) == self.BOILER_CURVE_BINS:
                self.boiler_curve = [float(v) for v in stored_curve]
                # Mark restored bins as pre-trained
                self.boiler_curve_counts = [
                    999 if v > 0 else 0 for v in self.boiler_curve]
                self.log(f"Boiler curve restored: {self.boiler_curve}")
            else:
                self.boiler_curve = [0.0] * self.BOILER_CURVE_BINS
        except (ValueError, TypeError, json.JSONDecodeError, Exception):
            self.boiler_curve = [0.0] * self.BOILER_CURVE_BINS

        # --- 3. RESTART HANDLING (Safety Park) ---
        self.log("SYSTEM RESTART DETECTED: Executing Safety Park Protocol...", level="WARNING")
        self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
        self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
        self.turn_off("light.shelly0110dimg3_28372f3e866c")
        self.log("SAFETY PARK COMPLETE: Fan -> Auto, Valve -> 0V. Entering STANDBY.")

        self.run_every(self.master_tick, "now", 5)

    # =========================================================================
    # 2.3 EQUATION 4 (MAGNUS-TETENS) & REVERSE
    # =========================================================================

    def calc_psychrometrics(self, temp_f, rh):
        if temp_f is None or rh is None: return None, None

        temp_c = (temp_f - 32) * (5/9)
        es = 0.61121 * math.exp((17.625 * temp_c) / (temp_c + 243.04))
        ea = es * (rh / 100.0)

        if ea <= 0.001:
            dp_f = -40.0
        else:
            alpha = math.log(ea / 0.61121)
            dp_c = (243.04 * alpha) / (17.625 - alpha)
            dp_f = (dp_c * 9/5) + 32

        w = 0.62198 * (ea / (self.P_ATM - ea)) * 7000.0
        return dp_f, w

    def calc_dp_from_w(self, w_grains):
        if w_grains <= 0: return -40.0

        k = w_grains / 4353.86
        ea = (k * self.P_ATM) / (1.0 + k)

        if ea <= 0.001: return -40.0

        alpha = math.log(ea / 0.61121)
        dp_c = (243.04 * alpha) / (17.625 - alpha)
        return (dp_c * 9/5) + 32

    def get_saturation_vapor_pressure(self, temp_c):
        return 0.61121 * math.exp((17.625 * temp_c) / (temp_c + 243.04))

    # =========================================================================
    # 2. HAL & THERMODYNAMICS
    # =========================================================================

    def update_ema(self, key, raw_t, raw_rh):
        try:
            val_t = float(raw_t)
            val_rh = float(raw_rh)
        except (ValueError, TypeError):
            return None, None

        current = self.ema_sensors[key]
        if current["t"] is None:
            current["t"] = val_t
            current["rh"] = val_rh
        else:
            current["t"] = (self.ema_alpha * val_t) + ((1 - self.ema_alpha) * current["t"])
            current["rh"] = (self.ema_alpha * val_rh) + ((1 - self.ema_alpha) * current["rh"])
        return current["t"], current["rh"]

    def read_and_validate_sensors(self):
        try:
            now_time = time.time()

            def safe_parse(t_val, rh_val):
                if t_val is None or rh_val is None:
                    return None, None
                invalid = {"unavailable", "unknown", "none", ""}
                if str(t_val).strip().lower() in invalid or str(rh_val).strip().lower() in invalid:
                    return None, None
                try:
                    return float(t_val), float(rh_val)
                except (ValueError, TypeError):
                    return None, None

            # --- Inside Conditions (Primary: House HA, Fallback: Extract CAN, Cache 30m) ---
            h_t = self.get_state("sensor.hapsic_room_average_temp")
            h_rh = self.get_state("sensor.hapsic_room_average_rh")
            e_t = self.get_state("sensor.hapsic_cleansed_inside_temp")
            e_rh = self.get_state("sensor.hapsic_cleansed_inside_rh")
            self.log(
                f"HAL DEBUG: room_avg_t={h_t!r}, room_avg_rh={h_rh!r}, "
                f"extract_t={e_t!r}, extract_rh={e_rh!r}",
                level="DEBUG",
            )

            effective_room_t, effective_room_rh = safe_parse(h_t, h_rh)
            if effective_room_t is None:
                self.log("HAL DEBUG: Primary room sensors invalid, falling back to extract CAN.", level="DEBUG")
                effective_room_t, effective_room_rh = safe_parse(e_t, e_rh)

            if effective_room_t is not None and not math.isnan(effective_room_t):
                self.room_dp, self.room_w = self.calc_psychrometrics(effective_room_t, effective_room_rh)
                self.room_temp_avg = effective_room_t
                self.room_rh_avg = effective_room_rh
                self.last_valid_room_dp_time = now_time
            else:
                cache_age = now_time - self.last_valid_room_dp_time
                if self.last_valid_room_dp_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Inside sensors offline (h_t={h_t!r}, "
                        f"h_rh={h_rh!r}, e_t={e_t!r}, e_rh={e_rh!r}). "
                        f"Using cached DP {self.room_dp:.1f}F",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Inside sensors failed (h_t={h_t!r}, "
                        f"h_rh={h_rh!r}, e_t={e_t!r}, e_rh={e_rh!r}) "
                        f"and cache expired (>30m).",
                        level="ERROR",
                    )
                    return False

            # --- Config Inputs (with safe fallback) ---
            raw_target_dp = self.get_state("input_number.target_dew_point")
            raw_max_cap = self.get_state("input_number.humidifier_max_capacity")
            cfg_target, _ = safe_parse(raw_target_dp, raw_max_cap)
            if cfg_target is None:
                self.log(
                    f"HAL ALERT: Config inputs unavailable "
                    f"(target={raw_target_dp!r}, cap={raw_max_cap!r}). "
                    f"Using cached.",
                    level="WARNING",
                )
            else:
                self.target_room_dp = float(raw_target_dp)
                self.MAX_CAPACITY = float(raw_max_cap)

            # --- Outdoor Data (with safe fallback + 30m cache) ---
            raw_out_t = self.get_state("sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature")
            raw_out_rh = self.get_state("sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity")
            effective_out_t, effective_out_rh = safe_parse(raw_out_t, raw_out_rh)

            if effective_out_t is not None and not math.isnan(effective_out_t):
                self.outdoor_dp, self.outdoor_w = self.calc_psychrometrics(effective_out_t, effective_out_rh)
                self.last_valid_outdoor_time = now_time
            else:
                cache_age = now_time - self.last_valid_outdoor_time
                if self.last_valid_outdoor_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Outdoor sensors offline "
                        f"(t={raw_out_t!r}, rh={raw_out_rh!r}). "
                        f"Using cached DP {self.outdoor_dp:.1f}F",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Outdoor sensors failed "
                        f"(t={raw_out_t!r}, rh={raw_out_rh!r}) "
                        f"and cache expired.",
                        level="ERROR",
                    )
                    return False

            # --- Supply / Pre-Steam (CAN) ---
            s_t = self.get_state("sensor.hapsic_pre_steam_temp")
            s_rh = self.get_state("sensor.hapsic_pre_steam_rh")

            effective_sup_t, effective_sup_rh = safe_parse(s_t, s_rh)

            if effective_sup_t is not None and not math.isnan(effective_sup_t):
                self.supply_t = effective_sup_t
                self.supply_rh = effective_sup_rh
                self.supply_dp, self.supply_w = self.calc_psychrometrics(self.supply_t, self.supply_rh)
                self.last_valid_supply_w_time = now_time
            else:
                cache_age = now_time - self.last_valid_supply_w_time
                if self.last_valid_supply_w_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Supply sensors offline "
                        f"(t={s_t!r}, rh={s_rh!r}). "
                        f"Using cached W {self.supply_w:.1f}",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Supply sensors failed "
                        f"(t={s_t!r}, rh={s_rh!r}) and cache expired.",
                        level="ERROR",
                    )
                    return False

            # --- Post-Steam (Duct) with EMA Filter (with safe fallback + 30m cache) ---
            raw_duct_t_str = self.get_state("sensor.hapsic_cleansed_post_steam_temp")
            raw_duct_rh_str = self.get_state("sensor.hapsic_cleansed_post_steam_rh")
            effective_duct_t, effective_duct_rh = safe_parse(raw_duct_t_str, raw_duct_rh_str)

            if effective_duct_t is not None and not math.isnan(effective_duct_t):
                self.raw_duct_rh = effective_duct_rh

                duct_alpha = 0.2
                if self.duct_ema_t is None:
                    self.duct_ema_t = effective_duct_t
                    self.duct_ema_rh = effective_duct_rh
                else:
                    self.duct_ema_t = (duct_alpha * effective_duct_t) + ((1 - duct_alpha) * self.duct_ema_t)
                    self.duct_ema_rh = (duct_alpha * effective_duct_rh) + ((1 - duct_alpha) * self.duct_ema_rh)

                self.duct_t = self.duct_ema_t
                self.duct_rh = self.duct_ema_rh
                self.duct_dp, self.duct_w = self.calc_psychrometrics(self.duct_t, self.duct_rh)
                self.last_valid_duct_time = now_time
            else:
                cache_age = now_time - self.last_valid_duct_time
                if self.last_valid_duct_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Duct sensors offline "
                        f"(t={raw_duct_t_str!r}, rh={raw_duct_rh_str!r}). "
                        f"Using cached DP {self.duct_dp:.1f}F",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Duct sensors failed "
                        f"(t={raw_duct_t_str!r}, rh={raw_duct_rh_str!r}) "
                        f"and cache expired.",
                        level="ERROR",
                    )
                    return False

            # --- Flow (with safe fallback + 30m cache) ---
            raw_supply_flow = self.get_state("sensor.hapsic_supply_flow")
            raw_exhaust_flow = self.get_state("sensor.hapsic_extract_flow")
            raw_bypass = self.get_state("sensor.zehnder_comfoair_q_a4cb9c_bypass_state")
            eff_sf, _ = safe_parse(raw_supply_flow, "0")  # dummy second arg
            eff_ef, _ = safe_parse(raw_exhaust_flow, "0")
            eff_bp, _ = safe_parse(raw_bypass, "0")

            if eff_sf is not None and eff_ef is not None and eff_bp is not None:
                self.supply_flow = eff_sf
                self.exhaust_flow = eff_ef
                self.bypass_state = eff_bp
                self.last_valid_flow_time = now_time
            else:
                cache_age = now_time - self.last_valid_flow_time
                if self.last_valid_flow_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Flow sensors offline "
                        f"(sf={raw_supply_flow!r}, ef={raw_exhaust_flow!r}, "
                        f"bp={raw_bypass!r}). Using cached.",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Flow sensors failed "
                        f"(sf={raw_supply_flow!r}, ef={raw_exhaust_flow!r}, "
                        f"bp={raw_bypass!r}) and cache expired.",
                        level="ERROR",
                    )
                    return False

            # --- 2.4 Mass Balance & Feasibility Horizon Engine ---
            cfm_nat = 1380.0 / 17.0
            vent_mass_factor = ((self.supply_flow * 0.5886) * 60 * self.RHO) / 7000.0
            infil_mass_factor = (cfm_nat * 60 * self.RHO) / 7000.0
            total_mass_factor = vent_mass_factor + infil_mass_factor

            if total_mass_factor > 0:
                steam_plus_supply = self.get_effective_max_capacity() + (vent_mass_factor * self.supply_w)
                max_room_w = (steam_plus_supply + (infil_mass_factor * self.outdoor_w)) / total_mass_factor
                self.max_achievable_dp = self.calc_dp_from_w(max_room_w)
            else:
                self.max_achievable_dp = self.supply_dp

            # Evaluated strictly for Telemetry & Windup protection
            # Hysteresis: SET at +1.0°F, CLEAR at -0.5°F (deadband prevents chatter)
            if self.target_room_dp > (self.max_achievable_dp + 1.0):
                self.is_target_infeasible = True
            elif self.target_room_dp < (self.max_achievable_dp - 0.5):
                self.is_target_infeasible = False

            return True

        except (ValueError, TypeError) as e:
            tb_lines = traceback.format_exc().splitlines()
            relevant_tb = " | ".join(tb_lines[-4:])
            self.log(
                f"HAL ALERT: Critical Sensor Read Failed. "
                f"Type: {type(e).__name__}, Error: {e} "
                f"| Traceback: {relevant_tb}",
                level="ERROR",
            )
            return False

    # =========================================================================
    # 3. MASTER SCHEDULER
    # =========================================================================

    def master_tick(self, kwargs):
        now = time.time()
        self.dt = now - self.last_tick_ts
        self.last_tick_ts = now

        # Deadman Watchdog Interlock
        if self.dt > 120.0 and self.tick_counter > 0:
            self.trigger_fault(f"Deadman Watchdog: Telemetry Stale > 120s ({self.dt:.1f}s)")
            self.publish_telemetry()
            return

        if self.dt > 10.0: self.dt = 5.0

        self.tick_counter += 1
        if self.turbo_lockout_ticks > 0: self.turbo_lockout_ticks -= 1

        valid_sensors = self.read_and_validate_sensors()
        if not valid_sensors:
            startup_age = now - self.startup_ts
            if startup_age < 90.0:
                self.log(
                    f"HAL: Sensors not ready ({startup_age:.0f}s "
                    f"since startup). Waiting in STANDBY.",
                    level="WARNING",
                )
                self.publish_telemetry()
                return
            self.trigger_fault("Sensor Failure")
            self.publish_telemetry()
            return

        self.room_dp_buffer.append(self.room_dp)
        if len(self.room_dp_buffer) > 180:
            self.room_dp_buffer.pop(0)

        # 60-Second Duct EMA Rolling Buffer for Boil-Detect Derivative
        self.duct_dp_buffer.append(self.duct_dp)
        if len(self.duct_dp_buffer) > 13:
            self.duct_dp_buffer.pop(0)

        if len(self.duct_dp_buffer) >= 13:
            self.duct_derivative = self.duct_dp - self.duct_dp_buffer[0]
        else:
            self.duct_derivative = 0.0

        # Thermodynamic Memory Tracking (15-Minute Cooldown)
        if self.steam_voltage == 0.0:
            self.zero_volt_ticks += 1
        else:
            self.zero_volt_ticks = 0

        if self.zero_volt_ticks >= 180:
            self.boil_achieved = False

        # --- PRIORITY 0 INTERLOCKS ---
        if self.supply_flow < 20.0:
            self.trigger_fault(f"Zero Flow ({self.supply_flow} m3/h)")
            self.publish_telemetry()
            return

        imbalance = self.exhaust_flow - self.supply_flow
        if imbalance > 50.0:
            self.clogged_filter_ticks += 1
            if self.clogged_filter_ticks > 2880:
                 self.trigger_fault("CLOGGED FILTER (Requires Reboot)")
                 self.publish_telemetry()
                 return
            self.trigger_fault(f"Defrost Active (Delta: {imbalance:.1f})")
            self.publish_telemetry()
            return
        else:
            if self.fault_reason != "CLOGGED FILTER (Requires Reboot)":
                self.clogged_filter_ticks = 0

        if self.bypass_state > 5.0:
            self.trigger_fault(f"Economizer Bypass Active ({self.bypass_state}%)")
            self.publish_telemetry()
            return

        self.evaluate_fsm()

        if self.tick_counter % 12 == 0:
            self.execute_loop_a()

        if self.fsm_state in ["ACTIVE_CRUISE", "ACTIVE_TURBO", "TURBO_PENDING"]:
            self.execute_loop_b()
        else:
            self.steam_voltage = 0.0
            self.stasis_active = False
            self.stasis_timer = 0
            self.upward_rate_ticks = 0
            self.downward_rate_ticks = 0
            self.boil_achieved = False

        # Unfiltered Hardware E-Stop (Overrides ALL loops and states)
        if hasattr(self, 'raw_duct_rh') and self.raw_duct_rh >= 88.0:
            if self.steam_voltage > 0.0:
                self.log(
                    f"SAFETY ABORT: Raw Duct RH ({self.raw_duct_rh:.1f}%) critically high! Forcing 0.0V.",
                    level="WARNING",
                )
            self.steam_voltage = 0.0
            self.stasis_active = False

        self.write_output()
        self.run_diagnostics()
        self.publish_telemetry()

        # --- HEARTBEAT ---
        if self.tick_counter % 2 == 0:
            self.log(
                f"[HEARTBEAT] FSM: {self.fsm_state} | "
                f"Room DP: {self.room_dp:.1f}F | "
                f"Target DP: {self.target_room_dp:.1f}F | "
                f"Duct DP: {self.duct_dp:.1f}F | "
                f"d(DuctDP)/dt: {self.duct_derivative:.2f}F/m | "
                f"Duct RH: {self.duct_rh:.1f}% | "
                f"Out: {self.steam_voltage:.1f}V",
                level="INFO"
            )

    # =========================================================================
    # 5. FINITE STATE MACHINE
    # =========================================================================

    def evaluate_fsm(self):
        if self.fsm_state in ["ACTIVE_CRUISE", "ACTIVE_TURBO"]:
            self.active_cruise_ticks += 1
        else:
            self.active_cruise_ticks = 0

        if self.fsm_state == "FAULT":
            self.fault_clear_ticks += 1
            if self.fault_clear_ticks >= 12:
                self.log("FSM: Plant stable (60s). Auto-clearing FAULT -> STANDBY.", level="INFO")
                self.fsm_state = "STANDBY"
                self.fault_reason = "NONE"
                self.boil_achieved = False
            return

        if self.fsm_state == "STANDBY":
            if self.room_dp < (self.target_room_dp - 1.0):
                deficit = self.target_room_dp - self.room_dp
                self.log(f"FSM TRANSITION: STANDBY -> ACTIVE_CRUISE. Deficit {deficit:.2f}F", level="INFO")
                self.fsm_state = "ACTIVE_CRUISE"
                self.integrator_a = 0.0
                self.integrator_b = 0.0
                self.call_service("switch/turn_off", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
                self.call_service(
                    "select/select_option",
                    entity_id="select.zehnder_comfoair_q_a4cb9c_fan_speed",
                    option="Medium",
                )
                self.tick_counter = 1
                self.execute_loop_a()

        elif self.fsm_state == "ACTIVE_CRUISE":
            if self.room_dp >= (self.target_room_dp + 1.0):
                self.log("FSM TRANSITION: ACTIVE_CRUISE -> HYGIENIC_PURGE.", level="INFO")
                self.fsm_state = "HYGIENIC_PURGE"
                self.purge_ticks = 0
                self.boil_achieved = False
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_15_min")
                return

            room_deficit = self.target_room_dp - self.room_dp

            # Target Met: Room DP at or above setpoint → coast to STANDBY
            if room_deficit <= 0.0:
                self.log(
                    f"FSM TRANSITION: ACTIVE_CRUISE -> STANDBY (Target Met: "
                    f"room={self.room_dp:.1f}F >= target={self.target_room_dp:.1f}F).",
                    level="INFO",
                )
                self.fsm_state = "STANDBY"
                self.boil_achieved = False
                self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
                return

            # Satisfaction Coasting: Nearly satisfied AND voltage already wound down
            if self.steam_voltage == 0.0 and room_deficit < 0.5:
                self.log("FSM TRANSITION: ACTIVE_CRUISE -> STANDBY (Satisfaction Coasting).", level="INFO")
                self.fsm_state = "STANDBY"
                self.boil_achieved = False
                self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
                return

            if (self.steam_voltage > 9.5 and
                self.duct_rh > 82.0 and
                room_deficit > 3.0 and
                self.turbo_lockout_ticks == 0):

                self.log("FSM TRANSITION: ACTIVE_CRUISE -> TURBO_PENDING.", level="INFO")
                self.fsm_state = "TURBO_PENDING"
                self.turbo_wait_ticks = 0
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_60_min")

        elif self.fsm_state == "TURBO_PENDING":
            self.turbo_wait_ticks += 1
            if self.supply_flow > 200.0:
                self.log(f"FSM TRANSITION: TURBO_PENDING -> ACTIVE_TURBO. Flow {self.supply_flow}", level="INFO")
                self.fsm_state = "ACTIVE_TURBO"
            elif self.turbo_wait_ticks > 12:
                self.log("FSM WARNING: Turbo Boost Failed. Lockout 30m.", level="WARNING")
                self.fsm_state = "ACTIVE_CRUISE"
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
                self.turbo_lockout_ticks = 360

        elif self.fsm_state == "ACTIVE_TURBO":
            if (self.target_room_dp - self.room_dp) < 1.0:
                self.log("FSM TRANSITION: ACTIVE_TURBO -> ACTIVE_CRUISE.", level="INFO")
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
                self.fsm_state = "ACTIVE_CRUISE"

        elif self.fsm_state == "HYGIENIC_PURGE":
            self.purge_ticks += 1
            if self.outdoor_dp > self.room_dp:
                self.log(f"FSM SAFETY: Outdoor air wet ({self.outdoor_dp:.1f}F). Aborting Purge.", level="WARNING")
                self.fsm_state = "STANDBY"
                self.boil_achieved = False
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
                self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
                return

            room_deficit = self.target_room_dp - self.room_dp
            if self.purge_ticks >= 120 or room_deficit > 2.0:
                self.log("FSM TRANSITION: HYGIENIC_PURGE -> STANDBY.", level="INFO")
                self.fsm_state = "STANDBY"
                self.boil_achieved = False
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
                self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")

    def trigger_fault(self, reason):
        if self.fsm_state != "FAULT":
            self.log(f"FSM: FAULT TRIGGERED - {reason}", level="ERROR")
            self.fsm_state = "FAULT"
            self.fault_reason = reason
            self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
            self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")

        self.steam_voltage = 0.0
        self.integrator_a = 0.0
        self.integrator_b = 0.0
        self.fault_clear_ticks = 0
        self.stasis_active = False
        self.stasis_timer = 0
        self.boil_achieved = False
        self.upward_rate_ticks = 0
        self.downward_rate_ticks = 0
        self.write_output()

    # =========================================================================
    # 6. PID LOOPS
    # =========================================================================

    def execute_loop_a(self):
        if self.fsm_state not in ["ACTIVE_CRUISE", "ACTIVE_TURBO", "TURBO_PENDING"]: return

        dt_factor = (self.dt * 12.0) / 60.0

        # Paradox Fix: Loop A evaluates raw User Target directly
        active_target_dp = self.target_room_dp

        error = active_target_dp - self.room_dp
        Kp, Ki = 2.0, 0.1

        # Calculate ideal output prior to limits
        ideal_output = active_target_dp + (Kp * error) + (Ki * (self.integrator_a + (error * dt_factor)))

        min_clamp = max(30.0, self.supply_dp)
        max_clamp = self.MAX_DUCT_DP

        # Strict Directional Freezing (Feasibility & Hardware Clamps)
        freeze_integrator = False

        if self.is_target_infeasible and error > 0:
            freeze_integrator = True
        elif ideal_output < min_clamp and error < 0:
            freeze_integrator = True
        elif ideal_output > max_clamp and error > 0:
            freeze_integrator = True

        if not freeze_integrator:
            self.integrator_a += (error * dt_factor)

        output_dp = active_target_dp + (Kp * error) + (Ki * self.integrator_a)

        self.target_duct_dp = max(min_clamp, min(max_clamp, output_dp))

    def execute_loop_b(self):
        # 1. Calculate Psychrometrics & V_FF
        target_c = (self.target_duct_dp - 32) * 5/9
        target_vp = self.get_saturation_vapor_pressure(target_c)
        target_w = 0.62198 * (target_vp / (self.P_ATM - target_vp)) * 7000.0

        w_req_grains = max(0, target_w - self.supply_w)
        cfm = self.supply_flow * 0.5886
        lbs_hr_req = (w_req_grains * cfm * 60 * self.RHO) / 7000.0

        # Use learned boiler curve when trained, falls back to linear model
        v_ff = self.voltage_for_steam_rate(lbs_hr_req)

        # 2. Continuous Ideal PID
        error = self.target_duct_dp - self.duct_dp
        if abs(error) < 1.5:
            error = 0.0

        Kp_b, Ki_b = 0.1, 0.02
        dt_factor = self.dt / 5.0

        # Compute Ideal Voltage using CURRENT Integrator State
        v_trim = (Kp_b * error) + (Ki_b * self.integrator_b)
        ideal_voltage = v_ff + v_trim
        quantized_target = round(ideal_voltage * 2.0) / 2.0

        next_voltage = self.steam_voltage
        shattered_this_tick = False

        # =========================================================
        # 4. STATE-BASED GLIDE-PATH BATCH SEQUENCER
        # =========================================================

        # Phase 1: Cold Start Ignition Overdrive (Now strikes at 9.5V)
        if not self.boil_achieved and self.steam_voltage == 0.0 and quantized_target >= 3.5:
            next_voltage = 9.5
            self.stasis_active = True
            self.stasis_timer = 180
            self.upward_rate_ticks = 0
            self.downward_rate_ticks = 0

        # Phase 2: Thermodynamic State Release (Dynamic Stasis Shatter)
        elif self.stasis_active:
            self.stasis_timer -= 1
            self.integrator_b = 0.0  # Rigidly force to 0.0 to prevent windup
            next_voltage = 9.5

            # Shatter Condition
            if self.duct_derivative >= 1.0 or self.stasis_timer <= 0:
                self.stasis_active = False
                self.boil_achieved = True
                shattered_this_tick = True
                # BUMPLESS HANDOFF PRIME: Seed the PID instantly using the new 9.5V reality
                self.integrator_b = max(0.0, (9.5 - v_ff - (Kp_b * error)) / Ki_b)

        # Phase 3: Glide-Path Asymmetric Limiting (Modulation Phase)
        anti_short_cycle_active = False

        if not self.stasis_active and not shattered_this_tick:
            if self.steam_voltage == 0.0 and quantized_target >= 3.5:
                # [NEW] 5-Minute Anti-Short Cycle Lockout (60 ticks)
                if self.zero_volt_ticks >= 60:
                    next_voltage = 3.5
                    self.upward_rate_ticks = 0
                    self.downward_rate_ticks = 0
                else:
                    anti_short_cycle_active = True
            elif self.steam_voltage != quantized_target:
                # Asymmetric Slew Limits
                if quantized_target > self.steam_voltage:
                    self.downward_rate_ticks = 0
                    self.upward_rate_ticks += 1
                    if self.upward_rate_ticks >= 12:
                        next_voltage = self.steam_voltage + 0.5
                        self.upward_rate_ticks = 0
                elif quantized_target < self.steam_voltage:
                    self.upward_rate_ticks = 0
                    self.downward_rate_ticks += 1
                    if self.downward_rate_ticks >= 6:
                        next_voltage = self.steam_voltage - 0.5
                        self.downward_rate_ticks = 0

        # =========================================================
        # 5. HARDWARE PROTECTION & UNIVERSAL DIRECTIONAL FREEZING
        # =========================================================

        # Evaluate Safety Limits (Overwrites Phase 3)
        ceiling_volts = max(0.0, 9.5 - ((self.duct_rh - 82.0) * 1.6))

        if next_voltage > ceiling_volts:
            next_voltage = ceiling_volts
            self.stasis_active = False
            self.stasis_timer = 0

        if next_voltage > 0.0 and next_voltage < 3.5:
            next_voltage = 0.0
            self.stasis_active = False
            self.stasis_timer = 0

        next_voltage = max(0.0, min(9.5, next_voltage))

        # Universal Directional Freezing Rule (Matrix)
        if not self.stasis_active and not shattered_this_tick:
            if anti_short_cycle_active:
                pass # [NEW] FREEZE to prevent infinite windup during ASCT lockout
            elif (next_voltage < ideal_voltage) and (error > 0):
                if next_voltage == 0.0:
                    # Exception: Allow windup to escape the 3.5V min-fire deadzone
                    self.integrator_b += (error * dt_factor)
                else:
                    pass # FREEZE: Prevents positive windup against Ceilings, HW Max, or Up-Slew
            elif (next_voltage > ideal_voltage) and (error < 0):
                pass # FREEZE: Prevents negative windup against HW Min or Down-Slew
            else:
                self.integrator_b += (error * dt_factor)

        self.steam_voltage = next_voltage

    def write_output(self):
        physical_volts = self.steam_voltage
        if physical_volts < 2.0:
            physical_volts = 0.0

        brightness = int((physical_volts / 10.0) * 255)
        brightness = max(0, min(255, brightness))

        # Closed-loop override logic
        ha_state = self.get_state("light.shelly0110dimg3_28372f3e866c")

        if brightness == 0:
            if ha_state != "off":
                self.log(f"SAFETY: Shelly is {ha_state} but requested 0%. Forcing OFF.", level="WARNING")
                self.turn_off("light.shelly0110dimg3_28372f3e866c")
                self.last_brightness = 0
        else:
            if brightness != self.last_brightness or ha_state == "off":
                self.turn_on("light.shelly0110dimg3_28372f3e866c", brightness=brightness)
                self.last_brightness = brightness

    # =========================================================================
    # 8 & 9. DIAGNOSTICS & TELEMETRY
    # =========================================================================

    # =========================================================================
    # BOILER CHARACTERIZATION — Online voltage→delivery curve learning
    # =========================================================================

    def _boiler_curve_bin_idx(self, voltage):
        """Map a voltage to a boiler curve bin index, or -1 if out of range."""
        if voltage < self.BOILER_CURVE_V_MIN:
            return -1
        idx = int((voltage - self.BOILER_CURVE_V_MIN) / self.BOILER_CURVE_V_STEP)
        return min(idx, self.BOILER_CURVE_BINS - 1)

    def voltage_for_steam_rate(self, target_lbs_hr):
        """Invert the learned boiler curve to find the voltage that produces target_lbs_hr.

        Falls back to the linear MAX_CAPACITY model if the curve is untrained.
        """
        # Check if any bin is trained
        any_trained = any(
            c >= self.BOILER_CURVE_MIN_SAMPLES and v > 0
            for c, v in zip(self.boiler_curve_counts, self.boiler_curve))

        if not any_trained:
            # Fallback: linear nameplate model
            return min(9.5, (target_lbs_hr / self.MAX_CAPACITY) * 10.0)

        # Walk bins low→high to find where target_lbs_hr falls
        for i in range(self.BOILER_CURVE_BINS):
            if self.boiler_curve_counts[i] < self.BOILER_CURVE_MIN_SAMPLES:
                continue
            if self.boiler_curve[i] <= 0:
                continue

            bin_mid_v = self.BOILER_CURVE_V_MIN + (i + 0.5) * self.BOILER_CURVE_V_STEP

            if self.boiler_curve[i] >= target_lbs_hr:
                # Target falls at or below this bin's measured rate
                # Find previous trained bin for interpolation
                prev_rate = 0.0
                prev_v = 0.0
                for j in range(i - 1, -1, -1):
                    if (self.boiler_curve_counts[j] >= self.BOILER_CURVE_MIN_SAMPLES
                            and self.boiler_curve[j] > 0):
                        prev_rate = self.boiler_curve[j]
                        prev_v = self.BOILER_CURVE_V_MIN + (j + 0.5) * self.BOILER_CURVE_V_STEP
                        break

                if self.boiler_curve[i] == prev_rate:
                    return min(9.5, bin_mid_v)

                frac = ((target_lbs_hr - prev_rate)
                        / (self.boiler_curve[i] - prev_rate))
                return min(9.5, prev_v + frac * (bin_mid_v - prev_v))

        # Target exceeds all learned bins — clamp to max
        return 9.5

    def get_effective_max_capacity(self):
        """Return the best estimate of max steam delivery (lbs/hr).

        Uses the highest trained boiler curve bin if available,
        falls back to CHI-corrected nameplate.
        """
        trained_vals = [
            v for v, c in zip(self.boiler_curve, self.boiler_curve_counts)
            if c >= self.BOILER_CURVE_MIN_SAMPLES and v > 0]
        if trained_vals:
            return max(trained_vals)
        # Fallback: CHI-corrected nameplate
        return self.MAX_CAPACITY * self.chi_ema

    def run_diagnostics(self):
        self.calc_steam_mass = (self.steam_voltage / 10.0) * self.MAX_CAPACITY

        cfm_nat = 1380.0 / 17.0

        vent_mass_factor = ((self.supply_flow * 0.5886) * 60 * self.RHO) / 7000.0
        infil_mass_factor = (cfm_nat * 60 * self.RHO) / 7000.0

        self.calc_loss_vent = vent_mass_factor * max(0, self.room_w - self.supply_w)
        loss_infil = infil_mass_factor * max(0, self.room_w - self.outdoor_w)
        self.calc_flux = self.calc_steam_mass - (self.calc_loss_vent + loss_infil)

        self._last_measured_steam = 0.0
        self.boil_status = "COLD"
        if self.boil_achieved and self.steam_voltage > 1.0:
            self.boil_status = "BOILING"

            dry_air_mass_lbs_hr = (self.supply_flow * 0.5886) * 60 * self.RHO
            theo_grains = self.calc_steam_mass * (7000 / 60)
            actual_grains = (self.duct_w - self.supply_w) * dry_air_mass_lbs_hr / 60.0

            # CHI Gating strictly evaluated off physical boil state
            if theo_grains > 100 and self.boil_achieved and not self.stasis_active:
                self.chi_instant = actual_grains / theo_grains
                self.chi_instant = max(0.0, min(2.0, self.chi_instant))
                self.chi_ema = (self.chi_alpha * self.chi_instant) + ((1 - self.chi_alpha) * self.chi_ema)

                # --- Boiler Curve Learning ---
                # Convert actual grains/min delivery to lbs/hr
                actual_lbs_hr = actual_grains * 60.0 / 7000.0
                self._last_measured_steam = actual_lbs_hr

                bin_idx = self._boiler_curve_bin_idx(self.steam_voltage)
                if bin_idx >= 0 and actual_lbs_hr > 0:
                    a = self.boiler_curve_alpha
                    self.boiler_curve[bin_idx] = (
                        a * actual_lbs_hr
                        + (1 - a) * self.boiler_curve[bin_idx])
                    self.boiler_curve_counts[bin_idx] += 1

                # Persist CHI + boiler curve every 5 minutes (60 ticks)
                if self.tick_counter % 60 == 0:
                    self.call_service("input_number/set_value",
                                      entity_id="input_number.hapsic_chi_ema",
                                      value=round(self.chi_ema, 3))
                    self.call_service("input_text/set_value",
                                      entity_id="input_text.hapsic_boiler_curve",
                                      value=json.dumps(
                                          [round(v, 4) for v in self.boiler_curve]))
            else:
                self.chi_instant = 0.0

    def publish_telemetry(self):
        struct_vel = 0.0
        if len(self.room_dp_buffer) > 0:
            struct_vel = (self.room_dp - self.room_dp_buffer[0]) * 4

        payload = {
            "fsm": {
                "state": self.fsm_state,
                "fault": self.fault_reason,
            },
            "process": {
                "user_target": round(self.target_room_dp, 2),
                "effective_target": round(
                    self.max_achievable_dp if self.is_target_infeasible else self.target_room_dp, 2
                ),
                "is_infeasible": self.is_target_infeasible,
                "max_achievable": round(self.max_achievable_dp, 2),
                "max_achievable_dp": round(self.max_achievable_dp, 2),
                "room_deficit": round(self.target_room_dp - self.room_dp, 2), # Now strictly User Target
                "duct_target": round(self.target_duct_dp, 2),
                "structure_velocity": round(struct_vel, 2),
                "stasis_active": self.stasis_active,
                "duct_derivative": round(self.duct_derivative, 2),
                "is_boiling": self.boil_achieved
            },
            "psychrometrics": {
                "room_dp": round(self.room_dp, 2) if hasattr(self, 'room_dp') else 0.0,
                "room_avg_rh": round(self.room_rh_avg, 2) if hasattr(self, 'room_rh_avg') else 0.0,
                "room_avg_temp": round(self.room_temp_avg, 1) if hasattr(self, 'room_temp_avg') else 0.0,
                "pre_steam_dp": round(self.supply_dp, 2) if hasattr(self, 'pre_steam_dp') else 0.0,
                "post_steam_dp": round(self.duct_dp, 2) if hasattr(self, 'duct_dp') else 0.0,
                "outdoor_dp": round(self.outdoor_dp, 2),
                "bypass_state": self.bypass_state
            },
            "io": {
                "steam_volts": round(self.steam_voltage, 2),
                "steam_mass": round(self.calc_steam_mass, 2) if hasattr(self, 'calc_steam_mass') else 0.0
            },
            "physics": {
                "flux_net": round(self.calc_flux, 2) if hasattr(self, 'calc_flux') else 0.0,
                "loss_vent": round(self.calc_loss_vent, 2) if hasattr(self, 'calc_loss_vent') else 0.0
            },
            "health": {
                "boil_status": getattr(self, 'boil_status', "COLD"),
                "chi_ratio": round(self.chi_instant, 3) if hasattr(self, 'chi_instant') else 0.0,
                "chi_ema": round(self.chi_ema, 3),
                "boiler_curve": [round(v, 3) for v in self.boiler_curve],
                "boiler_curve_samples": list(self.boiler_curve_counts),
                "effective_max_capacity": round(self.get_effective_max_capacity(), 3),
                "measured_steam_lbs_hr": round(getattr(self, '_last_measured_steam', 0.0), 3)
            }
        }

        self.call_service("mqtt/publish", topic="hapsic/telemetry/state", payload=json.dumps(payload))
