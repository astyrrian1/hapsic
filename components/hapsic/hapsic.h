// =============================================================================
// HAPSIC 2.2 Gold Master — C++ Soft-PLC Physics Engine
// Target: M5Stack StamPLC (ESP32-S3) | ESPHome 2026.x External Component
//
// Header: class declarations, data structures, constants
// Implementation: hapsic.cpp
//
// NOTE: Forward declarations are used instead of #include <esphome.h> so
//       this header can be safely included from any compilation unit
//       (including the Zehnder component) without include-order issues.
// =============================================================================

#pragma once

#include <cmath>
#include <cstdint>
#include <string>

#include "esphome/core/component.h"
#include "esphome/core/preferences.h"

// Forward declarations for ESPHome types used in our interface.
// This avoids dependency on include order within the generated esphome.h.
namespace esphome {

namespace sensor {
class Sensor;
}

namespace binary_sensor {
class BinarySensor;
}

namespace text_sensor {
class TextSensor;
}

namespace number {
class Number;
}

namespace button {
class Button;
}

namespace output {
class FloatOutput;
}

namespace switch_ {
class Switch;
}

namespace mqtt {
class MQTTClientComponent;
extern MQTTClientComponent *global_mqtt_client;
}  // namespace mqtt

}  // namespace esphome

namespace esphome {
namespace hapsic {

// =============================================================================
// PSYCHROMETRICS
// =============================================================================

struct PsychResult {
  float dew_point_c;
  float mixing_ratio_g_kg;
  bool is_valid;
};

class MagnusTetens {
 public:
  static constexpr float A = 17.625f;
  static constexpr float B = 243.04f;

  static PsychResult calculate(float temp_c, float rh_percent, float pressure_kpa);
  static float target_w_from_dp(float target_dp_c, float pressure_kpa);
  static float target_dp_from_w(float w_g_kg, float pressure_kpa);
};

// =============================================================================
// NVS Persistence Structure
// =============================================================================

struct HapsicPersist {
  float chi_ema;
  float cached_target_rh;
  float boiler_curve[4];  // [2-4V), [4-6V), [6-8V), [8-10V] EMA lbs/hr
  uint32_t magic;         // 0xABCD1235 = valid data (bumped from 1234 for migration)
};

// =============================================================================
// HAPSIC CONTROLLER — ESPHome External PollingComponent
// =============================================================================

class HapsicController : public PollingComponent {
 public:
  HapsicController() : PollingComponent(5000) {}  // 5-second tick

  // =========================================================================
  // FSM STATES
  // =========================================================================
  enum State {
    INITIALIZING,
    STANDBY,
    ACTIVE_CRUISE,
    TURBO_PENDING,
    ACTIVE_TURBO,
    HYGIENIC_PURGE,
    FAULT,
    MAINTENANCE_LOCKOUT
  };

  // =========================================================================
  // SENSOR / OUTPUT WIRING (called from codegen)
  // =========================================================================

  // Local hardware sensors
  void set_duct_temp_sensor(sensor::Sensor *s) { duct_temp_sensor_ = s; }
  void set_duct_rh_sensor(sensor::Sensor *s) { duct_rh_sensor_ = s; }
  void set_supply_flow_sensor(sensor::Sensor *s) { supply_flow_sensor_ = s; }
  void set_supply_flow_sensor_ha(sensor::Sensor *s) { supply_ha_flow_sensor_ = s; }
  void set_extract_flow_sensor(sensor::Sensor *s) { extract_flow_sensor_ = s; }
  void set_extract_flow_sensor_ha(sensor::Sensor *s) { extract_ha_flow_sensor_ = s; }
  void set_bypass_sensor(sensor::Sensor *s) { bypass_sensor_ = s; }
  void set_bypass_sensor_ha(sensor::Sensor *s) { bypass_ha_sensor_ = s; }

  // Outdoor
  void set_outdoor_sensors(sensor::Sensor *t, sensor::Sensor *rh) {
    outdoor_temp_sensor_ = t;
    outdoor_rh_sensor_ = rh;
  }

