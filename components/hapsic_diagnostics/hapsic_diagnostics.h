#pragma once

#include "observer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "esp_heap_caps.h"
#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "esphome/core/preferences.h"
#include "esphome/components/hapsic/hapsic.h"
#include "esphome/components/json/json_util.h"
#include "esphome/components/mqtt/mqtt_client.h"
#include "esphome/components/time/real_time_clock.h"

namespace esphome {
namespace hapsic_diagnostics {

class DiagnosticComponent : public PollingComponent {
 public:
  void set_controller(const hapsic::HapsicController *controller) { controller_ = controller; }
  void set_clock(time::RealTimeClock *clock) { clock_ = clock; }
  void set_identity(const std::string &producer, const std::string &topic) {
    producer_ = producer;
    topic_ = topic;
  }
  void set_config(bool verified, double warning, double recovery, uint32_t signature) {
    Config config;
    config.verified = verified;
    config.warning = warning;
    config.recovery = recovery;
    config.signature = signature;
    observer_ = Observer(config);
  }
  void set_inputs(const std::string &topic, const std::string &producer, bool remote_command) {
    input_topic_ = topic;
    input_producer_ = producer;
    remote_command_ = remote_command;
  }
  void set_source(unsigned index, const std::string &identity, bool verified) {
    identities_[index] = identity;
    verified_[index] = verified;
  }
  void setup() override {
    boot_ = str_sprintf("%08x%08x", random_uint32(), random_uint32());
    storage_open_ =
        nvs_flash_init_partition("hapsic_diag") == ESP_OK &&
        nvs_open_from_partition(
            "hapsic_diag",
            str_sprintf("hd%08x", fnv1_hash(producer_ + std::to_string(observer_.config.signature))).c_str(),
            NVS_READWRITE, &storage_) == ESP_OK;
    Manifest manifest{};
    size_t stored_size = 0;
    esp_err_t stored = storage_open_ ? nvs_get_blob(storage_, "manifest", nullptr, &stored_size) : ESP_FAIL;
    storage_error_ = !storage_open_ || (stored != ESP_OK && stored != ESP_ERR_NVS_NOT_FOUND);
    if (stored == ESP_OK && (!load_record("manifest", manifest) || manifest.magic != MAGIC ||
                             manifest.size > MAX_BYTES || manifest.request_count > 64 ||
                             std::any_of(manifest.requests.begin(), manifest.requests.end(), [](const auto &request) {
                               auto end = std::find(request.begin(), request.end(), '\0');
                               return end == request.end() ||
                                      !std::all_of(end, request.end(), [](char byte) { return byte == 0; });
                             })))
      storage_error_ = true;
    else if (stored == ESP_OK) {
      std::vector<uint8_t> bytes;
      bytes.reserve(manifest.size);
      bool valid = true;
      for (unsigned i = 0; i < (manifest.size + CHUNK_BYTES - 1) / CHUNK_BYTES; i++) {
        Chunk chunk{};
        if (!load_record(chunk_key(manifest.slot, i), chunk) || chunk.transaction != manifest.transaction) {
          valid = false;
          break;
        }
        unsigned count = std::min<unsigned>(CHUNK_BYTES, manifest.size - bytes.size());
        bytes.insert(bytes.end(), chunk.data.begin(), chunk.data.begin() + count);
      }
      if (valid && checksum(bytes) == manifest.checksum && observer_.restore(bytes)) {
        manifest_ = manifest;
      } else
        storage_error_ = true;
    }
    mqtt::global_mqtt_client->subscribe(input_topic_, [this](const std::string &, const std::string &payload) {
      if (payload.size() > 8192 || heap_caps_get_free_size(MALLOC_CAP_8BIT) < 65536)
        return;
      json::parse_json(payload, [this](JsonObject root) { return read_inputs(root); });
    });
    mqtt::global_mqtt_client->subscribe(topic_ + "/command", [this](const std::string &, const std::string &payload) {
      if (payload.size() > 1024 || pending_ || heap_caps_get_free_size(MALLOC_CAP_8BIT) < 65536)
        return;
      json::parse_json(payload, [this](JsonObject root) {
        if (!root["schema_version"].is<int>() || root["schema_version"].as<int>() != 1 ||
            root["producer_id"].as<std::string>() != producer_ || !root["request_id"].is<const char *>() ||
            !root["action"].is<const char *>())
          return false;
        std::string request = root["request_id"].as<std::string>();
        if (!valid_request_id(request))
          return false;
        for (unsigned index = 0; index < manifest_.request_count; index++)
          if (request == manifest_.requests[index].data()) {
            acknowledge(request, true);
            return true;
          }
        auto utc = clock_->now();
        double issued = root["issued_at"].as<double>();
        if (!utc.is_valid() || !root["issued_at"].is<double>() || utc.timestamp - issued > 30 ||
            issued - utc.timestamp > 5)
          return false;
        if (!allocation_headroom())
          return false;
        auto replacement = std::unique_ptr<Observer>(new Observer(observer_));
        if (!replacement->action(root["action"].as<std::string>(), root["revision"].as<uint32_t>(),
                                 (root["healthy"].is<bool>() && root["healthy"].as<bool>())))
          return false;
        request_ = request;

        auto bytes = replacement->save();
        if (begin_save(std::move(bytes), std::move(replacement)))
          observer_.interrupt("STORAGE_PENDING");
        else
          acknowledge(request, false);
        return true;
      });
    });
  }
  void loop() override {
    uint32_t current = millis();
    monotonic_ms_ += uint32_t(current - last_ms_);
    last_ms_ = current;
    if (!remote_command_)
      command_event(controller_->diagnostic_commanded_voltage(), controller_->diagnostic_faulted());
    if (!pending_)
      return;
    // One chunk per loop; never writes from the controller update path.
    if (next_chunk_ * CHUNK_BYTES < bytes_.size()) {
      Chunk chunk{};
      chunk.transaction = next_manifest_.transaction;
      size_t begin = next_chunk_ * CHUNK_BYTES, count = std::min<size_t>(CHUNK_BYTES, bytes_.size() - begin);
      std::copy_n(bytes_.begin() + begin, count, chunk.data.begin());
      if (!save_record(chunk_key(next_manifest_.slot, next_chunk_++), chunk))
        finish_save(false);
    } else {
      bool ok = save_record("manifest", next_manifest_);
      finish_save(ok);
    }
  }
  void update() override {
    double mono = monotonic_ms_ / 1000.0;
    auto stamp = clock_->now();
    double utc = stamp.is_valid() ? stamp.timestamp : NAN;
    if (!remote_command_) {
      double voltage = controller_->diagnostic_commanded_voltage();
      frame_.samples[6] = Sample{voltage, utc, ++command_report_, fnv1_hash(producer_ + "/command"), true, NAN};
      frame_.fault = controller_->diagnostic_faulted() || command_interrupted_;
    } else
      frame_.fault = remote_fault_ || command_interrupted_;
    frame_.command_uncertain = frame_.command_uncertain || !std::isfinite(last_input_) || mono - last_input_ > 30;
    command_interrupted_ = false;
    if (heap_caps_get_free_size(MALLOC_CAP_8BIT) < 65536)
      observer_.interrupt("CAPACITY_EXHAUSTED");
    else if (!replacement_)
      observer_.observe(frame_, mono, utc, stamp.is_valid());
    if (!pending_ && mono - last_checkpoint_ >= 300) {
      if (allocation_headroom())
        begin_save(observer_.save(), nullptr);
      else
        storage_error_ = true;
      last_checkpoint_ = mono;
    }
    std::string payload = observer_.json(utc);
    payload.pop_back();
    payload += ",\"producer_id\":\"" + producer_ + "\",\"boot_id\":\"" + boot_ +
               "\",\"sequence\":" + std::to_string(++sequence_) +
               ",\"published_at\":" + (std::isfinite(utc) ? std::to_string(utc) : "null") +
               ",\"storage_error\":" + (storage_error_ ? "\"STORAGE_ERROR\"" : "null") + "}";
    mqtt::global_mqtt_client->publish(topic_, payload, 0, false);
  }

