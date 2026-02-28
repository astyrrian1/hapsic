---

# PRODUCT REQUIREMENTS DOCUMENT: HAPSIC 2.3.1 (GOLD MASTER REVISED)

**System:** High-Altitude Psychrometric Steam Injection Control (v2.3.1) **Architecture:** MPC Cascade PI + State-Based Glide-Path Batch Manager **Target Platform:** Hardware-Agnostic (StamPLC C++ RTOS / Home Assistant "Soft-PLC") **Constraint Environment:** Amarillo, TX (Elevation 3,605 ft | $P_{atm}$ = 88.6 kPa) **Release Notes (v2.3.1):** Incorporates the hardware-restricted operating envelope ("Simmer Floor"), FSM Satisfaction Coasting deadbands, and the 5-Minute Anti-Short Cycle Timer (ASCT) to prevent electrode scaling and contactor failure.

---

## 1. Architectural Scope & Axioms

* 
**Absolute Psychrometrics Only:** Internal math evaluates strictly on Absolute Dew Point (°F).


* 
**RH% Limitation:** Relative Humidity is permitted only for physical safety clamps.


* 
**Feasibility Horizon (Physics Over Logic):** The controller continuously calculates the maximum achievable moisture state based on outdoor weather, ERV latent recovery, and structural leakage.


* 
**State-Based Actuation:** Output MUST be held in "Stasis" during cold starts until physical phase-change (boiling) is mathematically proven by duct sensor derivatives.


* 
**Universal Directional Freezing:** To prevent Integral Corruption, back-calculation is banned, and integrators are strictly directionally frozen against mathematical boundaries.


* 
**Deterministic Execution:** The system MUST run on a rigid, single-threaded 5-second tick cycle.



---

## 2. Thermodynamic Model Overview

### 2.1 Hardcoded Constants & Structural Parameters

| Parameter | Value | Description |
| --- | --- | --- |
| **$P_{atm}$** | 88.6 kPa | Local Barometric Pressure 

 |
| **$\rho$ (Rho)** | 0.065 lbs/ft³ | Air Density 

 |
| **Max_Duct_DP** | 60.0°F | Absolute condensation limit 

 |
| **CFM Multiplier** | 0.5886 | Converts m³/h to CFM 

 |
| **Natural Infiltration** | 81.18 CFM | Calculated via 1380 CFM50 / 17.0 N-Factor 

 |

### 2.2 Mass Balance & Feasibility Horizon Engine

The engine evaluates the following formulas sequentially per tick to define the system's physical ceiling:

* 
**Total_CFM** = (Z_Supply_Flow * 0.5886) + CFM_nat 


* 
**Incoming_W** = (((Z_Supply_Flow * 0.5886) * Z_Supply_W) + (CFM_nat * Outdoor_W)) / Total_CFM 


* 
**Delta_W** = ((Max_Capacity * 7000) / 60) / (Total_CFM * 0.065) 


* 
**Max_Achievable_DP** = Output of Reverse Psychrometric conversion using (Incoming_W + Delta_W).


* 
**Feasibility Flag:** If User_Target_DP > (Max_Achievable_DP + 0.5°F), the system sets `is_target_infeasible = True`.



---

## 3. The Master Scheduler & Boot Sequence

### 3.1 Boot Sequence: The "Safety Park" Protocol & NVRAM Load

Upon firmware boot, the controller MUST execute a blocking hardware reset to ensure physical safety:

* 
**Force Valve to 0.0V:** Write actuator explicitly to 0.


* 
**Force Fan to Auto:** Sets `auto_ventilation = ON`.


* 
**Force Boost to Off:** Cancels pending mechanical timers.


* 
**State Restore:** Read persistent CHI memory (NVRAM/HA Helper) to seed the Canister Health filter, defaulting to 1.0 if unavailable.


* 
**Reset Memory:** Flush Integrators and Loop B Timers, set `Boil_Achieved = False`, `zero_volt_ticks = 0`, and initialize FSM to STANDBY.