  // -------------------------------------------------------------------------
  // 1. House Averaged (from HA — Primary)
  // -------------------------------------------------------------------------
  void set_house_sensors(sensor::Sensor *t, sensor::Sensor *rh) {
    house_temp_sensor_ = t;
    house_rh_sensor_ = rh;
  }

  // -------------------------------------------------------------------------
  // 2. Extract (from CAN — First Fallback) and (from HA — Second Fallback)
  // -------------------------------------------------------------------------
  void set_extract_sensors_can(sensor::Sensor *t, sensor::Sensor *rh) {
    extract_can_temp_sensor_ = t;
    extract_can_rh_sensor_ = rh;
  }

  void set_extract_sensors_ha(sensor::Sensor *t, sensor::Sensor *rh) {
    extract_ha_temp_sensor_ = t;
    extract_ha_rh_sensor_ = rh;
  }

  // -------------------------------------------------------------------------
  // 3. Supply / Pre-Steam (from CAN — Primary) and (from HA — Fallback)
  // -------------------------------------------------------------------------
  void set_supply_sensors_can(sensor::Sensor *t, sensor::Sensor *rh) {
    supply_can_temp_sensor_ = t;
    supply_can_rh_sensor_ = rh;
  }

  void set_supply_sensors_ha(sensor::Sensor *t, sensor::Sensor *rh) {
    supply_ha_temp_sensor_ = t;
    supply_ha_rh_sensor_ = rh;
  }

  // Target Setpoint (from HA)
  void set_target_dew_point_sensor(sensor::Sensor *s) { target_dew_point_sensor = s; }

  // Max capacity (ESPHome sensor component)
  void set_max_capacity_sensor(sensor::Sensor *s) { max_capacity_sensor_ = s; }

  // Manual reset (ESPHome button component)
  void set_manual_reset_button(button::Button *b) { manual_reset_button_ = b; }

  // PID Tuning (ESPHome number components)
  void set_kp_a_number(number::Number *n) { kp_a_number_ = n; }
  void set_ki_a_number(number::Number *n) { ki_a_number_ = n; }
  void set_kp_b_number(number::Number *n) { kp_b_number_ = n; }
  void set_ki_b_number(number::Number *n) { ki_b_number_ = n; }

  // Outputs
  void set_steam_dac(output::FloatOutput *o) { steam_dac_ = o; }
  void set_fan_dac(output::FloatOutput *o) { fan_dac_ = o; }

  // Text sensors for HA visibility
  void set_fsm_text(text_sensor::TextSensor *s) { fsm_text_ = s; }
  void set_fault_text(text_sensor::TextSensor *s) { fault_text_ = s; }