 protected:
  static constexpr uint32_t MAGIC = 0x48444D31;
  static constexpr unsigned CHUNK_BYTES = 1000, MAX_BYTES = 196608;
  struct Chunk {
    uint32_t transaction{0};
    std::array<uint8_t, CHUNK_BYTES> data{};
  };
  struct Manifest {
    uint32_t magic{MAGIC}, transaction{0}, size{0}, checksum{0}, slot{0}, request_index{0};
    uint32_t request_count{0};
    std::array<std::array<char, 129>, 64> requests{};
  };
  Observer observer_;
  Frame frame_;
  const hapsic::HapsicController *controller_{nullptr};
  time::RealTimeClock *clock_{nullptr};
  std::array<std::string, 6> identities_{};
  std::array<bool, 6> verified_{};

  std::string producer_, topic_, boot_, request_, input_topic_, input_producer_, input_boot_;
  uint64_t input_sequence_{0};
  double last_input_{NAN}, last_input_publication_{0};
  bool remote_command_{false}, remote_fault_{false}, command_interrupted_{false};
  uint64_t monotonic_ms_{0}, command_report_{0}, sequence_{0};
  uint32_t last_ms_{0};
  double last_voltage_{NAN}, last_checkpoint_{0};
  bool pending_{false}, storage_error_{false};
  unsigned next_chunk_{0};
  std::vector<uint8_t> bytes_;
  std::unique_ptr<Observer> replacement_;
  Manifest manifest_{}, next_manifest_{};
  nvs_handle_t storage_{0};
  bool storage_open_{false};

