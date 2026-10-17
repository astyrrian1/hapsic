// Exercise the actual compiled controller with and without an independent observer.
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
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
class Output : public esphome::output::FloatOutput {
 public:
  std::vector<float> calls;

 protected:
  void write_state(float value) override { calls.push_back(value); }
};
int main() {
  using namespace esphome;
  host::setup_preferences();
  hapsic::HapsicController plain, observed;
  sensor::Sensor temp, rh, flow, outdoor_temp, outdoor_rh, target, capacity;
  temp.state = 20;
  rh.state = 30;
  flow.state = 400;
  outdoor_temp.state = 5;
  outdoor_rh.state = 40;
  target.state = 10;
  capacity.state = 2.27;
  Output steam_plain, steam_observed, fan_plain, fan_observed;
  for (auto *c : {&plain, &observed}) {
    c->set_duct_temp_sensor(&temp);
    c->set_duct_rh_sensor(&rh);
    c->set_supply_flow_sensor(&flow);
    c->set_extract_flow_sensor(&flow);
    c->set_outdoor_sensors(&outdoor_temp, &outdoor_rh);
    c->set_house_sensors(&temp, &rh);
    c->set_supply_sensors_can(&temp, &rh);
    c->set_extract_sensors_can(&temp, &rh);
    c->set_target_dew_point_sensor(&target);
    c->set_max_capacity_sensor(&capacity);
    c->setup();
  }
  plain.set_steam_dac(&steam_plain);
  observed.set_steam_dac(&steam_observed);
  plain.set_fan_dac(&fan_plain);
  observed.set_fan_dac(&fan_observed);
  hapsic_diagnostics::Config config;
  config.verified = true;
  hapsic_diagnostics::Observer observer(config);
  bool saw_fault = false;
  for (unsigned tick = 0; tick < 2000; tick++) {
    test_ms += 5000;
    rh.state = tick % 250 < 120 ? 25 : 45;
    // Include missing safety-critical sensor input, recovery and demand changes.
    if (tick % 500 > 480)
      rh.state = NAN;
    target.state = tick % 400 < 300 ? 10 : -5;
    plain.update();
    observed.update();
    hapsic_diagnostics::Frame frame;
    double utc = test_ms / 1000.0;
    const double values[] = {20, 30, 20, 40, 400, 101325, observed.diagnostic_commanded_voltage()};
    for (unsigned i = 0; i < 7; i++)
      frame.samples[i] = {values[i], utc, tick + 1, i + 1, true, NAN};
    if (tick % 13 == 0)
      frame.samples[4].value = NAN;
    observer.observe(frame, utc, utc, tick % 17 != 0);
    if (tick % 19 == 0)
      assert(!observer.restore({1, 2, 3}));
    saw_fault = saw_fault || plain.fsm_state_ == hapsic::HapsicController::FAULT;
    assert(plain.fsm_state_ == observed.fsm_state_);
    assert(plain.steam_voltage_ == observed.steam_voltage_ && plain.fan_voltage_ == observed.fan_voltage_);
    assert(plain.chi_ema_ == observed.chi_ema_);
    assert(plain.integrator_a_ == observed.integrator_a_ && plain.integrator_b_ == observed.integrator_b_);
    assert(std::memcmp(plain.boiler_curve_, observed.boiler_curve_, sizeof(plain.boiler_curve_)) == 0);
    assert(std::memcmp(plain.boiler_curve_counts_, observed.boiler_curve_counts_, sizeof(plain.boiler_curve_counts_)) ==
           0);
  }
  assert(saw_fault);
  assert(!steam_plain.calls.empty() && *std::max_element(steam_plain.calls.begin(), steam_plain.calls.end()) > 0);
  assert(steam_plain.calls == steam_observed.calls && fan_plain.calls == fan_observed.calls);
  std::cout
      << "2000 actual native controller ticks: actuator calls, FSM, integrators, CHI and learned curves identical\n";
}