  // =========================================================================
  // Opt-in Telemetry Sensors
  // =========================================================================
  void set_tel_feasibility_max_achievable_dp(sensor::Sensor *s) { tel_feasibility_max_achievable_dp_ = s; }
  void set_tel_feasibility_total_loss_cfm(sensor::Sensor *s) { tel_feasibility_total_loss_cfm_ = s; }
  void set_tel_loop_a_pv_room_dp(sensor::Sensor *s) { tel_loop_a_pv_room_dp_ = s; }
  void set_tel_loop_a_error(sensor::Sensor *s) { tel_loop_a_error_ = s; }
  void set_tel_loop_a_p_term(sensor::Sensor *s) { tel_loop_a_p_term_ = s; }
  void set_tel_loop_a_i_term(sensor::Sensor *s) { tel_loop_a_i_term_ = s; }
  void set_tel_loop_a_integrator(sensor::Sensor *s) { tel_loop_a_integrator_ = s; }
  void set_tel_loop_a_output_target(sensor::Sensor *s) { tel_loop_a_output_target_ = s; }
  void set_tel_loop_b_pv_duct_dp(sensor::Sensor *s) { tel_loop_b_pv_duct_dp_ = s; }
  void set_tel_loop_b_error(sensor::Sensor *s) { tel_loop_b_error_ = s; }
  void set_tel_loop_b_v_ff(sensor::Sensor *s) { tel_loop_b_v_ff_ = s; }
  void set_tel_loop_b_p_term(sensor::Sensor *s) { tel_loop_b_p_term_ = s; }
  void set_tel_loop_b_i_term(sensor::Sensor *s) { tel_loop_b_i_term_ = s; }
  void set_tel_loop_b_integrator(sensor::Sensor *s) { tel_loop_b_integrator_ = s; }
  void set_tel_loop_b_ideal_voltage(sensor::Sensor *s) { tel_loop_b_ideal_voltage_ = s; }
  void set_tel_batch_stasis_timer_sec(sensor::Sensor *s) { tel_batch_stasis_timer_sec_ = s; }
  void set_tel_batch_zero_volt_ticks(sensor::Sensor *s) { tel_batch_zero_volt_ticks_ = s; }
  void set_tel_limiters_ceiling_volts(sensor::Sensor *s) { tel_limiters_ceiling_volts_ = s; }
  void set_tel_physics_duct_derivative(sensor::Sensor *s) { tel_physics_duct_derivative_ = s; }
  void set_tel_physics_structure_velocity(sensor::Sensor *s) { tel_physics_structure_velocity_ = s; }
  void set_tel_psychro_pre_steam_dp(sensor::Sensor *s) { tel_psychro_pre_steam_dp_ = s; }
  void set_tel_psychro_outdoor_dp(sensor::Sensor *s) { tel_psychro_outdoor_dp_ = s; }
  void set_tel_psychro_duct_rh_ema(sensor::Sensor *s) { tel_psychro_duct_rh_ema_ = s; }
  void set_tel_io_volts_out(sensor::Sensor *s) { tel_io_volts_out_ = s; }
  void set_tel_io_steam_mass_lbs(sensor::Sensor *s) { tel_io_steam_mass_lbs_ = s; }
  void set_tel_health_chi_ema(sensor::Sensor *s) { tel_health_chi_ema_ = s; }
  void set_tel_health_chi_instant(sensor::Sensor *s) { tel_health_chi_instant_ = s; }
  void set_tel_health_effective_max(sensor::Sensor *s) { tel_health_effective_max_ = s; }
  void set_tel_health_measured_steam(sensor::Sensor *s) { tel_health_measured_steam_ = s; }
  void set_tel_health_boil_status(text_sensor::TextSensor *s) { tel_health_boil_status_ = s; }

  void set_tel_feasibility_is_infeasible(binary_sensor::BinarySensor *s) { tel_feasibility_is_infeasible_ = s; }
  void set_tel_batch_boil_achieved(binary_sensor::BinarySensor *s) { tel_batch_boil_achieved_ = s; }
  void set_tel_batch_stasis_active(binary_sensor::BinarySensor *s) { tel_batch_stasis_active_ = s; }

  void set_tel_limiters_active_limit(text_sensor::TextSensor *s) { tel_limiters_active_limit_ = s; }

  // =========================================================================
  // COMPONENT LIFECYCLE
  // =========================================================================
  void setup() override;
  void update() override;