  bool allocation_headroom() const {
    size_t representation =
        sizeof(Observer) + sizeof(Archive) * observer_.archives.size() + sizeof(Row) * observer_.window.size() +
        sizeof(Block) * (observer_.evidence.size() + observer_.quarantine.size() + observer_.delivery_window.size());
    // Clone + growing serialized vector + allocator/deque overhead coexist.
    return heap_caps_get_free_size(MALLOC_CAP_8BIT) > 4 * representation + 65536 &&
           heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) > 2 * representation;
  }
  static uint32_t checksum(const std::vector<uint8_t> &bytes) {
    uint32_t hash = 2166136261U;
    for (uint8_t byte : bytes) {
      hash ^= byte;
      hash *= 16777619U;
    }
    return hash;
  }
  static std::string chunk_key(unsigned slot, unsigned index) {
    return "d" + std::to_string(slot) + "_" + std::to_string(index);
  }
  // Dedicated preferences backend/namespace: committing diagnostic storage must
  // not flush control-owned pending preferences. IDF NVS set/commit contract:
  // https://docs.espressif.com/projects/esp-idf/en/v5.3.2/esp32s3/api-reference/storage/nvs_flash.html
  class Backend : public ESPPreferenceBackend {
   public:
    Backend(nvs_handle_t handle, const std::string &key) : handle_(handle), key_(key) {}
    bool save(const uint8_t *data, size_t length) override {
      return nvs_set_blob(handle_, key_.c_str(), data, length) == ESP_OK && nvs_commit(handle_) == ESP_OK;
    }
    bool load(uint8_t *data, size_t length) override {
      size_t actual = length;
      return nvs_get_blob(handle_, key_.c_str(), data, &actual) == ESP_OK && actual == length;
    }

