// =============================================================================
// HAPSIC 2.2 Gold Master — C++ Soft-PLC Physics Engine
// Target: M5Stack StamPLC (ESP32-S3) | ESPHome 2026.x External Component
//
// Implementation file — all method bodies
// =============================================================================

#include "hapsic.h"
#include "esphome/components/button/button.h"
#ifdef USE_MQTT
#include "esphome/components/mqtt/mqtt_client.h"
#endif
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/number/number.h"
#include "esphome/components/output/float_output.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/switch/switch.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "esphome/core/preferences.h"
#ifdef USE_ESP32
#include "M5StamPLC.h"
#include <M5GFX.h>
#include <M5Unified.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>
#endif

namespace esphome {
namespace hapsic {

#ifdef USE_ESP32
using namespace m5gfx;
#else
inline uint64_t esp_timer_get_time() {
  return millis() * 1000ULL;
}
#endif

// Native UI Canvas removed for debugging early-boot memory crashes
// M5Canvas ui_canvas(&M5StamPLC.Display);

// =============================================================================
// PSYCHROMETRICS
// =============================================================================

PsychResult MagnusTetens::calculate(float temp_c, float rh_percent, float pressure_kpa) {
  PsychResult result = {-40.0f, 0.0f, false};

  if (std::isnan(temp_c) || std::isnan(rh_percent))
    return result;
  if (rh_percent < 0.0f)
    rh_percent = 0.0f;
  if (rh_percent > 100.0f)
    rh_percent = 100.0f;

  float es = 0.61121f * expf((A * temp_c) / (temp_c + B));
  float ea = es * (rh_percent / 100.0f);

  if (ea <= 0.001f) {
    result.dew_point_c = -40.0f;
    result.mixing_ratio_g_kg = 0.0f;
    result.is_valid = true;
    return result;
  }

  float alpha = logf(ea / 0.61121f);
  float dp_c = (B * alpha) / (A - alpha);
  result.dew_point_c = dp_c;

  if (pressure_kpa - ea > 0.01f) {
    result.mixing_ratio_g_kg = 0.62198f * (ea / (pressure_kpa - ea)) * 1000.0f;
  } else {
    result.mixing_ratio_g_kg = 0.0f;
  }

  result.is_valid = true;
  return result;
}

float MagnusTetens::target_w_from_dp(float target_dp_c, float pressure_kpa) {
  float vp = 0.61121f * expf((A * target_dp_c) / (target_dp_c + B));
  if (pressure_kpa - vp > 0.01f) {
    return 0.62198f * (vp / (pressure_kpa - vp)) * 1000.0f;
  }
  return 0.0f;
}

float MagnusTetens::target_dp_from_w(float w_g_kg, float pressure_kpa) {
  if (std::isnan(w_g_kg) || w_g_kg < 0.0f)
    w_g_kg = 0.0f;
  float k = w_g_kg / 621.98f;
  float ea = (k * pressure_kpa) / (1.0f + k);
  if (ea <= 0.001f)
    return -40.0f;
  float alpha = logf(ea / 0.61121f);
  float dp_c = (243.04f * alpha) / (17.625f - alpha);
  return dp_c;
}

// =============================================================================
// SETUP — Safety Park Boot
// =============================================================================

void HapsicController::setup() {
  ESP_LOGI("hapsic", "HAPSIC 2.2 Gold Master — Safety Park Boot");

  // Allow hardware to settle before probing M5 internal buses
  delay(500);

#ifdef USE_ESP32
  // Initialize the M5StamPLC underlying hardware (Display, PMIC, Internal I2C)
  M5StamPLC_ptr = new m5::M5_STAMPLC();
  M5StamPLC.begin();
#endif

#ifdef USE_ESP32
  // Initialize watchdog (5 seconds, panic/reset on timeout)
  esp_task_wdt_config_t wdt_config = {
      .timeout_ms = 5000,
      .idle_core_mask = (1 << portNUM_PROCESSORS) - 1,  // Monitor all idle tasks
      .trigger_panic = true,
  };
  esp_task_wdt_init(&wdt_config);
  esp_task_wdt_add(NULL);  // Add current task (main loop)
#endif

  if (steam_dac_)
    steam_dac_->set_level(0.0f);
  if (fan_dac_)
    fan_dac_->set_level(0.0f);

  fsm_state_ = INITIALIZING;
  fault_reason_ = "NONE";
  steam_voltage_ = 0.0f;
  fan_voltage_ = 0.0f;
  integrator_a_ = 0.0f;
  integrator_b_ = 0.0f;
  target_duct_dp_ = 40.0f;
  tick_counter_ = 0;
  last_tick_ms_ = esp_timer_get_time() / 1000;
  last_ha_update_ms_ = esp_timer_get_time() / 1000;

  // Load persisted state from NVS
  pref_ = global_preferences->make_preference<HapsicPersist>(fnv1_hash("hapsic_state"));
  HapsicPersist stored;
  if (pref_.load(&stored) && stored.magic == 0xABCD1234) {
    chi_ema_ = stored.chi_ema;
    cached_target_dp_ = stored.cached_target_rh;  // Re-purposed NVRAM slot
    ESP_LOGI("hapsic", "NVS restored: CHI=%.4f, target_dp=%.1fC", chi_ema_, cached_target_dp_);
  } else {
    chi_ema_ = 1.0f;
    cached_target_dp_ = DEFAULT_TARGET_DP;
    ESP_LOGI("hapsic", "NVS empty — defaults: CHI=1.0, target_dp=%.1fC", DEFAULT_TARGET_DP);
  }

  // Reset PRD batch timers
  boil_achieved_ = false;
  zero_volt_ticks_ = 0;
  stasis_active_ = false;
  stasis_timer_sec_ = 0;

  // Register button press callback for manual reset
  if (manual_reset_button_) {
    manual_reset_button_->add_on_press_callback([this]() {
      this->manual_reset_requested_ = true;
      ESP_LOGI("hapsic", "Manual reset button pressed");
    });
  }

#ifdef USE_ESP32
  M5StamPLC.Display.fillRect(0, 0, M5StamPLC.Display.width(), M5StamPLC.Display.height());
#endif
#ifdef DESK_MODE
  // Shadow Integrator: Subscribe to production MQTT for voltage tracking
#ifdef USE_MQTT
  if (mqtt::global_mqtt_client != nullptr) {
    mqtt::global_mqtt_client->subscribe(
        "hapsic/telemetry/state",
        [this](const std::string &topic, const std::string &payload) {
          // Parse io.steam_volts from production JSON
          // Simple extraction without full JSON parser
          auto pos = payload.find("\"steam_volts\":");
          if (pos == std::string::npos)
            pos = payload.find("\"volts_out\":");
          if (pos != std::string::npos) {
            auto colon = payload.find(':', pos);
            if (colon != std::string::npos) {
              float v = atof(payload.c_str() + colon + 1);
              if (v >= 0.0f && v <= 10.0f) {
                shadow_prod_voltage_ = v;
                shadow_mode_active_ = true;
                shadow_last_update_ms_ = millis();
              }
            }
          }
        },
        0);
    ESP_LOGI("hapsic",
             "SHADOW MODE: Subscribed to hapsic/telemetry/state for "
             "production voltage tracking");
  }
#endif
#endif

  ESP_LOGI("hapsic", "Boot complete. State=INITIALIZING, Steam=0V, Relay=OPEN");
}

// =============================================================================
// UPDATE — Master Tick (every 5 seconds)
// =============================================================================

void HapsicController::update() {
#ifdef USE_ESP32
  esp_task_wdt_reset();  // Pet the watchdog
#endif

  // Run the super-fast UI updates
  update_buttons();
  update_display();

  ESP_LOGI("hapsic", "HAPSIC Heartbeat - State: %s", state_name(fsm_state_));

  uint32_t now = esp_timer_get_time() / 1000;
  dt_ = (now - last_tick_ms_) / 1000.0f;
  if (dt_ > 10.0f)
    dt_ = 5.0f;
  if (dt_ < 0.1f)
    dt_ = 5.0f;
  last_tick_ms_ = now;

  tick_counter_++;

  // Update Duct DP History ring buffer
  duct_dp_history_[history_idx_] = duct_dp_;
  history_idx_ = (history_idx_ + 1) % 12;
  if (history_idx_ == 0)
    history_filled_ = true;

  // Calculate derivative (1 min apart if filled)
  if (history_filled_) {
    int oldest_idx = history_idx_;  // the oldest value is where we will write next
    duct_derivative_ = duct_dp_ - duct_dp_history_[oldest_idx];
  } else {
    duct_derivative_ = 0.0f;
  }

  if (turbo_lockout_ticks_ > 0)
    turbo_lockout_ticks_--;
  if (fsm_state_ == FAULT && fault_clear_ticks_ > 0)
    fault_clear_ticks_--;
  if (fsm_state_ == TURBO_PENDING)
    turbo_wait_ticks_++;
  if (fsm_state_ == HYGIENIC_PURGE)
    purge_ticks_++;
  if (fsm_state_ == ACTIVE_CRUISE || fsm_state_ == ACTIVE_TURBO || fsm_state_ == TURBO_PENDING) {
    active_cruise_ticks_++;
  } else {
    active_cruise_ticks_ = 0;
  }

  bool sensors_ok = read_sensors();
  if (!sensors_ok) {
    if (fsm_state_ != INITIALIZING) {
      trigger_fault("Sensor Failure");
    }
    write_output();
    publish_telemetry();
    return;
  }

  if (execute_interlocks()) {
    write_output();
    publish_telemetry();
    return;
  }

  evaluate_fsm();

  if (tick_counter_ % 12 == 0) {
    execute_loop_a();
  }

  if (fsm_state_ == ACTIVE_CRUISE || fsm_state_ == ACTIVE_TURBO || fsm_state_ == TURBO_PENDING) {
    execute_loop_b();
  } else {
    steam_voltage_ = 0.0f;
  }

  // Unfiltered Hardware E-Stop (Overrides ALL loops and states)
  if (raw_duct_rh_ >= 88.0f) {
    if (steam_voltage_ > 0.0f) {
      ESP_LOGW("hapsic", "SAFETY ABORT: Raw Duct RH (%.1f%%) critically high! Forcing 0.0V.", raw_duct_rh_);
    }
    steam_voltage_ = 0.0f;
    stasis_active_ = false;
  }

  write_output();
  run_diagnostics();
  publish_telemetry();
}

// =============================================================================
// HELPERS
// =============================================================================

float HapsicController::ema(float current, float previous, bool initialized) {
  if (!initialized)
    return current;
  return (EMA_ALPHA * current) + ((1.0f - EMA_ALPHA) * previous);
}

float HapsicController::sensor_value(sensor::Sensor *s) {
  if (s == nullptr)
    return NAN;
  return s->state;
}

const char *HapsicController::state_name(State s) {
  switch (s) {
    case INITIALIZING:
      return "INITIALIZING";
    case STANDBY:
      return "STANDBY";
    case ACTIVE_CRUISE:
      return "ACTIVE_CRUISE";
    case TURBO_PENDING:
      return "TURBO_PENDING";
    case ACTIVE_TURBO:
      return "ACTIVE_TURBO";
    case HYGIENIC_PURGE:
      return "HYGIENIC_PURGE";
    case FAULT:
      return "FAULT";
    case MAINTENANCE_LOCKOUT:
      return "MAINTENANCE_LOCKOUT";
    default:
      return "UNKNOWN";
  }
}

// =============================================================================
// SENSOR READING
// =============================================================================

bool HapsicController::read_sensors() {
  // --- Duct sensors (local hardware — always available) ---
  float raw_duct_temp = sensor_value(duct_temp_sensor_);
  float raw_duct_rh = sensor_value(duct_rh_sensor_);

  if (std::isnan(raw_duct_temp) || std::isnan(raw_duct_rh)) {
    ESP_LOGW("hapsic", "Duct sensor NaN — temp=%.1f rh=%.1f", raw_duct_temp, raw_duct_rh);
    return false;
  }

  raw_duct_rh_ = raw_duct_rh;
  ema_duct_temp_ = ema(raw_duct_temp, ema_duct_temp_, ema_duct_initialized_);
  ema_duct_rh_ = ema(raw_duct_rh, ema_duct_rh_, ema_duct_initialized_);
  ema_duct_initialized_ = true;

  duct_temp_ = ema_duct_temp_;
  duct_rh_ = ema_duct_rh_;

  auto duct_psych = MagnusTetens::calculate(duct_temp_, duct_rh_, P_ATM);
  if (duct_psych.is_valid) {
    duct_dp_ = duct_psych.dew_point_c;
    duct_w_ = duct_psych.mixing_ratio_g_kg;
  }

  // --- Supply flow & bypass (CAN) ---
  float raw_flow = sensor_value(supply_flow_sensor_);
  if (std::isnan(raw_flow)) {
    ESP_LOGW("hapsic", "CRITICAL: Supply flow sensor NaN");
    return false;
  }
  ema_supply_flow_ = ema(raw_flow, ema_supply_flow_, ema_flow_initialized_);
  ema_flow_initialized_ = true;
  supply_flow_ = ema_supply_flow_;

  float raw_extract = sensor_value(extract_flow_sensor_);
  if (!std::isnan(raw_extract))
    extract_flow_ = raw_extract;

  // --- Bypass state ---
  float raw_bypass = sensor_value(bypass_sensor_);
  float raw_ha_bypass = sensor_value(bypass_ha_sensor_);
  if (std::isnan(raw_bypass) && !std::isnan(raw_ha_bypass)) {
    raw_bypass = raw_ha_bypass;
  }

  if (!std::isnan(raw_bypass)) {
    bypass_pct_ = raw_bypass;
  }

  // --- Inside Conditions (Primary: House HA, Fallback 1: Extract CAN, Fallback
  // 2: Extract HA, Fallback 3: Cache) ---
  float house_t = sensor_value(house_temp_sensor_);
  float house_rh = sensor_value(house_rh_sensor_);
  float ext_can_t = sensor_value(extract_can_temp_sensor_);
  float ext_can_rh = sensor_value(extract_can_rh_sensor_);
  float ext_ha_t = sensor_value(extract_ha_temp_sensor_);
  float ext_ha_rh = sensor_value(extract_ha_rh_sensor_);

  float effective_room_temp = NAN;
  float effective_room_rh = NAN;
  uint32_t now_ms = esp_timer_get_time() / 1000;

  if (!std::isnan(house_t) && !std::isnan(house_rh)) {
    effective_room_temp = house_t;
    effective_room_rh = house_rh;
    using_fallback_ = false;
  } else if (!std::isnan(ext_can_t) && !std::isnan(ext_can_rh)) {
    effective_room_temp = ext_can_t;
    effective_room_rh = ext_can_rh;
    using_fallback_ = true;
    ESP_LOGW("hapsic", "House sensors NaN. Using Extract CAN fallback.");
  } else if (!std::isnan(ext_ha_t) && !std::isnan(ext_ha_rh)) {
    effective_room_temp = ext_ha_t;
    effective_room_rh = ext_ha_rh;
    using_fallback_ = true;
    ESP_LOGW("hapsic", "House & Extract CAN NaN. Using Extract HA fallback.");
  }

  if (!std::isnan(effective_room_temp) && !std::isnan(effective_room_rh)) {
    auto room_psych = MagnusTetens::calculate(effective_room_temp, effective_room_rh, P_ATM);
    if (room_psych.is_valid) {
      room_dp_ = room_psych.dew_point_c;
      room_w_ = room_psych.mixing_ratio_g_kg;
      house_temp_avg_ = effective_room_temp;
      house_rh_avg_ = effective_room_rh;
      last_valid_room_dp_time_ = now_ms;
    }
  } else {
    if (now_ms - last_valid_room_dp_time_ < 1800000 && last_valid_room_dp_time_ > 0) {
      ESP_LOGW("hapsic", "All Inside sensors NaN! Using cached DP (%.1fC). Expires in %u s", room_dp_,
               1800 - ((now_ms - last_valid_room_dp_time_) / 1000));
      using_fallback_ = true;
    } else {
      ESP_LOGE("hapsic", "CRITICAL: All Inside sensors failed and cache expired (>30m).");
      return false;  // Triggers "Sensor Failure" FAULT
    }
  }

  // --- Supply Conditions (Primary: Supply CAN, Fallback: Supply HA, Fallback
  // 3: Cache) ---
  float sup_can_t = sensor_value(supply_can_temp_sensor_);
  float sup_can_rh = sensor_value(supply_can_rh_sensor_);
  float sup_ha_t = sensor_value(supply_ha_temp_sensor_);
  float sup_ha_rh = sensor_value(supply_ha_rh_sensor_);

  float effective_supply_temp = NAN;
  float effective_supply_rh = NAN;

  if (!std::isnan(sup_can_t) && !std::isnan(sup_can_rh)) {
    effective_supply_temp = sup_can_t;
    effective_supply_rh = sup_can_rh;
  } else if (!std::isnan(sup_ha_t) && !std::isnan(sup_ha_rh)) {
    effective_supply_temp = sup_ha_t;
    effective_supply_rh = sup_ha_rh;
    ESP_LOGW("hapsic", "Supply CAN NaN. Using Supply HA fallback.");
  }

  if (!std::isnan(effective_supply_temp) && !std::isnan(effective_supply_rh)) {
    auto sup_psych = MagnusTetens::calculate(effective_supply_temp, effective_supply_rh, P_ATM);
    if (sup_psych.is_valid) {
      supply_dp_ = sup_psych.dew_point_c;
      supply_w_ = sup_psych.mixing_ratio_g_kg;
      supply_t_ = effective_supply_temp;
      supply_rh_ = effective_supply_rh;
      last_valid_supply_w_time_ = now_ms;
    }
  }

  if (std::isnan(effective_supply_temp) || std::isnan(effective_supply_rh)) {
    if (now_ms - last_valid_supply_w_time_ < 1800000 && last_valid_supply_w_time_ > 0) {
      ESP_LOGW("hapsic",
               "All Supply sensors NaN! Using cached Supply W (%.1fg/kg). "
               "Expires in %u s",
               supply_w_, 1800 - ((now_ms - last_valid_supply_w_time_) / 1000));
    } else {
      ESP_LOGE("hapsic", "CRITICAL: All Supply sensors failed and cache expired (>30m).");
      return false;
    }
  }

  // --- Outdoor conditions (CAN — reports in °C after filter) ---
  float od_temp_c = sensor_value(outdoor_temp_sensor_);
  float od_rh = sensor_value(outdoor_rh_sensor_);

  if (std::isnan(od_temp_c) || std::isnan(od_rh)) {
    od_temp_c = 10.0f;  // 50F in C
    od_rh = 50.0f;      // 50%
    ESP_LOGW("hapsic", "Outdoor sensor NaN, using DEFAULT (10C, 50%%)");
  }

  auto od_psych = MagnusTetens::calculate(od_temp_c, od_rh, P_ATM);
  if (od_psych.is_valid) {
    outdoor_dp_ = od_psych.dew_point_c;
    outdoor_w_ = od_psych.mixing_ratio_g_kg;
  }

  // --- Target setpoint (from HA, absolute DEW POINT format) ---
  float target_dp = sensor_value(target_dew_point_sensor);
  if (!std::isnan(target_dp) && target_dp > -40.0f && target_dp <= 40.0f) {
    cached_target_dp_ = target_dp;
  }

  target_room_dp_ = cached_target_dp_;

  // --- Max capacity (from HA sensor) ---
  float mc = sensor_value(max_capacity_sensor_);
  if (!std::isnan(mc) && mc > 0.1f) {
    max_capacity_ = mc;
  }

  // --- Mass Balance & Feasibility Horizon ---
  total_loss_cfm_ = 1380.0f / 17.0f;  // CFM_nat -> approximately 81.18 CFM
  float total_m3h = supply_flow_ + (total_loss_cfm_ / 0.5886f);

  if (total_m3h > 0.1f) {
    float incoming_w = ((supply_flow_ * supply_w_) + ((total_loss_cfm_ / 0.5886f) * outdoor_w_)) / total_m3h;

    // Use learned max delivery rate (lbs/hr → kg/h) for realistic feasibility
    float effective_max_kg_hr = get_effective_max_capacity() * 0.453592f;
    float delta_w = (effective_max_kg_hr * 1000.0f) / (total_m3h * RHO);

    max_achievable_dp_ = MagnusTetens::target_dp_from_w(incoming_w + delta_w, P_ATM);
  } else {
    max_achievable_dp_ = -40.0f;
  }

  // Hysteresis: SET at +0.5C, CLEAR at -0.25C (deadband prevents chatter)
  if (target_room_dp_ > (max_achievable_dp_ + 0.5f)) {
    is_target_infeasible_ = true;
  } else if (target_room_dp_ < (max_achievable_dp_ - 0.25f)) {
    is_target_infeasible_ = false;
  }

  return true;
}

// =============================================================================
// INTERLOCKS
// =============================================================================

bool HapsicController::execute_interlocks() {
  if (fsm_state_ == INITIALIZING) {
    return false;  // Skip interlocks until we have valid sensors
  }

  if (fsm_state_ == MAINTENANCE_LOCKOUT) {
    if (manual_reset_requested_) {
      ESP_LOGI("hapsic", "Manual reset — exiting MAINTENANCE_LOCKOUT");
      fsm_state_ = STANDBY;
      clogged_filter_ticks_ = 0;
      manual_reset_requested_ = false;
      reset_control_state();
    }
    return true;
  }

  if (bypass_pct_ > 5.0f) {
    trigger_fault("Economizer Bypass Active");
    return true;
  }

  if (supply_flow_ < 20.0f) {
    trigger_fault("Zero Flow");
    return true;
  }

  if ((extract_flow_ - supply_flow_) > 50.0f) {
    clogged_filter_ticks_++;
    if (clogged_filter_ticks_ > CLOGGED_FILTER_TICKS) {
      ESP_LOGE("hapsic", "MAINTENANCE LOCKOUT: Clogged filter > 4 hours");
      fsm_state_ = MAINTENANCE_LOCKOUT;
      fault_reason_ = "Clogged Filter";
      force_safe_outputs();
      return true;
    }
    trigger_fault("Defrost Imbalance");
    return true;
  } else {
    clogged_filter_ticks_ = 0;
  }

  if (fsm_state_ == FAULT) {
    if (fault_clear_ticks_ <= 0) {
      ESP_LOGI("hapsic", "Fault cleared — returning to STANDBY");
      fsm_state_ = STANDBY;
      fault_reason_ = "NONE";
      reset_control_state();
    }
    return true;
  }

  return false;
}

void HapsicController::trigger_fault(const std::string &reason) {
  if (fsm_state_ != FAULT && fsm_state_ != MAINTENANCE_LOCKOUT) {
    ESP_LOGW("hapsic", "FAULT: %s", reason.c_str());
    fsm_state_ = FAULT;
    fault_reason_ = reason;
    fault_clear_ticks_ = FAULT_CLEAR_TICKS;
    force_safe_outputs();
  }
}

void HapsicController::force_safe_outputs() {
  steam_voltage_ = 0.0f;
  fan_voltage_ = 0.0f;
  integrator_a_ = 0.0f;
  integrator_b_ = 0.0f;
}

void HapsicController::reset_control_state() {
  integrator_a_ = 0.0f;
  integrator_b_ = 0.0f;
  steam_voltage_ = 0.0f;
  fan_voltage_ = 0.0f;
  turbo_wait_ticks_ = 0;
  purge_ticks_ = 0;
}

// =============================================================================
// FINITE STATE MACHINE
// =============================================================================

void HapsicController::evaluate_fsm() {
  float room_deficit = target_room_dp_ - room_dp_;

  switch (fsm_state_) {
    case INITIALIZING:
      steam_voltage_ = 0.0f;
      fan_voltage_ = 0.0f;
      if (!std::isnan(duct_temp_) && !std::isnan(duct_rh_) && !std::isnan(room_dp_)) {
        ESP_LOGI("hapsic", "INITIALIZING → STANDBY (All critical sensors available)");
        fsm_state_ = STANDBY;
      } else {
        // Periodic log to show what we are waiting on
        if (tick_counter_ % 6 == 0) {
          ESP_LOGI("hapsic",
                   "INITIALIZING... waiting for sensors (Duct Temp: %.1f, Duct "
                   "RH: %.1f, Room DP: %.1f)",
                   duct_temp_, duct_rh_, room_dp_);
        }
      }
      break;

    case STANDBY:
      steam_voltage_ = 0.0f;
      fan_voltage_ = 0.0f;

      if (room_deficit > 0.555f) {  // 1.0 F
        ESP_LOGI("hapsic", "STANDBY → ACTIVE_CRUISE (deficit=%.1f)", room_deficit);
        fsm_state_ = ACTIVE_CRUISE;
        reset_control_state();
        fan_voltage_ = 5.0f;
        tick_counter_ = ((tick_counter_ / 12) * 12);
      }
      break;

    case ACTIVE_CRUISE:
      fan_voltage_ = 5.0f;

      if (room_deficit < 0.277f && turbo_lockout_ticks_ == 0) {  // 0.5 F
        ESP_LOGI("hapsic", "ACTIVE_CRUISE → STANDBY (deficit=%.1f < 0.28)", room_deficit);
        fsm_state_ = STANDBY;
        steam_voltage_ = 0.0f;
        fan_voltage_ = 0.0f;
      } else if (steam_voltage_ > TURBO_STEAM_THRESH && duct_rh_ > TURBO_RH_THRESH &&
                 room_deficit > TURBO_DEFICIT_THRESH && turbo_lockout_ticks_ == 0) {
        ESP_LOGI("hapsic", "ACTIVE_CRUISE → TURBO_PENDING");
        fsm_state_ = TURBO_PENDING;
        turbo_wait_ticks_ = 0;
      } else if (room_deficit < -0.555f) {  // -1.0 F
        ESP_LOGI("hapsic", "ACTIVE_CRUISE → HYGIENIC_PURGE (surplus=%.1f)", -room_deficit);
        fsm_state_ = HYGIENIC_PURGE;
        purge_ticks_ = 0;
        steam_voltage_ = 0.0f;
        fan_voltage_ = 9.0f;
        integrator_a_ = 0.0f;
        integrator_b_ = 0.0f;
      }
      break;

    case TURBO_PENDING:
      fan_voltage_ = 5.0f;

      if ((supply_flow_ * 0.5886f) > TURBO_FLOW_CONFIRM) {
        ESP_LOGI("hapsic", "TURBO_PENDING → ACTIVE_TURBO (flow=%.0f CFM)", supply_flow_ * 0.5886f);
        fsm_state_ = ACTIVE_TURBO;
        fan_voltage_ = 9.0f;
      } else if (turbo_wait_ticks_ > TURBO_TIMEOUT_TICKS) {
        ESP_LOGW("hapsic", "TURBO_PENDING timeout — lockout 30min");
        fsm_state_ = ACTIVE_CRUISE;
        turbo_lockout_ticks_ = TURBO_LOCKOUT_DURATION;
        turbo_wait_ticks_ = 0;
      }
      break;

    case ACTIVE_TURBO:
      fan_voltage_ = 9.0f;

      if (room_deficit < 0.555f) {  // 1.0 F
        ESP_LOGI("hapsic", "ACTIVE_TURBO → ACTIVE_CRUISE (deficit=%.1f)", room_deficit);
        fsm_state_ = ACTIVE_CRUISE;
        fan_voltage_ = 5.0f;
      }
      break;

    case HYGIENIC_PURGE:
      steam_voltage_ = 0.0f;
      fan_voltage_ = 9.0f;

      if (purge_ticks_ >= PURGE_MAX_TICKS) {
        ESP_LOGI("hapsic", "HYGIENIC_PURGE → STANDBY (10 min elapsed)");
        fsm_state_ = STANDBY;
        fan_voltage_ = 0.0f;
      } else if (room_deficit > 1.111f) {  // 2.0 F
        ESP_LOGI("hapsic", "HYGIENIC_PURGE → STANDBY (over-drying, deficit=%.1f)", room_deficit);
        fsm_state_ = STANDBY;
        fan_voltage_ = 0.0f;
      } else if (outdoor_dp_ > room_dp_) {
        ESP_LOGW("hapsic", "HYGIENIC_PURGE → STANDBY (Swamp Trap: outdoor DP > room)");
        fsm_state_ = STANDBY;
        fan_voltage_ = 0.0f;
      }
      break;

    case FAULT:
    case MAINTENANCE_LOCKOUT:
      steam_voltage_ = 0.0f;
      fan_voltage_ = 0.0f;
      break;
  }
}

// =============================================================================
// LOOP A — STRATEGIST (every 60s)
// =============================================================================

void HapsicController::execute_loop_a() {
  if (fsm_state_ != ACTIVE_CRUISE && fsm_state_ != ACTIVE_TURBO && fsm_state_ != TURBO_PENDING) {
    return;
  }

  float error = target_room_dp_ - room_dp_;
  float dt_factor = (dt_ * 12.0f) / 60.0f;

  float KP_A = 2.0f;
  float KI_A = 0.1f;

  if (kp_a_number_ && !std::isnan(kp_a_number_->state)) {
    KP_A = kp_a_number_->state;
  }
  if (ki_a_number_ && !std::isnan(ki_a_number_->state)) {
    KI_A = ki_a_number_->state;
  }

  // PRD Strict Eq: Output_DP = User_Target_DP + (2.0 * Error) + (0.1 *
  // Integrator_A)
  float p_term = KP_A * error;
  float proposed_val = target_room_dp_ + p_term + (KI_A * integrator_a_);

  float min_clamp = std::max(MIN_DUCT_DP, supply_dp_);
  float max_clamp = MAX_DUCT_DP;

  bool saturated_high = proposed_val >= max_clamp;
  bool saturated_low = proposed_val <= min_clamp;

  // Anti-Windup Strict Directional Freeze
  if (saturated_high && error > 0) {
    // Freeze
  } else if (saturated_low && error < 0) {
    // Freeze
  } else if (is_target_infeasible_ && error > 0) {
    // Feasibility Freeze
  } else {
    integrator_a_ += error;
  }

  target_duct_dp_ = std::max(min_clamp, std::min(max_clamp, target_room_dp_ + (KP_A * error) + (KI_A * integrator_a_)));

  ESP_LOGD("hapsic",
           "LoopA: err=%.2f proposed=%.2f target_duct_dp=%.2f int=%.2f "
           "infeasible=%d",
           error, proposed_val, target_duct_dp_, integrator_a_, is_target_infeasible_);
}

// =============================================================================
// LOOP B — TACTICIAN (every tick)
// =============================================================================

void HapsicController::execute_loop_b() {
  float KP_B = 0.1f;
  float KI_B = 0.02f;

  // 1. Phase 0 (Memory)
  if (steam_voltage_ == 0.0f) {
    zero_volt_ticks_++;
  } else {
    zero_volt_ticks_ = 0;
  }
  if (zero_volt_ticks_ >= 180) {
    boil_achieved_ = false;
  }

  // Feed-Forward — compute moisture demand then invert learned boiler curve
  float target_w = MagnusTetens::target_w_from_dp(target_duct_dp_, P_ATM);
  float w_req = std::max(0.0f, target_w - supply_w_);
  // Imperial path (parity with Python): CFM * 60 * RHO_IMP = lbs/hr dry air
  float cfm = supply_flow_ * 0.5886f;
  float w_req_grains = w_req * (7000.0f / 1000.0f);  // g/kg → grains/lb
  float lbs_hr_req = (w_req_grains * cfm * 60.0f * RHO_IMP) / 7000.0f;

  // Use learned boiler curve when trained, falls back to linear model
  v_ff_ = voltage_for_steam_rate(lbs_hr_req);

  // Error & Deadband
  float error = target_duct_dp_ - duct_dp_;
  if (std::abs(error) < DEADBAND)
    error = 0.0f;

  // 2. Continuous Ideal PID
  float p_term = KP_B * error;
  ideal_voltage_ = std::min(9.5f, v_ff_ + p_term + (KI_B * integrator_b_));

#ifdef DESK_MODE
  // Shadow Integrator (Mode C): Override integrator to track production
  uint32_t now_ms = millis();
  if (shadow_mode_active_ && shadow_prod_voltage_ >= 0.0f && (now_ms - shadow_last_update_ms_) < 30000) {
    // Back-compute integrator so ideal_voltage matches production output
    if (KI_B > 0.001f) {
      integrator_b_ = (shadow_prod_voltage_ - v_ff_ - p_term) / KI_B;
    }
    ideal_voltage_ = std::min(9.5f, v_ff_ + p_term + (KI_B * integrator_b_));
    ESP_LOGD("hapsic", "SHADOW: prod=%.1fV → integ=%.1f ideal=%.1fV", shadow_prod_voltage_, integrator_b_,
             ideal_voltage_);
  }
#endif

  float quantized_target = roundf(ideal_voltage_ * 2.0f) / 2.0f;

  float next_voltage = 0.0f;
  active_limit_ = "NONE";

  // 3. Phase 1 (Cold Start Strike)
  if (!boil_achieved_ && steam_voltage_ == 0.0f && quantized_target >= 3.5f) {
    next_voltage = 9.5f;
    stasis_active_ = true;
    stasis_timer_sec_ = 180;
    upward_rate_ticks_ = 0;
    downward_rate_ticks_ = 0;
  }
  // 4. Phase 2 (Dynamic Shatter)
  else if (stasis_active_) {
    next_voltage = 9.5f;
    integrator_b_ = 0.0f;
    stasis_timer_sec_ -= (int)dt_;

    if (duct_derivative_ >= 1.0f || stasis_timer_sec_ <= 0) {
      // Shatter
      integrator_b_ = std::max(0.0f, (9.5f - v_ff_ - p_term) / KI_B);
      stasis_active_ = false;
      boil_achieved_ = true;
      next_voltage = ideal_voltage_;
      active_limit_ = "STASIS_SHATTER";
    } else {
      active_limit_ = "STASIS_LOCK";
    }
  }
  // 5. Phase 3 (Glide-Path Modulation)
  else {
    if (steam_voltage_ == 0.0f && quantized_target >= 3.5f) {
      next_voltage = 3.5f;  // Min-Fire Bypass
    } else {
      next_voltage = ideal_voltage_;
    }
  }

  // Safety Ceiling & Limits
  ceiling_volts_ = 9.5f - ((duct_rh_ - 82.0f) * 1.6f);
  if (ceiling_volts_ > 9.5f)
    ceiling_volts_ = 9.5f;
  if (ceiling_volts_ < 0.0f)
    ceiling_volts_ = 0.0f;

  // Apply clamps on Next_Voltage
  if (!stasis_active_) {
    if (next_voltage > ceiling_volts_) {
      next_voltage = ceiling_volts_;
      active_limit_ = "SAFETY_CEILING";
    }
    if (next_voltage < SOLENOID_MIN && next_voltage > 0.0f) {
      next_voltage = 0.0f;
      active_limit_ = "MIN_FIRE_DEADZONE";
    }
    if (next_voltage > 9.5f) {
      next_voltage = 9.5f;
      active_limit_ = "MAX_HW_CLAMP";
    }

    // Asymmetric Slew Limits (tick-counter approach matching Python)
    // +0.5V per 60s (12 ticks), -0.5V per 30s (6 ticks)
    if (quantized_target > steam_voltage_) {
      downward_rate_ticks_ = 0;
      upward_rate_ticks_++;
      if (upward_rate_ticks_ >= 12) {
        next_voltage = steam_voltage_ + 0.5f;
        upward_rate_ticks_ = 0;
        active_limit_ = "UP_SLEW";
      } else {
        next_voltage = steam_voltage_;
        active_limit_ = "UP_SLEW";
      }
    } else if (quantized_target < steam_voltage_) {
      upward_rate_ticks_ = 0;
      downward_rate_ticks_++;
      if (downward_rate_ticks_ >= 6) {
        next_voltage = steam_voltage_ - 0.5f;
        downward_rate_ticks_ = 0;
        active_limit_ = "DOWN_SLEW";
      } else {
        next_voltage = steam_voltage_;
        active_limit_ = "DOWN_SLEW";
      }
    } else {
      upward_rate_ticks_ = 0;
      downward_rate_ticks_ = 0;
    }
  }

  // Universal Directional Freezing Rule
  if (next_voltage < ideal_voltage_ && error > 0) {
    if (next_voltage == 0.0f) {
      integrator_b_ += error;  // Escape min-fire
    } else {
      // Freeze
    }
  } else if (next_voltage > ideal_voltage_ && error < 0) {
    // Freeze
  } else {
    integrator_b_ += error;
  }

  steam_voltage_ = next_voltage;

  ESP_LOGD("hapsic", "LoopB: ff=%.2f idl=%.2f next=%.2f nxt_fin=%.2f stasis=%d", v_ff_, ideal_voltage_,
           quantized_target, next_voltage, stasis_active_);
}

// =============================================================================
// OUTPUT WRITING
// =============================================================================

void HapsicController::write_output() {
  if (steam_dac_) {
    steam_dac_->set_level(steam_voltage_ / 10.0f);
  }

  if (fan_dac_) {
    fan_dac_->set_level(fan_voltage_ / 10.0f);
  }
}

// =============================================================================
// DIAGNOSTICS — CHI, Boiling, NVS Persistence
// =============================================================================

void HapsicController::run_diagnostics() {
  steam_mass_kg_hr_ = (steam_voltage_ / 10.0f) * max_capacity_;

  float cfm_nat = 1380.0f / 17.0f;

  float vent_mass_factor = ((supply_flow_ * 0.5886f) * 60.0f * RHO_IMP) / 7000.0f;
  float infil_mass_factor = (cfm_nat * 60.0f * RHO_IMP) / 7000.0f;

  vent_loss_ = vent_mass_factor * std::max(0.0f, room_w_ - supply_w_);
  float loss_infil = infil_mass_factor * std::max(0.0f, room_w_ - outdoor_w_);
  net_flux_ = steam_mass_kg_hr_ - (vent_loss_ + loss_infil);

  last_measured_steam_ = 0.0f;
  if (active_cruise_ticks_ > BOILING_MIN_TICKS && steam_voltage_ > BOILING_MIN_VOLTAGE) {
    boil_status_ = "BOILING";

    float dry_air_mass_lbs_hr = (supply_flow_ * 0.5886f) * 60.0f * RHO_IMP;
    float theo_grains = steam_mass_kg_hr_ * (7000.0f / 60.0f);
    float actual_grains = (duct_w_ - supply_w_) * dry_air_mass_lbs_hr / 60.0f;

    // CHI Gating strictly evaluated off physical boil state
    if (theo_grains > 100.0f && boil_achieved_ && !stasis_active_) {
      chi_instant_ = actual_grains / theo_grains;
      chi_instant_ = std::max(0.0f, std::min(2.0f, chi_instant_));
      chi_ema_ = (CHI_ALPHA * chi_instant_) + ((1.0f - CHI_ALPHA) * chi_ema_);

      // --- Boiler Curve Learning ---
      float actual_lbs_hr = actual_grains * 60.0f / 7000.0f;
      last_measured_steam_ = actual_lbs_hr;

      int bin_idx = boiler_curve_bin_idx(steam_voltage_);
      if (bin_idx >= 0 && actual_lbs_hr > 0.0f) {
        boiler_curve_[bin_idx] =
            (BOILER_CURVE_ALPHA * actual_lbs_hr) + ((1.0f - BOILER_CURVE_ALPHA) * boiler_curve_[bin_idx]);
        boiler_curve_counts_[bin_idx]++;
      }
    }
  } else {
    boil_status_ = "COLD";
  }

  // Persist to NVS every 5 minutes
  nvs_persist_counter_++;
  if (nvs_persist_counter_ >= NVS_PERSIST_TICKS) {
    nvs_persist_counter_ = 0;
    HapsicPersist data;
    data.chi_ema = chi_ema_;
    data.cached_target_rh = cached_target_dp_;  // Mapping DP into legacy struct slot
    for (int i = 0; i < BOILER_CURVE_BINS; i++) {
      data.boiler_curve[i] = boiler_curve_[i];
    }
    data.magic = 0xABCD1235;
    pref_.save(&data);
    ESP_LOGD("hapsic", "NVS persisted: CHI=%.4f, target_dp=%.1f, curve=[%.3f,%.3f,%.3f,%.3f]", chi_ema_,
             cached_target_dp_, boiler_curve_[0], boiler_curve_[1], boiler_curve_[2], boiler_curve_[3]);
  }
}

// =============================================================================
// BOILER CHARACTERIZATION — helpers
// =============================================================================

int HapsicController::boiler_curve_bin_idx(float voltage) {
  if (voltage < BOILER_CURVE_V_MIN)
    return -1;
  int idx = static_cast<int>((voltage - BOILER_CURVE_V_MIN) / BOILER_CURVE_V_STEP);
  return std::min(idx, BOILER_CURVE_BINS - 1);
}

float HapsicController::voltage_for_steam_rate(float target_lbs_hr) {
  // Check if any bin is trained
  bool any_trained = false;
  for (int i = 0; i < BOILER_CURVE_BINS; i++) {
    if (boiler_curve_counts_[i] >= BOILER_CURVE_MIN_SAMPLES && boiler_curve_[i] > 0.0f) {
      any_trained = true;
      break;
    }
  }

  if (!any_trained) {
    // Fallback: linear nameplate model
    return std::min(9.5f, (target_lbs_hr / max_capacity_) * 10.0f);
  }

  // Walk bins low→high to find where target falls
  for (int i = 0; i < BOILER_CURVE_BINS; i++) {
    if (boiler_curve_counts_[i] < BOILER_CURVE_MIN_SAMPLES)
      continue;
    if (boiler_curve_[i] <= 0.0f)
      continue;

    float bin_mid_v = BOILER_CURVE_V_MIN + (i + 0.5f) * BOILER_CURVE_V_STEP;

    if (boiler_curve_[i] >= target_lbs_hr) {
      float prev_rate = 0.0f;
      float prev_v = 0.0f;
      for (int j = i - 1; j >= 0; j--) {
        if (boiler_curve_counts_[j] >= BOILER_CURVE_MIN_SAMPLES && boiler_curve_[j] > 0.0f) {
          prev_rate = boiler_curve_[j];
          prev_v = BOILER_CURVE_V_MIN + (j + 0.5f) * BOILER_CURVE_V_STEP;
          break;
        }
      }

      if (boiler_curve_[i] == prev_rate) {
        return std::min(9.5f, bin_mid_v);
      }

      float frac = (target_lbs_hr - prev_rate) / (boiler_curve_[i] - prev_rate);
      return std::min(9.5f, prev_v + frac * (bin_mid_v - prev_v));
    }
  }

  return 9.5f;  // Target exceeds all learned bins
}

float HapsicController::get_effective_max_capacity() {
  float best = 0.0f;
  for (int i = 0; i < BOILER_CURVE_BINS; i++) {
    if (boiler_curve_counts_[i] >= BOILER_CURVE_MIN_SAMPLES && boiler_curve_[i] > best) {
      best = boiler_curve_[i];
    }
  }
  if (best > 0.0f)
    return best;
  return max_capacity_ * chi_ema_;
}

// =============================================================================
// TELEMETRY — MQTT JSON + HA Text Sensors
// =============================================================================

void HapsicController::publish_telemetry() {
  if (fsm_text_)
    fsm_text_->publish_state(state_name(fsm_state_));
  if (fault_text_)
    fault_text_->publish_state(fault_reason_);

  float loop_a_error = target_room_dp_ - room_dp_;
  float KP_A = kp_a_number_ ? kp_a_number_->state : 2.0f;
  float KI_A = ki_a_number_ ? ki_a_number_->state : 0.1f;
  float loop_a_p_term = KP_A * loop_a_error;
  float loop_a_i_term = KI_A * integrator_a_;

  float loop_b_error = target_duct_dp_ - duct_dp_;
  if (std::abs(loop_b_error) < DEADBAND)
    loop_b_error = 0.0f;
  float KP_B = kp_b_number_ ? kp_b_number_->state : 0.1f;
  float KI_B = ki_b_number_ ? ki_b_number_->state : 0.02f;
  float loop_b_p_term = KP_B * loop_b_error;
  float loop_b_i_term = KI_B * integrator_b_;

  // ESP32 API Opt-In Telemetry
  if (tel_feasibility_max_achievable_dp_)
    tel_feasibility_max_achievable_dp_->publish_state(max_achievable_dp_);
  if (tel_feasibility_total_loss_cfm_)
    tel_feasibility_total_loss_cfm_->publish_state(total_loss_cfm_);
  if (tel_loop_a_pv_room_dp_)
    tel_loop_a_pv_room_dp_->publish_state(room_dp_);
  if (tel_loop_a_error_)
    tel_loop_a_error_->publish_state(loop_a_error);
  if (tel_loop_a_p_term_)
    tel_loop_a_p_term_->publish_state(loop_a_p_term);
  if (tel_loop_a_i_term_)
    tel_loop_a_i_term_->publish_state(loop_a_i_term);
  if (tel_loop_a_integrator_)
    tel_loop_a_integrator_->publish_state(integrator_a_);
  if (tel_loop_a_output_target_)
    tel_loop_a_output_target_->publish_state(target_duct_dp_);
  if (tel_loop_b_pv_duct_dp_)
    tel_loop_b_pv_duct_dp_->publish_state(duct_dp_);
  if (tel_loop_b_error_)
    tel_loop_b_error_->publish_state(loop_b_error);
  if (tel_loop_b_v_ff_)
    tel_loop_b_v_ff_->publish_state(v_ff_);
  if (tel_loop_b_p_term_)
    tel_loop_b_p_term_->publish_state(loop_b_p_term);
  if (tel_loop_b_i_term_)
    tel_loop_b_i_term_->publish_state(loop_b_i_term);
  if (tel_loop_b_integrator_)
    tel_loop_b_integrator_->publish_state(integrator_b_);
  if (tel_loop_b_ideal_voltage_)
    tel_loop_b_ideal_voltage_->publish_state(ideal_voltage_);
  if (tel_batch_stasis_timer_sec_)
    tel_batch_stasis_timer_sec_->publish_state(stasis_timer_sec_);
  if (tel_batch_zero_volt_ticks_)
    tel_batch_zero_volt_ticks_->publish_state(zero_volt_ticks_);
  if (tel_limiters_ceiling_volts_)
    tel_limiters_ceiling_volts_->publish_state(ceiling_volts_);
  if (tel_physics_duct_derivative_)
    tel_physics_duct_derivative_->publish_state(duct_derivative_);
  if (tel_physics_structure_velocity_)
    tel_physics_structure_velocity_->publish_state(structure_velocity_);
  if (tel_psychro_pre_steam_dp_)
    tel_psychro_pre_steam_dp_->publish_state(supply_dp_);
  if (tel_psychro_outdoor_dp_)
    tel_psychro_outdoor_dp_->publish_state(outdoor_dp_);
  if (tel_psychro_duct_rh_ema_)
    tel_psychro_duct_rh_ema_->publish_state(duct_rh_);
  if (tel_io_volts_out_)
    tel_io_volts_out_->publish_state(steam_voltage_);
  if (tel_io_steam_mass_lbs_)
    tel_io_steam_mass_lbs_->publish_state(steam_mass_kg_hr_ * 2.20462f);
  if (tel_health_chi_ema_)
    tel_health_chi_ema_->publish_state(chi_ema_);

  if (tel_feasibility_is_infeasible_)
    tel_feasibility_is_infeasible_->publish_state(is_target_infeasible_);
  if (tel_batch_boil_achieved_)
    tel_batch_boil_achieved_->publish_state(boil_achieved_);
  if (tel_batch_stasis_active_)
    tel_batch_stasis_active_->publish_state(stasis_active_);

  if (tel_limiters_active_limit_)
    tel_limiters_active_limit_->publish_state(active_limit_);

  char json[2048];
  snprintf(json, sizeof(json),
           "{"
           "\"fsm\":{\"state\":\"%s\",\"fault_reason\":\"%s\"},"
           "\"feasibility\":{\"max_achievable_dp\":%.2f,\"is_infeasible\":%s,"
           "\"total_loss_cfm\":%.2f},"
           "\"loop_a\":{\"sp_user_target\":%.2f,\"pv_room_dp\":%.2f,\"error\":%"
           ".2f,\"p_term\":%.2f,\"i_term\":%.2f,\"integrator\":%.2f,\"is_"
           "frozen\":%s,\"output_target\":%.2f},"
           "\"loop_b\":{\"sp_duct_target\":%.2f,\"pv_duct_dp\":%.2f,\"error\":%"
           ".2f,\"v_ff\":%.2f,\"p_term\":%.2f,\"i_term\":%.2f,\"integrator\":%."
           "2f,\"is_frozen\":%s,\"ideal_voltage\":%.2f},"
           "\"batch\":{\"boil_achieved\":%s,\"stasis_active\":%s,\"stasis_"
           "timer_sec\":%d,\"zero_volt_ticks\":%u},"
           "\"limiters\":{\"ceiling_volts\":%.2f,\"active_limit\":\"%s\"},"
           "\"physics\":{\"duct_derivative\":%.2f,\"structure_velocity\":%.2f},"
           "\"psychrometrics\":{\"pre_steam_dp\":%.2f,\"outdoor_dp\":%.2f,"
           "\"duct_rh_ema\":%.2f},"
           "\"io\":{\"volts_out\":%.2f,\"steam_mass_lbs\":%.3f},"
           "\"health\":{\"chi_ratio\":%.4f,\"chi_ema\":%.4f}"
           "}",
           state_name(fsm_state_), fault_reason_.c_str(), max_achievable_dp_, is_target_infeasible_ ? "true" : "false",
           total_loss_cfm_, target_room_dp_, room_dp_, loop_a_error, loop_a_p_term, loop_a_i_term, integrator_a_,
           "false", target_duct_dp_, target_duct_dp_, duct_dp_, loop_b_error, v_ff_, loop_b_p_term, loop_b_i_term,
           integrator_b_, "false", ideal_voltage_, boil_achieved_ ? "true" : "false", stasis_active_ ? "true" : "false",
           stasis_timer_sec_, zero_volt_ticks_, ceiling_volts_, active_limit_.c_str(), duct_derivative_,
           structure_velocity_, supply_dp_, outdoor_dp_, duct_rh_, steam_voltage_, steam_mass_kg_hr_, 1.0f, chi_ema_);

#ifdef USE_MQTT
  if (mqtt::global_mqtt_client != nullptr) {
#ifdef DESK_MODE
    mqtt::global_mqtt_client->publish("hapsic-desk/telemetry/state", json);
#else
    mqtt::global_mqtt_client->publish("hapsic/telemetry/state", json);
#endif
  }
#endif

  if (tick_counter_ % 2 == 0) {
    publish_terminal_heartbeat();
  }
}

void HapsicController::publish_terminal_heartbeat() {
  float loop_a_p_term = (kp_a_number_ ? kp_a_number_->state : 2.0f) * (target_room_dp_ - room_dp_);
  float loop_b_p_term = (kp_b_number_ ? kp_b_number_->state : 0.1f) *
                        (std::abs(target_duct_dp_ - duct_dp_) < DEADBAND ? 0.0f : (target_duct_dp_ - duct_dp_));
  ESP_LOGI("hapsic",
           "[HEARTBEAT] %s [Boil:%s|Stasis:%ds] | R_DP: %.1fC (SP:%.1fC, "
           "IntA:%.1f) | D_DP: %.1fC (SP:%.1fC, IntB:%.1f) | dDP/dt: %.1fC/m | "
           "Lim: %s | Out: %.1fV [FF:%.1f|P:%.1f|I:%.1f]",
           state_name(fsm_state_), boil_achieved_ ? "1" : "0", stasis_timer_sec_, room_dp_, target_room_dp_,
           integrator_a_, duct_dp_, target_duct_dp_, integrator_b_, duct_derivative_, active_limit_.c_str(),
           steam_voltage_, v_ff_, loop_b_p_term, (ki_b_number_ ? ki_b_number_->state : 0.02f) * integrator_b_);
}

// =============================================================================
// ROBUST DISPLAY & CONTROLS (Native M5GFX)
// =============================================================================

#ifndef TFT_BLACK
#define TFT_BLACK 0x0000
#define TFT_RED 0xF800
#define TFT_ORANGE 0xFD20
#define TFT_GREEN 0x07E0
#define TFT_WHITE 0xFFFF
#define TFT_DARKGRAY 0x7BEF
#define top_left 0
#endif

void HapsicController::update_display() {
#ifdef USE_ESP32
  if (!M5StamPLC_ptr)
    return;
  uint8_t current_state = fsm_state_;
  M5StamPLC.Display.fillScreen(TFT_BLACK);

  // Top Bar (Status)
  M5StamPLC.Display.setTextDatum(top_left);
  M5StamPLC.Display.setCursor(5, 5);

  if (fsm_state_ == FAULT || fsm_state_ == MAINTENANCE_LOCKOUT) {
    M5StamPLC.Display.setTextColor(TFT_RED, TFT_BLACK);
    M5StamPLC.Display.printf("STATE: %s", state_name(fsm_state_));
    M5StamPLC.Display.setCursor(5, 20);
    M5StamPLC.Display.printf("REASON: %s", fault_reason_.c_str());
  } else if (fsm_state_ == HYGIENIC_PURGE || fsm_state_ == ACTIVE_TURBO) {
    M5StamPLC.Display.setTextColor(TFT_ORANGE, TFT_BLACK);
    M5StamPLC.Display.printf("STATE: %s", state_name(fsm_state_));
  } else {
    M5StamPLC.Display.setTextColor(TFT_GREEN, TFT_BLACK);
    M5StamPLC.Display.printf("STATE: %s", state_name(fsm_state_));
  }

  // Telemetry (Temps and DP)
  M5StamPLC.Display.printf("Duct: %.1f C | RH: %.1f%%", duct_temp_, duct_rh_);
  M5StamPLC.Display.setCursor(5, 60);
  M5StamPLC.Display.printf("Room DP: %.1f C (Tgt: %.1f C)", room_dp_, target_room_dp_);

  M5StamPLC.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5StamPLC.Display.setCursor(5, 45);
  M5StamPLC.Display.printf("Duct: %.1f C | RH: %.1f%%", duct_temp_, duct_rh_);

  M5StamPLC.Display.setCursor(5, 60);
  M5StamPLC.Display.printf("Stm: %.1f V | Fan: %.1f V", steam_voltage_, fan_voltage_);

  // Diagnostics
  M5StamPLC.Display.setTextColor(TFT_DARKGRAY);
  M5StamPLC.Display.setCursor(5, 105);
  M5StamPLC.Display.printf("UP: %.0fs", millis() / 1000.0f);

  // Frame push removed since we are drawing directly to the hardware Display
#endif
}

void HapsicController::update_buttons() {
#ifdef USE_ESP32
  M5StamPLC.update();  // Update physical button states

  // BtnA: STOP ALL (Emergency Hard Stop)
  if (M5StamPLC.BtnA.pressedFor(1000)) {
    if (fsm_state_ != FAULT) {
      ESP_LOGE("hapsic", "USER BUTTON E-STOP TRIGGERED!");
      fsm_state_ = FAULT;
      fault_reason_ = "USER_E_STOP";
      fault_clear_ticks_ = 999999;  // Requires manual reset
      force_safe_outputs();
      write_output();
    }
  }
  // BtnB: Soft STOP (Standby)
  else if (M5StamPLC.BtnB.wasClicked()) {
    if (fsm_state_ != FAULT && fsm_state_ != STANDBY) {
      ESP_LOGW("hapsic", "USER BUTTON SOFT STOP TRIGGERED!");
      fsm_state_ = STANDBY;
      reset_control_state();
      write_output();
    }
  }
#endif
}

}  // namespace hapsic
}  // namespace esphome
