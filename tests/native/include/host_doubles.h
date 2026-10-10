#pragma once
// Only ESPHome/platform boundaries are doubled; controller math is compiled unchanged.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <string>
#include <vector>
namespace esphome {
inline uint32_t host_now_ms = 0;
inline uint32_t millis() {
  return host_now_ms;
}
inline void delay(uint32_t elapsed) {
  host_now_ms += elapsed;
}
inline uint32_t fnv1_hash(const char *text) {
  uint32_t hash = 2166136261u;
  while (*text) {
    hash *= 16777619u;
    hash ^= static_cast<uint8_t>(*text++);
  }
  return hash;
}
class Component {
 public:
  virtual ~Component() = default;
  virtual void setup() {}
  virtual void update() {}
};
class PollingComponent : public Component {
 public:
  explicit PollingComponent(uint32_t) {}
};
// Key AND byte size must match. Different NVS keys never alias each other.
inline std::map<uint32_t, std::vector<uint8_t>> host_nvs;
class ESPPreferenceObject {
 public:
  explicit ESPPreferenceObject(uint32_t key = 0) : key_(key) {}
  template <class T>
  bool load(T *value) {
    auto found = host_nvs.find(key_);
    if (found == host_nvs.end() || found->second.size() != sizeof(T))
      return false;
    std::memcpy(value, found->second.data(), sizeof(T));
    return true;
  }
  template <class T>
  bool save(const T *value) {
    auto &bytes = host_nvs[key_];
    bytes.resize(sizeof(T));
    std::memcpy(bytes.data(), value, sizeof(T));
    return true;
  }

 private:
  uint32_t key_;
};
class Preferences {
 public:
  template <class T>
  ESPPreferenceObject make_preference(uint32_t key) {
    return ESPPreferenceObject(key);
  }
};
inline Preferences host_preferences;
inline Preferences *global_preferences = &host_preferences;
namespace sensor {
class Sensor {
 public:
  float state = NAN;
  void publish_state(float v) { state = v; }
};
}  // namespace sensor
namespace number {
class Number {
 public:
  float state = NAN;
};
}  // namespace number
namespace binary_sensor {
class BinarySensor {
 public:
  bool state = false;
  void publish_state(bool v) { state = v; }
};
}  // namespace binary_sensor
namespace text_sensor {
class TextSensor {
 public:
  std::string state;
  void publish_state(const std::string &v) { state = v; }
};
}  // namespace text_sensor
namespace output {
class FloatOutput {
 public:
  float level = NAN;
  std::vector<float> writes;
  void set_level(float v) {
    level = v;
    writes.push_back(v);
  }
};
}  // namespace output
namespace button {
class Button {
 public:
  std::vector<std::function<void()>> callbacks;
  template <class T>
  void add_on_press_callback(T callback) {
    callbacks.emplace_back(callback);
  }
  void press() {
    for (const auto &callback : callbacks)
      callback();
  }
};
}  // namespace button
namespace switch_ {
class Switch {};
}  // namespace switch_
}  // namespace esphome
#define ESP_LOGI(...) \
  do {                \
  } while (0)
#define ESP_LOGW(...) \
  do {                \
  } while (0)
#define ESP_LOGE(...) \
  do {                \
  } while (0)
#define ESP_LOGD(...) \
  do {                \
  } while (0)