### 3.2 Execution Order (Per 5-Second Tick)

1. 
**Tick Management:** Increment counters, update `zero_volt_ticks`, update 60-second ring buffers for the EMA_Duct_DP.


2. 
**HAL Update:** Read physical inputs, apply DSP filters, execute Section 7 Thermodynamics.


3. 
**Priority 0 Interlocks:** Check Watchdogs.


4. 
**FSM Evaluation:** Assess FSM thresholds and token handshakes.


5. 
**Loop A:** Execute every 60 seconds (Modulo 12).


6. 
**Loop B (Batch Manager):** Execute every 5 seconds.


7. 
**Write Output:** Evaluate directional freezes, apply DAC scaling, write to physical actuator.


8. 
**SCADA Publish:** Emit JSON MQTT payload and terminal heartbeat.



---

## 4. Priority 0 Interlocks (Safety Watchdogs)

If any of these watchdogs trip, the system immediately triggers the FAULT state, cuts steam to 0.0V, sets `Boil_Achieved = False`, and shatters Loop B Stasis locks.

* 
**Deadman:** Missing or stale telemetry for > 120s.


* 
**Zero Flow:** Supply Flow drops < 20.0 m³/h.


* 
**Economizer Yield:** ERV Bypass State > 5.0%.


* 
**Defrost/Clogged Filter (LATCHING):** (Extract_Flow - Supply_Flow) > 50.0 m³/h. Escalate to permanent human-reset lockout if condition persists > 2880 ticks (4 hours).



---

## 5. The Finite State Machine (FSM)

* 
**STATE 0 (FAULT):** Forces Valve 0.0V and Auto-Vent ON, automatically recovering to STANDBY after 60s of clear faults.


* 
**STATE 1 (STANDBY):** Assumes Sovereign ERV control, transitioning to ACTIVE_CRUISE if Room_DP < (User_Target_DP - 1.0°F).


* 
**STATE 2 (ACTIVE_CRUISE):** Tracks DP. **Satisfaction Coasting:** If the commanded output hits 0.0V AND the Room Deficit is < 0.5°F, the FSM immediately transitions to STANDBY. It fully powers down the PI loop and rests until the house naturally leaks a full 1.0°F below the target.


* 
**STATE 3 (TURBO_PENDING):** Waits for Supply Flow > 200 CFM. It triggers only if Voltage Demand > 9.5V, Duct RH > 82.0%, Room DP Deficit > 3.0°F, and `turbo_lockout_ticks == 0`. Aborts to ACTIVE_CRUISE with a 30-min penalty if taking > 60s.


* 
**STATE 4 (ACTIVE_TURBO):** Delivers dilated capacity and drops back to ACTIVE_CRUISE when Room_Deficit < 1.0°F.


* 
**STATE 5 (HYGIENIC_PURGE):** 10-minute boost. Instantly aborts on Swamp Traps (Outdoor_DP > Room_DP) or Over-Drying Limits (User_Target_DP - Room_DP > 2.0°F).



---

## 6. The MIMO Control Topology (Loops A & B)

### 6.1 Loop A: The Strategist

* 
**Targeting Logic:** Evaluate strictly against User_Target_DP without artificially clamping based on feasibility limits. Output_DP = User_Target_DP + (2.0 * Error) + (0.1 * Integrator_A).


* 
**Minimum Clamp:** MAX(30.0F, Pre_Steam_DP); freeze Integrator_A if limited and Error < 0.


* 
**Maximum Clamp:** 60.0F; freeze Integrator_A if limited and Error > 0.


* 
**Feasibility Freeze:** Freeze Integrator_A if `is_target_infeasible == True` and Error > 0.



### 6.2 Loop B: State-Based Glide-Path Batch Sequencer

To prevent boiler collapse and scale baking, the physical bounds of the control logic are strictly clamped to a window of 3.5V to 9.5V.

