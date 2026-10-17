// Exercise the actual compiled controller with and without an independent observer.
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <map>
#include <vector>
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/core/preferences.h"
#include "esphome/components/host/preferences.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/output/float_output.h"
#define private public
#include "esphome/components/hapsic/hapsic.h"
#undef private
#include "components/hapsic_diagnostics/observer.h"

static uint32_t test_ms = 1000000;
namespace esphome {
void yield() {}
uint32_t millis() {
  return test_ms;
}
void delay(uint32_t ms) {
  test_ms += ms;
}
uint32_t micros() {
  return test_ms * 1000U;
}
void delayMicroseconds(uint32_t us) {
  test_ms += us / 1000U;
}
void arch_restart() {
  std::abort();
}
void arch_init() {}
void arch_feed_wdt() {}
uint8_t progmem_read_byte(const uint8_t *addr) {
  return *addr;
}
uint32_t arch_get_cpu_cycle_count() {
  return micros();
}
uint32_t arch_get_cpu_freq_hz() {
  return 1000000;
}
}  // namespace esphome

class MemoryPreferences : public esphome::ESPPreferences {
 public:
  std::map<uint32_t, std::vector<uint8_t>> pending, durable;
  bool fail = false;
  class Backend : public esphome::ESPPreferenceBackend {
   public:
    MemoryPreferences *owner;
    uint32_t key;
    Backend(MemoryPreferences *p, uint32_t k) : owner(p), key(k) {}
    bool save(const uint8_t *data, size_t len) override {
      if (owner->fail)
        return false;
      owner->pending[key] = std::vector<uint8_t>(data, data + len);
      return true;
    }
    bool load(uint8_t *data, size_t len) override {
      auto &m = owner->pending.count(key) ? owner->pending : owner->durable;
      auto it = m.find(key);
      if (it == m.end() || it->second.size() != len)
        return false;
      std::copy(it->second.begin(), it->second.end(), data);
      return true;
    }
  };
  esphome::ESPPreferenceObject make_preference(size_t, uint32_t key, bool) override {
    return esphome::ESPPreferenceObject(new Backend(this, key));
  }
  esphome::ESPPreferenceObject make_preference(size_t n, uint32_t key) override {
    return make_preference(n, key, false);
  }
  bool sync() override {
    if (fail)
      return false;
    for (auto &v : pending)
      durable[v.first] = v.second;
    pending.clear();
    return true;
  }
  bool reset() override {
    pending.clear();
    durable.clear();
    return true;
  }
};
class CaptureOutput : public esphome::output::FloatOutput {
 public:
  float last = 0;

