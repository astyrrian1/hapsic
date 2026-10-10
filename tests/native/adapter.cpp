#include "host_doubles.h"
#include <iomanip>
#include <iostream>
#include <sstream>
// Isolated test-only access. Standard/platform headers are loaded first. The
// Controller source is compiled unchanged; private access exposes snapshots and
// explicit fault injection, never synthesized state alignment between engines.
#define private public
#include "components/hapsic/hapsic.cpp"
#undef private

using esphome::hapsic::HapsicController;
using esphome::hapsic::MagnusTetens;

int main() {
  HapsicController controller;
  esphome::output::FloatOutput steam, fan;
  std::map<std::string, esphome::sensor::Sensor> sensors;
  controller.set_steam_dac(&steam);
  controller.set_fan_dac(&fan);
  controller.set_duct_temp_sensor(&sensors["duct_temp"]);
  controller.set_duct_rh_sensor(&sensors["duct_rh"]);
  controller.set_supply_flow_sensor(&sensors["supply_flow"]);
  controller.set_extract_flow_sensor(&sensors["extract_flow"]);
  controller.set_bypass_sensor(&sensors["bypass"]);
  controller.set_outdoor_sensors(&sensors["outdoor_temp"], &sensors["outdoor_rh"]);
  controller.set_house_sensors(&sensors["house_temp"], &sensors["house_rh"]);
  controller.set_extract_sensors_can(&sensors["extract_temp"], &sensors["extract_rh"]);
  controller.set_supply_sensors_can(&sensors["supply_temp"], &sensors["supply_rh"]);
  controller.set_target_dew_point_sensor(&sensors["target_dp"]);
  controller.set_max_capacity_sensor(&sensors["max_capacity"]);
  std::cout << std::setprecision(9) << std::boolalpha;
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream input(line);
    std::string command;
    input >> command;
    if (command == "setup") {
      controller.setup();
    } else if (command == "fault") {
      controller.trigger_fault("injected hazard");
    } else if (command == "clock" || command == "tick") {
      uint32_t now;
      if (!(input >> now))
        return 2;
      esphome::host_now_ms = now;
      if (command == "tick")
        controller.update();
    } else if (command == "sensor") {
      std::string name, value;
      if (!(input >> name >> value) || !sensors.count(name))
        return 2;
      sensors.at(name).publish_state(std::stof(value));
    } else if (command == "magnus") {
      float temperature, rh, pressure;
      if (!(input >> temperature >> rh >> pressure))
        return 2;
      auto result = MagnusTetens::calculate(temperature, rh, pressure);
      std::cout << "{\"valid\":" << result.is_valid << ",\"dew_point_c\":" << result.dew_point_c
                << ",\"mixing_ratio_g_kg\":" << result.mixing_ratio_g_kg << "}\n";
    } else if (command == "snapshot") {
      std::cout << "{\"now_ms\":" << esphome::millis() << ",\"tick\":" << controller.tick_counter_
                << ",\"dt\":" << controller.dt_ << ",\"state\":\"" << controller.state_name(controller.fsm_state_)
                << "\""
                << ",\"steam_level\":" << steam.level << ",\"fan_level\":" << fan.level
                << ",\"steam_writes\":" << steam.writes.size() << ",\"fan_writes\":" << fan.writes.size()
                << ",\"first_steam_write_ms\":" << (steam.write_times.empty() ? -1 : int64_t(steam.write_times.front()))
                << ",\"duct_temp_c\":" << controller.duct_temp_ << ",\"duct_rh\":" << controller.duct_rh_ << "}\n";
    } else if (!command.empty()) {
      std::cerr << "Unknown command: " << command << '\n';
      return 2;
    }
  }
}
