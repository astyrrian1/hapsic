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
} // namespace mqtt

} // namespace esphome

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

  static PsychResult calculate(float temp_c, float rh_percent,
                               float pressure_kpa);
  static float target_w_from_dp(float target_dp_c, float pressure_kpa);
  static float target_dp_from_w(float w_g_kg, float pressure_kpa);
};

// =============================================================================
// NVS Persistence Structure
// =============================================================================

struct HapsicPersist {
  float chi_ema;
  float cached_target_rh;
  uint32_t magic; // 0xABCD1234 = valid data
};

// =============================================================================
// HAPSIC CONTROLLER — ESPHome External PollingComponent
// =============================================================================

class HapsicController : public PollingComponent {
public:
  HapsicController() : PollingComponent(5000) {} // 5-second tick

  // =========================================================================
  // FSM STATES
  // =========================================================================
  enum State {
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
  void set_extract_flow_sensor(sensor::Sensor *s) { extract_flow_sensor_ = s; }
  void set_bypass_sensor(sensor::Sensor *s) { bypass_sensor_ = s; }

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
  void set_target_dew_point_sensor(sensor::Sensor *s) {
    target_dew_point_sensor = s;
  }

  // Max capacity (ESPHome number component)
  void set_max_capacity_number(number::Number *n) { max_capacity_number_ = n; }

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
  void set_safety_relay(switch_::Switch *s) { safety_relay_ = s; }

  // Text sensors for HA visibility
  void set_fsm_text(text_sensor::TextSensor *s) { fsm_text_ = s; }
  void set_fault_text(text_sensor::TextSensor *s) { fault_text_ = s; }

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
  static constexpr float RHO = 1.041f; // kg/m^3
  static constexpr float EMA_ALPHA = 0.1f;
  static constexpr float CHI_ALPHA = 0.00006f;
  static constexpr float MAX_DUCT_DP = 15.56f; // 60F in C
  static constexpr float MIN_DUCT_DP = -1.11f; // 30F in C
  static constexpr float SLEW_RATE = 0.5f;
  static constexpr float SOLENOID_MIN = 3.5f; // v2.3.1 hardware constraint
  static constexpr float DEADBAND = 0.83f;    // 1.5F in C
  static constexpr float TURBO_STEAM_THRESH = 9.5f;
  static constexpr float TURBO_RH_THRESH = 82.0f;
  static constexpr float TURBO_DEFICIT_THRESH = 1.67f; // 3.0F in C
  static constexpr float TURBO_FLOW_CONFIRM = 340.0f;  // 200 CFM in m3/h
  static constexpr int TURBO_TIMEOUT_TICKS = 12;
  static constexpr int TURBO_LOCKOUT_DURATION = 360;
  static constexpr int FAULT_CLEAR_TICKS = 12;
  static constexpr int PURGE_MAX_TICKS = 120;
  static constexpr int CLOGGED_FILTER_TICKS = 2880;
  static constexpr int DEADMAN_TIMEOUT_MS = 120000;
  static constexpr int BOILING_MIN_TICKS = 120;
  static constexpr float BOILING_MIN_VOLTAGE = 1.0f;
  static constexpr float DEFAULT_TARGET_DP = 4.4f;     // 40F in C default
  static constexpr float DEFAULT_MAX_CAPACITY = 2.27f; // 5 lbs/hr in kg/hr
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
  float target_duct_dp_ = 4.4f; // Default 40F

  // =========================================================================
  // PSYCHROMETRIC INTERNAL STATE TRACKING & TIMEOUTS
  // =========================================================================
  float house_temp_avg_ = 0.0f;
  float house_rh_avg_ = 0.0f;
  float room_dp_ = 0.0f; // Represents the calculated "Inside" condition
  float room_w_ = 0.0f;
  uint32_t last_valid_room_dp_time_ = 0;

  float target_room_dp_ = 0.0f;

  float supply_t_ = 0.0f; // Represents the physical pre-steam air
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
  std::string boil_status_ = "COLD";
  float steam_mass_kg_hr_ = 0.0f;
  float net_flux_ = 0.0f;
  float vent_loss_ = 0.0f;
  float v_ff_ = 0.0f;

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
  sensor::Sensor *extract_flow_sensor_{nullptr};
  sensor::Sensor *bypass_sensor_{nullptr};

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
  number::Number *max_capacity_number_{nullptr};
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
  switch_::Switch *safety_relay_ = nullptr;

  // Text sensor references
  text_sensor::TextSensor *fsm_text_ = nullptr;
  text_sensor::TextSensor *fault_text_ = nullptr;

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
};

} // namespace hapsic
} // namespace esphome