 protected:
  void write_state(float value) override {
    assert(std::isfinite(value));
    assert(value >= 0 && value <= 1.0f);
    last = value;
  }
};
int main(int argc, char **argv) {
  using namespace esphome;
  MemoryPreferences storage;
  global_preferences = &storage;
#ifndef LEGACY_BASELINE
  hapsic::HapsicController c;
  c.setup();
  c.fsm_state_ = hapsic::HapsicController::ACTIVE_CRUISE;
  c.steam_voltage_ = 9.5f;
  c.max_capacity_ = 4.61f * 0.453592f;
  c.supply_flow_ = 200;
  c.supply_w_ = 5;
  c.duct_w_ = 10;
  c.room_w_ = 8;
  c.outdoor_w_ = 3;
  c.boil_achieved_ = true;
  c.active_cruise_ticks_ = 0;
  c.run_diagnostics();
  float expected = 200 * .5886f * 60 * .065f * .005f;
  assert(std::abs(c.last_measured_steam_ - expected) < .00001f);
  assert(std::abs(c.chi_instant_ - expected / (4.61f * .95f)) < .00001f);
  for (int n = 1; n < 49; ++n)
    c.run_diagnostics();
  assert(c.boiler_curve_counts_[3] == 49);
  assert(std::abs(c.get_effective_max_capacity() - 4.61f * c.chi_ema_) < .00001f);
  c.run_diagnostics();
  assert(c.boiler_curve_counts_[3] == 50);
  assert(std::abs(c.boiler_curve_[3] - expected) < .00001f);
  assert(std::abs(c.get_effective_max_capacity() - expected) < .00001f);
  // Each native gate is evaluated by the real implementation, without shared flags.
  hapsic::HapsicController gates;
  gates.max_capacity_ = c.max_capacity_;
  gates.supply_flow_ = c.supply_flow_;
  gates.supply_w_ = c.supply_w_;
  gates.duct_w_ = c.duct_w_;
  gates.steam_voltage_ = 9.5f;
  gates.run_diagnostics();
  assert(!gates.chi_eligible_);  // Cold, even with positive moisture difference.
  gates.boil_achieved_ = true;
  gates.steam_voltage_ = 1.0f;
  gates.max_capacity_ = 10 * .453592f;
  gates.run_diagnostics();
  assert(!gates.chi_eligible_);  // Exactly the command threshold, with adequate theory.
  gates.steam_voltage_ = 1.01f;
  gates.run_diagnostics();
  assert(gates.chi_eligible_);
  gates.steam_voltage_ = 9.5f;
  gates.max_capacity_ = (99.99f * 60 / 7000) / .95f * .453592f;
  gates.run_diagnostics();
  assert(!gates.chi_eligible_);
  gates.max_capacity_ = (100.01f * 60 / 7000) / .95f * .453592f;
  gates.run_diagnostics();
  assert(gates.chi_eligible_);
  gates.stasis_active_ = true;
  gates.run_diagnostics();
  assert(!gates.chi_eligible_);
  gates.stasis_active_ = false;
  gates.supply_flow_ = 0;
  gates.run_diagnostics();
  assert(!gates.chi_eligible_);
  c.checkpoint_learning();
  storage.pending.clear();  // Abrupt loss before framework flush.
  hapsic::HapsicController lost;
  lost.restore_learning();
  assert(lost.boiler_curve_counts_[3] == 0);
  c.checkpoint_learning();
  assert(storage.sync());
  hapsic::HapsicController restored;
  restored.restore_learning();
  assert(restored.boiler_curve_counts_[3] == 50 && restored.boiler_curve_[3] == c.boiler_curve_[3]);
  storage.fail = true;
  c.checkpoint_learning();
  assert(c.boiler_curve_counts_[3] == 50);
  storage.fail = false;
  sensor::Sensor api_chi, api_mass, api_efficiency, api_theoretical;
  c.set_tel_health_chi_instant(&api_chi);
  c.set_tel_health_measured_steam(&api_mass);
  c.set_tel_health_production_efficiency(&api_efficiency);
  c.set_tel_io_steam_mass_lbs(&api_theoretical);
  c.publish_telemetry();
  assert(std::abs(api_mass.state - expected) < .00001f);
  assert(std::abs(api_theoretical.state - 4.61f * .95f) < .00001f);
  assert(std::abs(api_chi.state - expected / (4.61f * .95f)) < .00001f);
  assert(std::abs(api_efficiency.state - expected / (4.61f * .95f) * 100) < .0001f);
  c.stasis_active_ = true;
  c.run_diagnostics();
  assert(!c.chi_eligible_ && c.boiler_curve_counts_[3] == 50);
  c.publish_telemetry();
  assert(std::isnan(api_efficiency.state) && std::isnan(api_mass.state) && std::isnan(api_chi.state));
  c.stasis_active_ = false;
  c.duct_w_ = 4;
  c.run_diagnostics();
  assert(c.chi_eligible_ && c.chi_instant_ == 0 && c.last_measured_steam_ < 0 && c.boiler_curve_counts_[3] == 50);
  c.boiler_curve_counts_[3] = UINT32_MAX;
  c.duct_w_ = 10;
  c.run_diagnostics();
  assert(c.boiler_curve_counts_[3] == UINT32_MAX);
  storage.reset();
  auto legacy = global_preferences->make_preference<hapsic::HapsicPersist>(fnv1_hash("hapsic_state"));
  hapsic::HapsicPersist old{};
  old.chi_ema = .3f;
  old.cached_target_rh = 9;
  old.magic = 0xABCD1235;
  old.boiler_curve[3] = .01f;
  assert(legacy.save(&old));
  assert(storage.sync());
  hapsic::HapsicController migrated;
  migrated.restore_learning();
  assert(migrated.chi_ema_ == 1 && migrated.boiler_curve_counts_[3] == 0 && migrated.cached_target_dp_ == 9);
  hapsic::HapsicPersist preserved{};
  assert(legacy.load(&preserved) && preserved.boiler_curve[3] == .01f);
  auto corrupt = global_preferences->make_preference<hapsic::HapsicLearningV2>(fnv1_hash("hapsic_learning_v2"));
  hapsic::HapsicLearningV2 bad;
  bad.curve[3] = NAN;
  bad.counts[3] = 50;
  assert(corrupt.save(&bad));
  assert(storage.sync());
  hapsic::HapsicController rejected;
  rejected.restore_learning();
  assert(rejected.boiler_curve_counts_[3] == 0 && rejected.chi_ema_ == 1);
  c.steam_voltage_ = 9.49f;
  c.upward_rate_ticks_ = 11;
  c.integrator_b_ = 0;
  c.target_duct_dp_ = 20;
  c.duct_dp_ = 20;
  c.duct_rh_ = 60;
  c.boil_achieved_ = true;
  c.stasis_active_ = false;
  c.execute_loop_b();
  assert(c.steam_voltage_ <= 9.5f && c.steam_voltage_ >= 0);
  c.steam_voltage_ = 9.49f;
  c.upward_rate_ticks_ = 11;
  c.duct_rh_ = 86;
  c.execute_loop_b();
  assert(c.steam_voltage_ == 0);  // Ceiling below min-fire: slew must not restore steam.
  c.steam_voltage_ = 9.5f;
  c.duct_rh_ = 60;
  c.run_diagnostics();
  if (argc > 1 && std::string(argv[1]) == "nonfinite") {
    c.vent_loss_ = NAN;
    c.net_flux_ = INFINITY;
    c.chi_instant_ = NAN;
    c.boiler_curve_[0] = INFINITY;
    std::cout << c.legacy_telemetry_json() << std::endl;
    return 0;
  }
  if (argc > 1 && std::string(argv[1]) == "overflow") {
    c.fault_reason_ = std::string(4000, 'x');
    std::cout << c.legacy_telemetry_json() << std::endl;
    return 0;
  }
  if (argc == 1) {
    std::cout << c.legacy_telemetry_json() << std::endl;
    return 0;
  }
#endif
  storage.reset();
  hapsic::HapsicController actual;
  sensor::Sensor duct_t, duct_rh, flow, extract, room_t, room_rh, sup_t, sup_rh, out_t, out_rh, target, capacity,
      bypass;
  actual.set_duct_temp_sensor(&duct_t);
  actual.set_duct_rh_sensor(&duct_rh);
  actual.set_supply_flow_sensor(&flow);
  actual.set_extract_flow_sensor(&extract);
  actual.set_house_sensors(&room_t, &room_rh);
  actual.set_supply_sensors_can(&sup_t, &sup_rh);
  actual.set_extract_sensors_can(&room_t, &room_rh);
  actual.set_outdoor_sensors(&out_t, &out_rh);
  actual.set_bypass_sensor(&bypass);
  actual.set_target_dew_point_sensor(&target);
  actual.set_max_capacity_sensor(&capacity);
  actual.setup();
  CaptureOutput steam, fan;
  actual.set_steam_dac(&steam);
  actual.set_fan_dac(&fan);
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream row(line);
    char mode;
    double stamp;
    float command;
    int boil, stasis;
    float dt, dr, sf, ef, rt, rr, st, sr, ot, orr, tg, cap, bp;
    if (!(row >> mode >> stamp))
      return 2;
    for (float *v : {&dt, &dr, &sf, &ef, &rt, &rr, &st, &sr, &ot, &orr, &tg, &cap, &bp, &command}) {
      std::string token;
      if (!(row >> token))
        return 2;
      *v = std::stof(token);
    }
    if (!(row >> boil >> stasis))
      return 2;
    test_ms = static_cast<uint32_t>(stamp * 1000);
    duct_t.state = dt;
    duct_rh.state = dr;
    flow.state = sf;
    extract.state = ef;
    room_t.state = rt;
    room_rh.state = rr;
    sup_t.state = st;
    sup_rh.state = sr;
    out_t.state = ot;
    out_rh.state = orr;
    target.state = tg;
    capacity.state = cap;
    bypass.state = bp;
    if (mode == 'F')
      actual.update();
    else {
      actual.read_sensors();
      actual.steam_voltage_ = command;
      actual.boil_achieved_ = boil;
      actual.stasis_active_ = stasis;
      if (mode == 'M') {
        row >> actual.supply_w_ >> actual.duct_w_ >> actual.room_w_ >> actual.outdoor_w_ >> actual.supply_flow_;
        actual.active_cruise_ticks_ = 181;  // Arithmetic comparison, not an eligibility reconstruction.
      }
      actual.run_diagnostics();
    }
#ifndef LEGACY_BASELINE
    std::string payload = actual.legacy_telemetry_json();
#else
    auto number = [](float v) { return std::isfinite(v) ? std::to_string(v) : std::string("null"); };
    std::string payload = "{\"fsm\":{\"state\":\"" + std::string(actual.state_name(actual.fsm_state_)) +
                          "\",\"fault_reason\":\"" + actual.fault_reason_ + "\"}," +
                          "\"limiters\":{\"active_limit\":\"" + actual.active_limit_ + "\"}," +
                          "\"io\":{\"volts_out\":" + number(actual.steam_voltage_) +
                          "},\"health\":{\"chi_ema\":" + number(actual.chi_ema_) + "}," +
                          "\"loop_b\":{\"v_ff\":" + number(actual.v_ff_) + "}}";
#endif
    std::cout << payload << std::endl;
  }
}
