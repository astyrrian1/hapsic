#ifdef USE_ESP32
/*
 * SPDX-FileCopyrightText: 2025 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "M5StamPLC.h"
#include "modbus_params.h"
#include "pin_config.h"
#include <cstring>

using namespace m5;

m5::M5_STAMPLC *M5StamPLC_ptr = nullptr;

static const char *TAG = "M5StamPLC";

void M5_STAMPLC::begin() {
  auto cfg = M5.config();
  M5.begin(cfg);
}

void M5_STAMPLC::update() {
  M5.update();
}

/* -------------------------------------------------------------------------- */
/*                                     I2C                                    */
/* -------------------------------------------------------------------------- */
void M5_STAMPLC::i2c_init() {
  m5::In_I2C.release();
  m5::In_I2C.begin(I2C_NUM_0, STAMPLC_PIN_I2C_INTER_SDA, STAMPLC_PIN_I2C_INTER_SCL);
}

/* -------------------------------------------------------------------------- */
/*                                  IO EXT A (0)                              */
/* -------------------------------------------------------------------------- */
void M5_STAMPLC::io_expander_a_init() {
  auto &ioe = M5.getIOExpander(0);

  // Status light init
  ioe.setDirection(4, true);
  ioe.setPullMode(4, false);
  ioe.setHighImpedance(4, true);

  ioe.setDirection(5, true);
  ioe.setPullMode(5, false);
  ioe.setHighImpedance(5, true);

  ioe.setDirection(6, true);
  ioe.setPullMode(6, false);
  ioe.setHighImpedance(6, true);

  ioe.resetIrq();
  ioe.disableIrq();
}

m5::IOExpander_Base &M5_STAMPLC::getIOExpanderA() {
  return M5.getIOExpander(0);
}

void M5_STAMPLC::setBacklight(bool on) {
  auto &ioe = M5.getIOExpander(0);

  ioe.setHighImpedance(7, !on);
  ioe.digitalWrite(7, !on);  // backlight is active low
}

void M5_STAMPLC::setStatusLight(const uint8_t &r, const uint8_t &g, const uint8_t &b) {
  auto &ioe = M5.getIOExpander(0);

  if (r == 0) {
    ioe.setHighImpedance(6, true);
  } else {
    ioe.setHighImpedance(6, false);
    ioe.digitalWrite(6, false);
  }

  if (g == 0) {
    ioe.setHighImpedance(5, true);
  } else {
    ioe.setHighImpedance(5, false);
    ioe.digitalWrite(5, false);
  }

  if (b == 0) {
    ioe.setHighImpedance(4, true);
  } else {
    ioe.setHighImpedance(4, false);
    ioe.digitalWrite(4, false);
  }
}

/* -------------------------------------------------------------------------- */
/*                                  IO EXT B                                  */
/* -------------------------------------------------------------------------- */
static std::vector<int> _in_pin_list = {4, 5, 6, 7, 12, 13, 14, 15};
static std::vector<int> _out_pin_list = {0, 1, 2, 3};

void M5_STAMPLC::io_expander_b_init() {
  _io_expander_b = new AW9523_Class;
  if (!_io_expander_b->begin()) {
    ESP_LOGE(TAG, "io expander b init failed");
  } else {
    _io_expander_b->configureDirection(0x0);  // all inputs!
    _io_expander_b->openDrainPort0(false);    // push pull default
    _io_expander_b->interruptEnableGPIO(0);   // no interrupt

    // Outputs init
    for (const auto &i : _out_pin_list) {
      _io_expander_b->pinMode(i, AW9523_Class::AW_OUTPUT);
      _io_expander_b->digitalWrite(i, false);
    }

    // Inputs init
    for (const auto &i : _in_pin_list) {
      _io_expander_b->pinMode(i, AW9523_Class::AW_INPUT);
    }

    _io_expander_b->disableIrq();
  }
}

AW9523_Class &M5_STAMPLC::getIOExpanderB() {
  return *_io_expander_b;
}

bool M5_STAMPLC::readPlcInput(const uint8_t &channel) {
  if (_io_expander_b == nullptr) {
    return false;
  }
  if (channel >= _in_pin_list.size()) {
    return false;
  }
  return _io_expander_b->digitalRead(_in_pin_list[channel]);
}

bool M5_STAMPLC::readPlcRelay(const uint8_t &channel) {
  if (_io_expander_b == nullptr) {
    return false;
  }
  if (channel >= _out_pin_list.size()) {
    return false;
  }
  return _io_expander_b->digitalRead(_out_pin_list[channel]);
}

void M5_STAMPLC::writePlcRelay(const uint8_t &channel, const bool &state) {
  if (_io_expander_b == nullptr) {
    return;
  }
  if (channel >= _out_pin_list.size()) {
    return;
  }
  _io_expander_b->digitalWrite(_out_pin_list[channel], state);
}

void M5_STAMPLC::writePlcAllRelay(const uint8_t &relayState) {
  if (_io_expander_b == nullptr) {
    return;
  }
  for (int i = 0; i < _out_pin_list.size(); i++) {
    _io_expander_b->digitalWrite(_out_pin_list[i], (relayState & (1 << i)));
  }
}

/* -------------------------------------------------------------------------- */
/*                                    LM75B                                   */
/* -------------------------------------------------------------------------- */
void M5_STAMPLC::lm75b_init() {
  if (!LM75B.begin()) {
    ESP_LOGE(TAG, "lm75b init failed");
  }
}

float M5_STAMPLC::getTemp() {
  return LM75B.temp();
}

/* -------------------------------------------------------------------------- */
/*                                   INA226                                   */
/* -------------------------------------------------------------------------- */
void M5_STAMPLC::ina226_init() {
  if (!INA226.begin()) {
    ESP_LOGE(TAG, "ina226 init failed");
  } else {
    INA226_Class::config_t cfg;
    cfg.sampling_rate = INA226_Class::Sampling::Rate16;
    cfg.bus_conversion_time = INA226_Class::ConversionTime::US_1100;
    cfg.shunt_conversion_time = INA226_Class::ConversionTime::US_1100;
    cfg.mode = INA226_Class::Mode::ShuntAndBus;
    cfg.shunt_res = 0.01f;
    cfg.max_expected_current = 2.0f;
    INA226.config(cfg);
  }
}

float M5_STAMPLC::getPowerVoltage() {
  return INA226.getBusVoltage();
}

float M5_STAMPLC::getIoSocketOutputCurrent() {
  return INA226.getShuntCurrent();
}

/* -------------------------------------------------------------------------- */
/*                                   RX8130                                   */
/* -------------------------------------------------------------------------- */
void M5_STAMPLC::rx8130_init() {
  if (!RX8130.begin()) {
    ESP_LOGE(TAG, "rx8130 init failed!");
  } else {
    RX8130.initBat();
    RX8130.disableIrq();
    RX8130.clearIrqFlags();
  }
}

void M5_STAMPLC::setRtcTime(struct tm *time) {
  RX8130.setTime(time);
}

void M5_STAMPLC::getRtcTime(struct tm *time) {
  RX8130.getTime(time);
}

/* -------------------------------------------------------------------------- */
/*                                   Buzzer                                   */
/* -------------------------------------------------------------------------- */
void M5_STAMPLC::tone(unsigned int frequency, unsigned long duration) {
  // Bypassed for ESP-IDF
}

void M5_STAMPLC::noTone() {
  // Bypassed for ESP-IDF
}

void M5_STAMPLC::modbus_slave_init() {
  ESP_LOGW("M5_STAMPLC", "Modbus slave initialization bypassed.");
}

#endif