 private:
  // =========================================================================
  // CONSTANTS
  // =========================================================================
  static constexpr float P_ATM = 88.6f;
  static constexpr float RHO = 1.041f;      // kg/m^3 (SI — used in feasibility horizon)
  static constexpr float RHO_IMP = 0.065f;  // lbs/ft^3 (imperial — used in diagnostics / boiler curve)
  static constexpr float EMA_ALPHA = 0.1f;
  static constexpr float CHI_ALPHA = 0.00006f;
  static constexpr float MAX_DUCT_DP = 15.56f;  // 60F in C
  static constexpr float MIN_DUCT_DP = -1.11f;  // 30F in C
  static constexpr float SLEW_RATE = 0.5f;
  static constexpr float SOLENOID_MIN = 3.5f;  // v2.3.1 hardware constraint
  static constexpr float DEADBAND = 0.83f;     // 1.5F in C
  static constexpr float TURBO_STEAM_THRESH = 9.5f;
  static constexpr float TURBO_RH_THRESH = 82.0f;
  static constexpr float TURBO_DEFICIT_THRESH = 1.67f;  // 3.0F in C
  static constexpr float TURBO_FLOW_CONFIRM = 340.0f;   // 200 CFM in m3/h
  static constexpr int TURBO_TIMEOUT_TICKS = 12;
  static constexpr int TURBO_LOCKOUT_DURATION = 360;
  static constexpr int FAULT_CLEAR_TICKS = 12;
  static constexpr int PURGE_MAX_TICKS = 120;
  static constexpr int CLOGGED_FILTER_TICKS = 2880;
  static constexpr int DEADMAN_TIMEOUT_MS = 120000;
  static constexpr int BOILING_MIN_TICKS = 120;
  static constexpr float BOILING_MIN_VOLTAGE = 1.0f;
  static constexpr float DEFAULT_TARGET_DP = 4.4f;        // 40F in C default
  static constexpr float DEFAULT_MAX_CAPACITY = 1.2247f;  // 2.7 lbs/hr in kg/hr
  static constexpr int NVS_PERSIST_TICKS = 60;

  // =========================================================================
  // STATE VARIABLES
  // =========================================================================

  // Tick management
  uint32_t tick_counter_ = 0;
  float dt_ = 5.0f;
  uint32_t last_tick_ms_ = 0;
  uint32_t last_ha_update_ms_ = 0;

  // Duct DP History for derivative (60s = 12 ticks)
  float duct_dp_history_[12] = {0.0f};
  int history_idx_ = 0;
  bool history_filled_ = false;

  // Timers
  int turbo_lockout_ticks_ = 0;
  int fault_clear_ticks_ = 0;
  int turbo_wait_ticks_ = 0;
  int purge_ticks_ = 0;
  int clogged_filter_ticks_ = 0;
  int active_cruise_ticks_ = 0;

  // FSM
  State fsm_state_ = STANDBY;
  std::string fault_reason_ = "NONE";

  // Feasibility Horizon
  bool is_target_infeasible_ = false;
  float max_achievable_dp_ = 0.0f;
  float total_loss_cfm_ = 0.0f;

  // Batch Sequencer (Loop B)
  uint32_t zero_volt_ticks_ = 0;
  bool boil_achieved_ = false;
  bool stasis_active_ = false;
  int stasis_timer_sec_ = 0;
  float ideal_voltage_ = 0.0f;
  int upward_rate_ticks_ = 0;
  int downward_rate_ticks_ = 0;

  // Shadow Integrator (Mode C — Desk Mode only)
  float shadow_prod_voltage_ = -1.0f;
  bool shadow_mode_active_ = false;
  uint32_t shadow_last_update_ms_ = 0;

  // Physical Metrics & Limiters
  float duct_derivative_ = 0.0f;
  float structure_velocity_ = 0.0f;
  std::string active_limit_ = "NONE";
  float ceiling_volts_ = 0.0f;

  // Control
  float steam_voltage_ = 0.0f;
  float fan_voltage_ = 0.0f;
  float integrator_a_ = 0.0f;
  float integrator_b_ = 0.0f;
  float target_duct_dp_ = 4.4f;  // Default 40F

  // =========================================================================
  // PSYCHROMETRIC INTERNAL STATE TRACKING & TIMEOUTS
  // =========================================================================
  float house_temp_avg_ = 0.0f;
  float house_rh_avg_ = 0.0f;
  float room_dp_ = 0.0f;  // Represents the calculated "Inside" condition
  float room_w_ = 0.0f;
  uint32_t last_valid_room_dp_time_ = 0;

  float target_room_dp_ = 0.0f;

  float supply_t_ = 0.0f;  // Represents the physical pre-steam air
  float supply_rh_ = 0.0f;
  float supply_dp_ = 0.0f;
  float supply_w_ = 0.0f;
  uint32_t last_valid_supply_w_time_ = 0;

  float outdoor_dp_ = 0.0f;
  float outdoor_w_ = 0.0f;
  float supply_flow_ = 0.0f;
  float extract_flow_ = 0.0f;
  float bypass_pct_ = 0.0f;
  float max_capacity_ = DEFAULT_MAX_CAPACITY;
  bool using_fallback_ = false;