   private:
    nvs_handle_t handle_;
    std::string key_;
  };
  template <class T>
  bool save_record(const std::string &key, const T &value) {
    if (!storage_open_)
      return false;
    Backend backend(storage_, key);
    ESPPreferenceObject pref(&backend);
    return pref.save(&value);
  }
  template <class T>
  bool load_record(const std::string &key, T &value) {
    if (!storage_open_)
      return false;
    Backend backend(storage_, key);
    ESPPreferenceObject pref(&backend);
    return pref.load(&value);
  }
  bool begin_save(std::vector<uint8_t> bytes, std::unique_ptr<Observer> replacement) {
    if (!storage_open_ || bytes.size() > MAX_BYTES) {
      storage_error_ = true;
      return false;
    }
    bytes_ = std::move(bytes);
    replacement_ = std::move(replacement);
    next_chunk_ = 0;
    pending_ = true;
    next_manifest_ = manifest_;
    next_manifest_.transaction++;
    next_manifest_.slot ^= 1;
    next_manifest_.size = bytes_.size();
    next_manifest_.checksum = checksum(bytes_);
    if (replacement_) {
      auto &slot = next_manifest_.requests[next_manifest_.request_index++ % 64];
      slot.fill(0);
      std::copy(request_.begin(), request_.end(), slot.begin());
      next_manifest_.request_count = std::min<uint32_t>(64, next_manifest_.request_count + 1);
    }
    return true;
  }
  void finish_save(bool ok) {
    storage_error_ = !ok;
    if (ok) {
      manifest_ = next_manifest_;
      if (replacement_)
        observer_ = std::move(*replacement_);
    }
    if (replacement_) {
      acknowledge(request_, ok);
    }
    pending_ = false;
    replacement_.reset();
    bytes_.clear();
    bytes_.shrink_to_fit();
  }
  void acknowledge(const std::string &request, bool ok) {
    mqtt::global_mqtt_client->publish_json(
        topic_ + "/ack",
        [this, &request, ok](JsonObject root) {
          root["schema_version"] = 1;
          root["producer_id"] = producer_;
          root["request_id"] = request;
          root["status"] = ok ? "accepted" : "rejected";
          if (!ok)
            root["reason"] = "STORAGE_ERROR";
        },
        0, false);
  }
  void command_event(double voltage, bool fault) {
    if (std::isfinite(last_voltage_) && (voltage != last_voltage_ || fault)) {
      double vmin = voltage, vmax = voltage;
      for (const auto &row : observer_.window) {
        vmin = std::min(vmin, row.m.voltage);
        vmax = std::max(vmax, row.m.voltage);
      }
      if (fault || voltage == 0 || last_voltage_ == 0 || vmax - vmin > observer_.config.command_range + 1e-9) {
        observer_.interrupt("COMMAND_EVENT");
        command_interrupted_ = true;
      }
    }
    last_voltage_ = voltage;
  }
  static uint64_t observation_id(const std::string &value) {
    uint64_t hash = 14695981039346656037ULL;
    for (unsigned char byte : value) {
      hash ^= byte;
      hash *= 1099511628211ULL;
    }
    return hash;
  }
  bool read_inputs(JsonObject root) {
    auto now = clock_->now();
    double published = root["published_at"].as<double>();
    std::string boot = root["boot_id"].as<std::string>();
    uint64_t sequence = root["sequence"].as<uint64_t>();
    if (!now.is_valid() || !root["schema_version"].is<int>() || root["schema_version"].as<int>() != 1 ||
        root["producer_id"].as<std::string>() != input_producer_ || boot.empty() ||
        !root["published_at"].is<double>() || !std::isfinite(published) || !root["sequence"].is<uint64_t>() ||
        sequence == 0 || now.timestamp - published > 30 || published - now.timestamp > 5 ||
        published < last_input_publication_ || (boot == input_boot_ && sequence <= input_sequence_))
      return false;
    static const char *keys[] = {"upstream_t", "upstream_rh", "downstream_t", "downstream_rh",
                                 "flow",       "pressure",    "command"};
    Frame next;
    for (unsigned i = 0; i < (remote_command_ ? 7U : 6U); i++) {
      JsonObject item = root["samples"][keys[i]];
      if (item.isNull())
        continue;
      std::string identity = item["source"].as<std::string>();
      std::string unit = item["unit"].as<std::string>();
      double factor = 1, offset = 0;
      bool valid = i == 6 ? unit == "V" : conversion(i, unit, factor, offset);
      if (!item["value"].is<double>() || !item["reported_at"].is<double>())
        continue;
      Sample &sample = next.samples[i];
      sample.value = valid ? item["value"].as<double>() * factor + offset : NAN;
      sample.reported_at = item["reported_at"].as<double>();
      sample.report_id = observation_id(item["report_id"].as<std::string>());
      std::string accuracy = item["accuracy"].as<std::string>();
      sample.source_id = fnv1_hash(identity + ":" + unit + ":" + accuracy);
      sample.verified = item["verified"].is<bool>() && item["verified"].as<bool>() &&
                        (i == 6 || (verified_[i] && identity == identities_[i]));
      sample.accuracy = item["accuracy"].is<double>() ? item["accuracy"].as<double>() * std::abs(factor) : NAN;
      if (i == 6) {
        next.command_uncertain =
            !item["uncertain"].isNull() && (!item["uncertain"].is<bool>() || item["uncertain"].as<bool>());
        remote_fault_ = !item["fault"].is<bool>() || item["fault"].as<bool>();
        command_event(sample.value, remote_fault_);
      }
    }
    if (!remote_command_)
      next.samples[6] = frame_.samples[6];
    frame_ = next;
    last_input_ = monotonic_ms_ / 1000.0;
    input_boot_ = boot;
    input_sequence_ = sequence;
    last_input_publication_ = published;
    return true;
  }
  static bool conversion(unsigned index, const std::string &unit, double &factor, double &offset) {
    if (index == 0 || index == 2) {
      if (unit == "°C" || unit == "C")
        return true;
      if (unit == "°F" || unit == "F") {
        factor = 1 / 1.8;
        offset = -32 / 1.8;
        return true;
      }
    } else if (index == 1 || index == 3)
      return unit == "%";
    else if (index == 4) {
      if (unit == "m³/h" || unit == "m3/h")
        return true;
      if (unit == "cfm") {
        factor = 1.69901079552;
        return true;
      }
    } else if (index == 5) {
      if (unit == "Pa")
        return true;
      if (unit == "kPa") {
        factor = 1000;
        return true;
      }
      if (unit == "hPa" || unit == "mbar") {
        factor = 100;
        return true;
      }
      if (unit == "inHg") {
        factor = 3386.389;
        return true;
      }
    }
    return false;
  }
};
}  // namespace hapsic_diagnostics
}  // namespace esphome