* 
**Phase 0 (Memory & ASCT):** Increment `zero_volt_ticks` if Current_Voltage == 0.0V; reset to 0 if > 0.0V. If `zero_volt_ticks >= 180` (15 mins), set `Boil_Achieved = False`. The Continuous Ideal PID operates with $K_p = 0.1$, $K_i = 0.02$, and a deadband of ±1.5°F, quantized to the nearest 0.5V.


* 
**Anti-Short Cycle Timer (ASCT):** If voltage drops to 0.0V, the sequencer enforces a hard 5-minute (60-tick) lockout (`zero_volt_ticks >= 60`) before the 3.5V minimum-fire bypass can re-engage.


* 
**Phase 1 (Cold Start Strike):** Next_Voltage is capped at 9.5V with Stasis_Active set to True and Stasis_Timer set to 180.


* 
**Phase 2 (Dynamic Shatter):** Stasis locks at 9.5V and decrements the timer, forcing Integrator_B to 0.0. It shatters when Duct_Derivative >= +1.0°F/min or the timer hits 0. A bumpless prime is executed on the tick stasis shatters, and `Boil_Achieved` is set to True.


* 
**Phase 3 (Glide-Path Modulation):** The minimum-fire bypass jumps directly to 3.5V, avoiding the deadzone entirely. Slew rates are capped at +0.5V per 60 seconds (12 ticks) upward and -0.5V per 30 seconds (6 ticks) downward.



### 6.3 Universal Directional Freezing Rule

This C++ logic matrix is applied after evaluating the Safety Ceiling, the 3.5V Min-Fire Clamp, and the 9.5V HW Clamp.

> If `anti_short_cycle_active`: pass # FREEZE Integrator_B Else If (Next_Voltage < Ideal_Voltage) AND (Error > 0):     If (Next_Voltage == 0.0V): Integrator_B += Error [Exception: Escape 3.5V min-fire deadzone]     Else: FREEZE Integrator_B [Exception: Winding up] Else If (Next_Voltage > Ideal_Voltage) AND (Error < 0):     FREEZE Integrator_B [Exception: Winding down] Else:     Integrator_B += Error 
> 
> 

---

## 7. Implementation Annex: Theory to Practice

### 7.1 Thermodynamic Physics Engine (Magnus-Tetens)

**Forward Conversion (Temperature & RH to Dew Point & Humidity Ratio):** 

* 
**Celsius Conversion:** $T_c = (T_f - 32) \times \frac{5}{9}$ 


* 
**Saturation Vapor Pressure ($E_s$ in kPa):** 
$$E_s = 0.61121 \times \exp\left(\frac{17.625 \times T_c}{T_c + 243.04}\right)$$





* 
**Actual Vapor Pressure ($E_a$ in kPa):** $E_a = E_s \times \frac{RH}{100.0}$. (If $E_a \le 0.001$, return $DP_F$ = -40.0°F to prevent log(0) exceptions ).


* 
**Dew Point Calculation:** 
$$\alpha = \ln\left(\frac{E_a}{0.61121}\right)$$


$$DP_c = \frac{243.04 \times \alpha}{17.625 - \alpha}$$


. $DP_F = (DP_c \times \frac{9}{5}) + 32.0$.


* 
**Humidity Ratio ($W$ in grains/lb):** 
$$W = 0.62198 \times \left(\frac{E_a}{P_{atm} - E_a}\right) \times 7000.0$$






**Reverse Conversion (Humidity Ratio $W$ to Feasibility Dew Point $DP_F$):** 

* 
**Ratio Constant:** $k = \frac{W}{4353.86}$ 


* 
**Derived Actual Vapor Pressure:** $E_a = \frac{k \times P_{atm}}{1.0 + k}$ 


* Apply the derived $E_a$ to the Dew Point calculation ($\alpha$).



### 7.2 Hardware Abstraction Layer (HAL) Requirements