  float duct_dp_ = 0.0f;
  float duct_w_ = 0.0f;
  float duct_temp_ = 0.0f;
  float duct_rh_ = 0.0f;
  float raw_duct_rh_ = 0.0f;

  // EMA state
  float ema_duct_temp_ = 0.0f;
  float ema_duct_rh_ = 0.0f;
  bool ema_duct_initialized_ = false;
  float ema_supply_flow_ = 0.0f;
  bool ema_flow_initialized_ = false;

  // Cached HA values (survive disconnect)
  float cached_target_dp_ = DEFAULT_TARGET_DP;
  float cached_extract_temp_ = NAN;
  float cached_extract_rh_ = NAN;

  // Diagnostics
  float chi_ema_ = 1.0f;
  float chi_instant_ = 0.0f;
  std::string boil_status_ = "COLD";
  float steam_mass_kg_hr_ = 0.0f;
  float net_flux_ = 0.0f;
  float vent_loss_ = 0.0f;
  float v_ff_ = 0.0f;
  float last_measured_steam_ = 0.0f;

  // Boiler characterization curve (4 bins: [2-4V), [4-6V), [6-8V), [8-10V])
  static constexpr int BOILER_CURVE_BINS = 4;
  static constexpr float BOILER_CURVE_V_MIN = 2.0f;
  static constexpr float BOILER_CURVE_V_STEP = 2.0f;
  static constexpr int BOILER_CURVE_MIN_SAMPLES = 50;
  static constexpr float BOILER_CURVE_ALPHA = 0.002f;
  float boiler_curve_[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  int boiler_curve_counts_[4] = {0, 0, 0, 0};

  // NVS persistence
  ESPPreferenceObject pref_;
  int nvs_persist_counter_ = 0;

  // Manual reset flag
  bool manual_reset_requested_ = false;

  // =========================================================================
  // SENSOR REFERENCES
  // =========================================================================
  // // Hardware
  sensor::Sensor *duct_temp_sensor_{nullptr};
  sensor::Sensor *duct_rh_sensor_{nullptr};
  sensor::Sensor *supply_flow_sensor_{nullptr};
  sensor::Sensor *supply_ha_flow_sensor_{nullptr};
  sensor::Sensor *extract_flow_sensor_{nullptr};
  sensor::Sensor *extract_ha_flow_sensor_{nullptr};
  sensor::Sensor *bypass_sensor_{nullptr};
  sensor::Sensor *bypass_ha_sensor_{nullptr};

  // Outdoor
  sensor::Sensor *outdoor_temp_sensor_{nullptr};
  sensor::Sensor *outdoor_rh_sensor_{nullptr};

  // Tier 1: House Avg (Home Assistant)
  sensor::Sensor *house_temp_sensor_{nullptr};
  sensor::Sensor *house_rh_sensor_{nullptr};

  // Tier 2: Extract Air (CAN Bus -> HA Backup)
  sensor::Sensor *extract_can_temp_sensor_{nullptr};
  sensor::Sensor *extract_can_rh_sensor_{nullptr};
  sensor::Sensor *extract_ha_temp_sensor_{nullptr};
  sensor::Sensor *extract_ha_rh_sensor_{nullptr};

  // Tier 3: Supply Air / Pre-Steam (CAN Bus -> HA Backup)
  sensor::Sensor *supply_can_temp_sensor_{nullptr};
  sensor::Sensor *supply_can_rh_sensor_{nullptr};
  sensor::Sensor *supply_ha_temp_sensor_{nullptr};
  sensor::Sensor *supply_ha_rh_sensor_{nullptr};

  // Settings
  sensor::Sensor *max_capacity_sensor_{nullptr};
  sensor::Sensor *target_dew_point_sensor{nullptr};

  // PID Tuning Entity Numbersences
  number::Number *kp_a_number_ = nullptr;
  number::Number *ki_a_number_ = nullptr;
  number::Number *kp_b_number_ = nullptr;
  number::Number *ki_b_number_ = nullptr;
  button::Button *manual_reset_button_ = nullptr;

  // Output references
  output::FloatOutput *steam_dac_ = nullptr;
  output::FloatOutput *fan_dac_ = nullptr;

  // Text sensor references
  text_sensor::TextSensor *fsm_text_ = nullptr;
  text_sensor::TextSensor *fault_text_ = nullptr;

  // Opt-in Telemetry Pointers
  sensor::Sensor *tel_feasibility_max_achievable_dp_ = nullptr;
  sensor::Sensor *tel_feasibility_total_loss_cfm_ = nullptr;
  sensor::Sensor *tel_loop_a_pv_room_dp_ = nullptr;
  sensor::Sensor *tel_loop_a_error_ = nullptr;
  sensor::Sensor *tel_loop_a_p_term_ = nullptr;
  sensor::Sensor *tel_loop_a_i_term_ = nullptr;
  sensor::Sensor *tel_loop_a_integrator_ = nullptr;
  sensor::Sensor *tel_loop_a_output_target_ = nullptr;
  sensor::Sensor *tel_loop_b_pv_duct_dp_ = nullptr;
  sensor::Sensor *tel_loop_b_error_ = nullptr;
  sensor::Sensor *tel_loop_b_v_ff_ = nullptr;
  sensor::Sensor *tel_loop_b_p_term_ = nullptr;
  sensor::Sensor *tel_loop_b_i_term_ = nullptr;
  sensor::Sensor *tel_loop_b_integrator_ = nullptr;
  sensor::Sensor *tel_loop_b_ideal_voltage_ = nullptr;
  sensor::Sensor *tel_batch_stasis_timer_sec_ = nullptr;
  sensor::Sensor *tel_batch_zero_volt_ticks_ = nullptr;
  sensor::Sensor *tel_limiters_ceiling_volts_ = nullptr;
  sensor::Sensor *tel_physics_duct_derivative_ = nullptr;
  sensor::Sensor *tel_physics_structure_velocity_ = nullptr;
  sensor::Sensor *tel_psychro_pre_steam_dp_ = nullptr;
  sensor::Sensor *tel_psychro_outdoor_dp_ = nullptr;
  sensor::Sensor *tel_psychro_duct_rh_ema_ = nullptr;
  sensor::Sensor *tel_io_volts_out_ = nullptr;
  sensor::Sensor *tel_io_steam_mass_lbs_ = nullptr;
  sensor::Sensor *tel_health_chi_ema_ = nullptr;
  sensor::Sensor *tel_health_chi_instant_ = nullptr;
  sensor::Sensor *tel_health_effective_max_ = nullptr;
  sensor::Sensor *tel_health_measured_steam_ = nullptr;

  binary_sensor::BinarySensor *tel_feasibility_is_infeasible_ = nullptr;
  binary_sensor::BinarySensor *tel_batch_boil_achieved_ = nullptr;
  binary_sensor::BinarySensor *tel_batch_stasis_active_ = nullptr;

  text_sensor::TextSensor *tel_limiters_active_limit_ = nullptr;
  text_sensor::TextSensor *tel_health_boil_status_ = nullptr;

  // =========================================================================
  // PRIVATE METHODS (implemented in hapsic.cpp)
  // =========================================================================
  float ema(float current, float previous, bool initialized);
  float sensor_value(sensor::Sensor *s);

  bool read_sensors();
  bool execute_interlocks();
  void trigger_fault(const std::string &reason);
  void force_safe_outputs();
  void reset_control_state();
  void evaluate_fsm();
  void execute_loop_a();
  void execute_loop_b();
  void write_output();
  void run_diagnostics();
  void publish_telemetry();
  void update_display();
  void update_buttons();
  void publish_terminal_heartbeat();
  const char *state_name(State s);

  // Boiler characterization
  int boiler_curve_bin_idx(float voltage);
  float voltage_for_steam_rate(float target_lbs_hr);
  float get_effective_max_capacity();
};

}  // namespace hapsic
}  // namespace esphome
