#pragma once

#include "esphome/components/number/number.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include <ctime>
#include <fstream>
#include <map>
#include <sstream>
#include <string>

namespace esphome {
namespace csv_player {

class CSVPlayer : public Component {
public:
  void set_file(const std::string &filename) { filename_ = filename; }
  void set_speed(float speed) { speed_ = speed; }

  // Mappings
  void add_sensor_mapping(const std::string &entity_id,
                          sensor::Sensor *sensor) {
    sensor_map_[entity_id] = sensor;
  }

  void add_number_mapping(const std::string &entity_id,
                          number::Number *number) {
    number_map_[entity_id] = number;
  }

  void setup() override {
    file_.open(filename_);
    if (!file_.is_open()) {
      ESP_LOGE("csv_player", "Could not open file %s", filename_.c_str());
      this->mark_failed();
      return;
    }
    // Skip header
    std::string line;
    std::getline(file_, line);
    start_time_ms_ = esphome::millis();
    ESP_LOGI("csv_player", "CSV Player started. Reading %s", filename_.c_str());
  }

  void loop() override {
    if (!file_.is_open())
      return;

    while (true) {
      if (next_line_.empty()) {
        if (!std::getline(file_, next_line_)) {
          ESP_LOGI("csv_player", "End of CSV file");
          file_.close();
          return;
        }
      }

      // Parse next_line_
      std::stringstream ss(next_line_);
      std::string entity_id, state_str, time_str;

      std::getline(ss, entity_id, ',');
      std::getline(ss, state_str, ',');
      std::getline(ss, time_str, ',');

      if (!time_str.empty() && time_str.back() == '\r') {
        time_str.pop_back();
      }

      long long timestamp_ms = parse_iso8601(time_str);

      if (first_timestamp_ < 0) {
        first_timestamp_ = timestamp_ms;
        ESP_LOGI("csv_player", "First timestamp: %s -> %lld", time_str.c_str(),
                 timestamp_ms);
      }

      long long relative_ms = (timestamp_ms - first_timestamp_) / speed_;
      long long now = esphome::millis() - start_time_ms_;

      if (now >= relative_ms) {
        // Process
        float val = 0.0;
        char *endptr = nullptr;
        val = strtof(state_str.c_str(), &endptr);
        if (endptr == state_str.c_str()) {
          ESP_LOGW("csv_player", "Failed to parse float: %s",
                   state_str.c_str());
          next_line_.clear();
          continue;
        }

        if (sensor_map_.count(entity_id)) {
          sensor_map_[entity_id]->publish_state(val);
        } else if (number_map_.count(entity_id)) {
          number_map_[entity_id]->publish_state(val);
        }

        next_line_.clear(); // Line consumed
      } else {
        return;
      }
    }
  }

protected:
  std::string filename_;
  std::ifstream file_;
  std::string next_line_;
  float speed_ = 1.0;
  long long first_timestamp_ = -1;
  uint32_t start_time_ms_ = 0;

  std::map<std::string, sensor::Sensor *> sensor_map_;
  std::map<std::string, number::Number *> number_map_;

  long long parse_iso8601(const std::string &s) {
    struct tm tm = {};
    int year, month, day, hour, min, sec, ms = 0;
    if (sscanf(s.c_str(), "%d-%d-%dT%d:%d:%d.%dZ", &year, &month, &day, &hour,
               &min, &sec, &ms) < 6) {
      ESP_LOGW("csv_player", "Failed to parse time: %s", s.c_str());
      return 0;
    }
    tm.tm_year = year - 1900;
    tm.tm_mon = month - 1;
    tm.tm_mday = day;
    tm.tm_hour = hour;
    tm.tm_min = min;
    tm.tm_sec = sec;
    tm.tm_isdst = -1;

    time_t t = mktime(&tm);
    return (long long)t * 1000 + ms;
  }
};

} // namespace csv_player
} // namespace esphome