* 
**Spatial Sensor Arrays:** The system must independently fetch data from room nodes, convert each to Dew Point, and yield an arithmetic spatial average.


* 
**Duct Plume Shock Absorber:** Duct sensors MUST pass through an Exponential Moving Average (EMA) with $\alpha = 0.2$ to prevent premature derivative boil-detect triggers. Formula: EMA = (New * 0.2) + (Old * 0.8).


* 
**Actuator Translation:** The floating-point demand bounded between 0.0V and 10.0V must be scaled to the plant's resolution, mapping it to an 8-bit PWM integer from 0 to 255.



### 7.3 Telemetry Persistence & Canister Health Index (CHI)

* 
**Volatility Gating:** Instantaneous CHI (Actual Yield / Theoretical Yield) must be hard-clamped to a maximum of 2.0 to filter out massive thermal spikes.


* 
**Deep Smoothing:** The CHI EMA MUST utilize a blending factor of $\alpha = 0.00006$, evaluated exclusively during stable non-stasis boiling with Theoretical Yield > 100 grains/min.


* 
**Non-Volatile Memory:** The CHI_EMA must be periodically written to non-volatile storage and restored upon PLC boot to preserve electrode degradation tracking.



---

## 8. SCADA Data Contract

### 8.1 Diagnostic Terminal Heartbeat

This payload is emitted every 10 seconds.
`[HEARTBEAT] {fsm.state} [Boil:{batch.boil}|Stasis:{batch.stasis_timer}s] | R_DP: {loop_a.pv}F (SP:{loop_a.sp}F, IntA:{loop_a.integrator}) | D_DP: {loop_b.pv}F (SP:{loop_b.sp}F, IntB:{loop_b.integrator}) | dDP/dt: {physics.deriv}F/m | Lim: {limiters.active} | [cite_start]Out: {io.volts}V [FF:{loop_b.v_ff}|P:{loop_b.p}|I:{loop_b.i}]` 

### 8.2 The "Absolute Glass Box" MQTT JSON Schema (`hapsic/telemetry/state`)

Published every 5s with `retain: true` and `QoS: 0`.

```json
{
  "fsm": {
    "state": "String",
    "fault_reason": "String"
  },
  "feasibility": {
    "max_achievable_dp": 0.0,
    "is_infeasible": false,
    "total_loss_cfm": 0.0
  },
  "loop_a": {
    "sp_user_target": 0.0,
    "pv_room_dp": 0.0,
    "error": 0.0,
    "p_term": 0.0,
    "i_term": 0.0,
    "integrator": 0.0,
    "is_frozen": false,
    "output_target": 0.0
  },
  "loop_b": {
    "sp_duct_target": 0.0,
    "pv_duct_dp": 0.0,
    "error": 0.0,
    "v_ff": 0.0,
    "p_term": 0.0,
    "i_term": 0.0,
    "integrator": 0.0,
    "is_frozen": false,
    "ideal_voltage": 0.0
  },
  "batch": {
    "boil_achieved": false,
    "stasis_active": false,
    "stasis_timer_sec": 0,
    "zero_volt_ticks": 0
  },
  "limiters": {
    "ceiling_volts": 0.0,
    "active_limit": "String (NONE, FAULT_LOCK, STASIS_LOCK, SAFETY_CEILING, MIN_FIRE_DEADZONE, MAX_HW_CLAMP, UP_SLEW, DOWN_SLEW)"
  },
  "physics": {
    "duct_derivative": 0.0,
    "structure_velocity": 0.0
  },
  "psychrometrics": {
    "pre_steam_dp": 0.0,
    "outdoor_dp": 0.0,
    "duct_rh_ema": 0.0
  },
  "io": {
    "volts_out": 0.0,
    "steam_mass_lbs": 0.0
  },
  "health": {
    "chi_ratio": 0.0,
    "chi_ema": 0.0
  }
}

```

---

