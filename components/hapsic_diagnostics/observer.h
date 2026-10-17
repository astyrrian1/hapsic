#pragma once

// Independent diagnostic engine. ASHRAE 2017 equations, checked against
// PsychroLib 2.5.0. No controller headers, services, actuators or learned state.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <iomanip>
#include <limits>
#include <sstream>
#include <string>
#include <type_traits>
#include <vector>

namespace esphome {
namespace hapsic_diagnostics {

inline bool valid_request_id(const std::string &request) {
  return !request.empty() && request.size() <= 128 && request.find('\0') == std::string::npos;
}

struct Config {
  bool verified{false};
  double max_age{120}, pressure_max_age{900}, max_skew{30}, stable_seconds{300}, warmup_seconds{900};
  double off_seconds{900}, baseline_seconds{600}, minimum_flow{50};
  double command_range{0.2}, flow_range{0.05}, temperature_slope{0.2}, moisture_slope{0.0001};
  double warning{60}, recovery{65}, site_pressure_pa{88600};
  unsigned minimum_reports{3}, max_window{512}, max_evidence{10080};
  uint32_t signature{1};
};

struct Sample {
  double value{NAN}, reported_at{NAN};
  uint64_t report_id{0};
  uint32_t source_id{0};
  bool verified{false};
  double accuracy{NAN};
};
struct Frame {
  // upstream T/RH, downstream T/RH, actual-upstream m3/h, Pa, command V.
  std::array<Sample, 7> samples{};
  bool fault{false}, command_uncertain{false};
};
struct Measurement {
  double raw{NAN}, corrected{NAN}, lower{NAN}, upper{NAN}, uncertainty{NAN}, delta{NAN}, delta_error{NAN};
  double upstream_t{NAN}, upstream_rh{NAN}, flow{NAN}, voltage{NAN};
  bool eligible{false};
  const char *reason{"STARTUP"};
  std::array<uint64_t, 5> reports{};
  std::array<double, 5> times{};
};
struct Row {
  Measurement m;
  double mono{0}, utc{0};
};
struct Baseline {
  bool valid{false}, known{false};
  double offset{0}, residual{0};
  std::array<std::array<double, 2>, 3> envelope{};
};
struct OffWindow {
  uint32_t period{0};
  double mean{0}, minimum{0}, maximum{0}, error{0};
  bool known{false};
  std::array<std::array<double, 2>, 3> envelope{};
};
struct Bin {
  bool used{false}, known{true};
  double seconds{0}, voltage{0}, flow{0}, output{0};
  double vmin{0}, vmax{0}, fmin{0}, fmax{0}, lower{NAN}, upper{NAN};
  double min_output{1e30}, max_output{-1e30};
  std::array<uint32_t, 3> cycles{};
  unsigned cycle_count{0};
  bool ready() const {
    return used && seconds >= 1800 && cycle_count >= 3 && output > 0 && (!known || (std::isfinite(lower) && lower > 0));
  }
  double value() const { return output / seconds; }
  double command() const { return voltage / seconds; }
  double airflow() const { return flow / seconds; }
};
using Bins = std::array<Bin, 21>;
struct Block {
  double start{0}, end{0}, seconds{0}, measured{0}, lower{NAN}, upper{NAN};
  double reference{0}, reference_lower{NAN}, reference_upper{NAN};
  uint32_t cycle{0}, generation{0};
};
struct Score {
  double value{NAN}, lower{NAN}, upper{NAN}, seconds{0}, last_observed{NAN};
  unsigned cycles{0};
  bool ready{false};
  const char *status{"insufficient_data"};
};
struct Candidate {
  uint32_t kind{0}, revision{0}, generation{0}, count{0};  // 1 baseline, 2 reference
  bool healthy{false}, ready{false};
  double offset{0}, residual{0};
  std::array<OffWindow, 4> windows{};
  Bins bins{};
};
struct Archive {
  uint32_t generation{0};
  Baseline baseline;
  Bins bins;
};

class Observer {
 public:
  explicit Observer(Config config = {}) : config(config), generation(config.signature) {}
  Config config;
  Measurement measurement;
  Baseline baseline;
  Bins reference{};
  Candidate candidate;
  std::deque<Block> evidence, quarantine;
  std::deque<Archive> archives;
  std::deque<Row> window;
  std::deque<Block> delivery_window;
  uint32_t generation{1}, revision{0}, reference_id{0}, cycle{0}, off_period{0}, episode{0};
  bool clock_valid{false}, off_open{false}, alert_active{false};
  double qualified_seconds{0};
  const char *reason{"STARTUP"}, *decision{"inconclusive"};
  Score delivery, canister, alert_snapshot;
  Score last_delivery, last_canister;
  double last_delivery_at{NAN}, last_canister_at{NAN};

  static double humidity_ratio(double temp, double rh, double pressure) {
    if (!(temp >= -80 && temp <= 100 && rh >= 0 && rh <= 100 && pressure >= 20000 && pressure <= 120000))
      return NAN;
    double t = temp + 273.15;
    double p = temp <= 0.01 ? -5674.5359 / t + 6.3925247 - 0.009677843 * t + 0.00000062215701 * t * t +
                                  0.0000000020747825 * t * t * t - 0.0000000000009484024 * t * t * t * t +
                                  4.1635019 * std::log(t)
                            : -5800.2206 / t + 1.3914993 - 0.048640239 * t + 0.000041764768 * t * t -
                                  0.000000014452093 * t * t * t + 6.5459673 * std::log(t);
    double vapor = std::exp(p) * rh / 100;
    return vapor < pressure ? 0.621945 * vapor / (pressure - vapor) : NAN;
  }
  static double volume(double t, double w, double p) { return 287.042 * (t + 273.15) * (1 + 1.607858 * w) / p; }
  Measurement measure(const Frame &frame, double now) const {
    Measurement m;
    m.reason = "INVALID_OR_MISSING_SOURCE";
    bool verified = config.verified;
    std::array<double, 6> x{}, err{};
    bool known = true;
    for (unsigned i = 0; i < 7; i++) {
      const auto &s = frame.samples[i];
      double age = now - s.reported_at;
      if (i == 5 && !std::isfinite(s.value)) {
        x[i] = config.site_pressure_pa;
        known = verified = false;
        continue;
      }
      if (!std::isfinite(s.value) || !std::isfinite(age) || age < 0 ||
          age > (i == 6   ? 15
                 : i == 5 ? config.pressure_max_age
                          : config.max_age))
        return m;
      if (i == 6) {
        if (s.value < 0 || s.value > 10 || frame.fault || frame.command_uncertain || !s.verified || !s.source_id)
          return m;
        m.voltage = s.value;
      } else {
        x[i] = s.value;
        err[i] = s.accuracy;
        known = known && std::isfinite(s.accuracy) && s.accuracy >= 0;
        verified = verified && s.verified && s.source_id != 0;
        if (i < 5) {
          m.reports[i] = s.report_id;
          m.times[i] = s.reported_at;
        }
      }
    }
    auto minmax = std::minmax_element(m.times.begin(), m.times.end());
    if (*minmax.second - *minmax.first > config.max_skew || x[4] < 0)
      return m;
    double wu = humidity_ratio(x[0], x[1], x[5]), wd = humidity_ratio(x[2], x[3], x[5]);
    double mass = x[4] / volume(x[0], wu, x[5]);
    if (!std::isfinite(wu) || !std::isfinite(wd) || !std::isfinite(mass))
      return m;
    m.delta = wd - wu;
    m.raw = mass * m.delta;
    m.corrected = mass * (m.delta - baseline.offset);
    m.upstream_t = x[0];
    m.upstream_rh = x[1];
    m.flow = x[4];
    if (known && (!baseline.valid || baseline.known)) {
      double wumin = 1e30, wumax = -1e30, wdmin = 1e30, wdmax = -1e30, vmin = 1e30, vmax = -1e30;
      for (int a : {-1, 1})
        for (int b : {-1, 1})
          for (int c : {-1, 1}) {
            double temp = x[0] + a * err[0], p = x[5] + c * err[5];
            double w = humidity_ratio(temp, std::clamp(x[1] + b * err[1], 0.0, 100.0), p);
            double v = volume(temp, w, p);
            double d = humidity_ratio(x[2] + a * err[2], std::clamp(x[3] + b * err[3], 0.0, 100.0), p);
            if (!std::isfinite(w) || !std::isfinite(d) || !std::isfinite(v))
              return Measurement{};
            wumin = std::min(wumin, w);
            wumax = std::max(wumax, w);
            wdmin = std::min(wdmin, d);
            wdmax = std::max(wdmax, d);
            vmin = std::min(vmin, v);
            vmax = std::max(vmax, v);
          }
      m.lower = 1e30;
      m.upper = -1e30;
      double dmin = wdmin - wumax - baseline.offset - baseline.residual;
      double dmax = wdmax - wumin - baseline.offset + baseline.residual;
      for (double air : {std::max(0.0, x[4] - err[4]) / vmax, (x[4] + err[4]) / vmin})
        for (double d : {dmin, dmax}) {
          m.lower = std::min(m.lower, air * d);
          m.upper = std::max(m.upper, air * d);
        }
      m.uncertainty = std::max(m.corrected - m.lower, m.upper - m.corrected);
      if (mass > 0)
        m.delta_error = m.uncertainty / mass;
    }
    m.eligible = verified;
    m.reason = verified ? "OK" : "UNVERIFIED_PROVENANCE_OR_AIRFLOW";
    if (!std::isfinite(frame.samples[5].value))
      m.reason = "SITE_PRESSURE_ESTIMATE";
    if (baseline.valid && m.voltage > 0 && std::isfinite(m.upper) && m.upper < 0) {
      m.reason = "MEASUREMENT_MISMATCH";
      m.eligible = false;
    }
    if (std::strcmp(m.reason, "OK") == 0 && baseline.valid && !in_envelope(m))
      m.reason = "OUTSIDE_BASELINE_ENVELOPE";
    return m;
  }
  void break_continuity(const char *why) {
    zero_since = demand_since = NAN;
    interrupt(why);
  }
  void interrupt(const char *why) {
    previous_valid = false;
    window.clear();
    delivery_window.clear();
    qualified_seconds = 0;
    reason = why;
  }
  void quarantine_evidence() {
    if (evidence.empty())
      return;
    quarantine.clear();
    for (const auto &b : evidence) {
      if (quarantine.empty() || quarantine.back().cycle != b.cycle) {
        quarantine.push_back(b);
        continue;
      }
      auto &q = quarantine.back();
      double total = q.seconds + b.seconds;
      q.measured = (q.measured * q.seconds + b.measured * b.seconds) / total;
      q.lower = (q.lower * q.seconds + b.lower * b.seconds) / total;
      q.upper = (q.upper * q.seconds + b.upper * b.seconds) / total;
      q.reference = (q.reference * q.seconds + b.reference * b.seconds) / total;
      q.reference_lower = (q.reference_lower * q.seconds + b.reference_lower * b.seconds) / total;
      q.reference_upper = (q.reference_upper * q.seconds + b.reference_upper * b.seconds) / total;
      q.end = b.end;
      q.seconds = total;
    }
  }
  bool clock(double mono, double utc, bool synced) {
    if (!std::isfinite(mono) || (std::isfinite(last_mono) && mono <= last_mono))
      return false;
    bool step = std::isfinite(last_mono) && std::isfinite(last_utc) && std::isfinite(utc) &&
                std::abs((utc - last_utc) - (mono - last_mono)) > 5;
    last_mono = mono;
    last_utc = utc;
    if (!synced || !std::isfinite(utc)) {
      clock_valid = false;
      recovery_since = mono;
    } else if (!std::isfinite(anchor)) {
      anchor = utc - mono;
      clock_valid = true;
    } else if (std::abs(utc - mono - anchor) > 5 || step) {
      clock_valid = false;
      anchor = utc - mono;
      recovery_since = mono;
      quarantine_evidence();
      evidence.clear();
    } else if (std::isfinite(recovery_since) && mono - recovery_since >= 30) {
      clock_valid = true;
      recovery_since = NAN;
    }
    if (clock_valid && !evidence.empty() && evidence.back().end > utc + 5) {
      quarantine_evidence();
      evidence.clear();
    }
    return clock_valid;
  }
  void observe(const Frame &frame, double mono, double utc, bool synced) {
    if (!clock(mono, utc, synced)) {
      zero_since = demand_since = NAN;
      interrupt("CLOCK_UNSYNCHRONIZED");
      measurement = Measurement{};
      measurement.reason = "CLOCK_UNSYNCHRONIZED";
      refresh(utc);
      return;
    }
    measurement = measure(frame, utc);
    if (!measurement.eligible || measurement.flow < config.minimum_flow) {
      zero_since = demand_since = NAN;
      interrupt(measurement.eligible ? "LOW_FLOW" : measurement.reason);
      refresh(utc);
      return;
    }
    std::array<uint32_t, 7> identity{};
    for (unsigned i = 0; i < 7; i++)
      identity[i] = frame.samples[i].source_id;
    if (identity_valid && identity != source_identity) {
      action("baseline_reset");
      source_identity = identity;
      interrupt("SOURCE_IDENTITY_CHANGED");
      measurement = Measurement{};
      measurement.reason = "SOURCE_IDENTITY_CHANGED";
      refresh(utc);
      return;
    }
    source_identity = identity;
    identity_valid = true;
    Row row{measurement, mono, utc};
    if (previous_valid && (previous.m.voltage == 0) != (row.m.voltage == 0))
      interrupt("COMMAND_INTERRUPTED");
    if (previous_valid && row.m.reports == previous.m.reports) {
      refresh(utc);
      return;
    }
    bool older = false;
    for (unsigned i = 0; i < 5 && previous_valid; i++)
      older = older || row.m.times[i] < previous.m.times[i];
    if (previous_valid && (mono - previous.mono > config.max_age || utc <= previous.utc || older)) {
      interrupt("OBSERVATION_GAP");
      zero_since = demand_since = NAN;
    }
    if (row.m.voltage == 0) {
      if (!std::isfinite(zero_since)) {
        zero_since = mono;
        if (!off_open)
          off_period++;
        off_open = true;
      }
      demand_since = NAN;
    } else {
      off_open = false;
      if (!std::isfinite(demand_since)) {
        double off = mono - zero_since;
        if (std::isfinite(off) && off >= config.off_seconds)
          cycle++;
        demand_since = mono;
        warmup_until = mono + ((!std::isfinite(off) || off >= 60) ? config.warmup_seconds : 0);
      }
      zero_since = NAN;
    }
    if (row.m.voltage > 0 && mono < warmup_until) {
      interrupt("WARMUP");
      previous = row;
      previous_valid = true;
      refresh(utc);
      return;
    }
    window.push_back(row);
    double duration = row.m.voltage == 0 ? config.baseline_seconds : config.stable_seconds;
    while (window.size() > 1 && window[1].mono <= mono - duration)
      window.pop_front();
    if (window.size() > config.max_window)
      interrupt("CAPACITY_EXHAUSTED");
    bool qualified = stable(duration), was_qualified = std::strcmp(reason, "QUALIFIED") == 0;
    reason = qualified ? "QUALIFIED" : "STABILIZING";
    if (row.m.voltage == 0) {
      delivery_window.clear();
      qualified_seconds = 0;
      if (qualified && mono - zero_since >= config.off_seconds + duration)
        collect_baseline();
    } else if (qualified && was_qualified && previous_valid) {
      double seconds = mono - previous.mono;
      qualified_seconds += seconds;
      if (in_envelope(row.m) && in_envelope(previous.m)) {
        collect_reference(previous.m, seconds);
        auto match = reference_rate(previous.m.voltage, previous.m.flow);
        if (std::isfinite(match[0])) {
          Block block{previous.utc,     utc,
                      seconds,          previous.m.corrected,
                      previous.m.lower, previous.m.upper,
                      match[0],         match[1],
                      match[2],         cycle,
                      generation};
          delivery_window.push_back(block);
          trim(delivery_window, 300);
          if (row.m.voltage >= 9 && previous.m.voltage >= 9) {
            evidence.push_back(block);
            trim(evidence, 7200);
            if (evidence.size() > config.max_evidence) {
              evidence.pop_back();
              interrupt("CAPACITY_EXHAUSTED");
            }
          }
        } else
          delivery_window.clear();
      } else
        delivery_window.clear();
    } else
      delivery_window.clear();
    previous = row;
    previous_valid = true;
    refresh(utc);
  }
  bool action(const std::string &name, uint32_t expected_revision = 0, bool healthy = false) {
    if (name == "baseline_start" || name == "reference_start") {
      if (name == "reference_start" && (!baseline.valid || !healthy))
        return false;
      candidate = Candidate{};
      candidate.kind = name == "baseline_start" ? 1 : 2;
      candidate.revision = ++revision;
      candidate.generation = generation;
      candidate.healthy = healthy;
      interrupt("CALIBRATION_STARTED");
      return true;
    }
    if (name == "cancel") {
      candidate = Candidate{};
      return true;
    }
    if (name == "baseline_reset" || name == "reference_reset") {
      archive();
      revision++;
      if (name == "baseline_reset") {
        baseline = Baseline{};
        generation++;
      }
      reference = Bins{};
      reference_id = 0;
      candidate = Candidate{};
      evidence.clear();
      quarantine.clear();
      last_delivery = last_canister = alert_snapshot = Score{};
      alert_active = false;
      interrupt("CALIBRATION_RESET");
      return true;
    }
    if (name != "baseline_accept" && name != "reference_accept")
      return false;
    update_ready();
    if (!candidate.ready || candidate.revision != expected_revision || candidate.generation != generation ||
        candidate.kind != (name == "baseline_accept" ? 1U : 2U))
      return false;
    archive();
    if (candidate.kind == 1) {
      baseline.valid = baseline.known = true;
      baseline.offset = candidate.offset;
      baseline.residual = candidate.residual;
      for (unsigned i = 0; i < 4; i++)
        baseline.known = baseline.known && candidate.windows[i].known;
      for (unsigned key = 0; key < 3; key++) {
        baseline.envelope[key] = candidate.windows[0].envelope[key];
        for (unsigned i = 1; i < 3; i++) {
          baseline.envelope[key][0] = std::min(baseline.envelope[key][0], candidate.windows[i].envelope[key][0]);
          baseline.envelope[key][1] = std::max(baseline.envelope[key][1], candidate.windows[i].envelope[key][1]);
        }
      }
      generation++;
      reference = Bins{};
      reference_id = 0;
    } else {
      reference = candidate.bins;
      reference_id = candidate.revision;
      for (auto &bin : reference)
        if (!bin.ready())
          bin = Bin{};
    }
    candidate = Candidate{};
    evidence.clear();
    quarantine.clear();
    last_delivery = last_canister = alert_snapshot = Score{};
    alert_active = false;
    interrupt("CALIBRATION_ACCEPTED");
    return true;
  }
  void refresh(double utc) {
    if (clock_valid) {
      while (!evidence.empty() && evidence.front().end < utc - 7 * 86400)
        evidence.pop_front();
      if (!evidence.empty() && evidence.front().start < utc - 7 * 86400) {
        auto &first = evidence.front();
        double cutoff = utc - 7 * 86400;
        first.seconds *= (first.end - cutoff) / (first.end - first.start);
        first.start = cutoff;
      }
    }
    delivery = std::strcmp(reason, "QUALIFIED") == 0 ? score(delivery_window, 300, 0) : Score{};
    canister = score(evidence, 1800, 3);
    canister.ready = canister.seconds >= 7200 && canister.cycles >= 3;
    if (!canister.ready && std::isfinite(canister.value))
      canister.status = "provisional";
    if (!evidence.empty()) {
      canister.last_observed = evidence.back().end;
      if (clock_valid && utc - evidence.back().end > 86400) {
        canister.value = canister.lower = canister.upper = NAN;
        canister.status = "stale";
      }
    }
    if (!clock_valid) {
      delivery.value = canister.value = canister.lower = canister.upper = NAN;
      delivery.status = canister.status = "unavailable";
    }
    decision = "inconclusive";
    if (canister.ready && std::strcmp(canister.status, "valid") == 0) {
      if (canister.upper < config.warning) {
        if (!alert_active) {
          episode++;
          alert_snapshot = canister;
        }
        alert_active = true;
        decision = "warn";
      } else if (canister.lower > config.recovery) {
        alert_active = false;
        decision = "clear";
      }
    }
    if (std::isfinite(delivery.value)) {
      last_delivery = delivery;
      last_delivery_at = delivery_window.back().end;
    }
    if (std::isfinite(canister.value)) {
      last_canister = canister;
      last_canister_at = evidence.back().end;
    }
    update_ready();
  }
  std::string json(double utc) {
    refresh(utc);
    std::ostringstream s;
    s << std::setprecision(16) << "{\"schema_version\":1,\"observed_at\":";
    number(s, measurement.times[0] > 0 ? *std::max_element(measurement.times.begin(), measurement.times.end()) : NAN);
    s << ",\"generation\":\"" << generation << "\",\"clock_valid\":" << (clock_valid ? "true" : "false")
      << ",\"measurement\":{\"raw_kg_h\":";
    number(s, measurement.raw);
    s << ",\"corrected_kg_h\":";
    number(s, measurement.corrected);
    s << ",\"lower_kg_h\":";
    number(s, measurement.lower);
    s << ",\"upper_kg_h\":";
    number(s, measurement.upper);
    s << ",\"uncertainty_kg_h\":";
    number(s, measurement.uncertainty);
    s << ",\"eligible\":" << (measurement.eligible ? "true" : "false") << ",\"reason\":\"" << measurement.reason
      << "\",\"status\":\""
      << (!std::isfinite(measurement.raw) ? "unavailable"
          : measurement.eligible && std::isfinite(measurement.uncertainty) && std::strcmp(measurement.reason, "OK") == 0
              ? "valid"
              : "provisional")
      << "\"},\"qualification\":{\"stable\":" << (std::strcmp(reason, "QUALIFIED") == 0 ? "true" : "false")
      << ",\"reason\":\"" << reason << "\",\"qualified_seconds\":" << qualified_seconds << ",\"cycle_id\":" << cycle
      << "},\"calibration\":{\"status\":\""
      << (!reference_id    ? "uncalibrated"
          : baseline.known ? "valid"
                           : "provisional")
      << "\",\"reference_id\":";
    if (reference_id)
      s << reference_id;
    else
      s << "null";
    s << ",\"coverage\":[";
    bool comma = false;
    for (unsigned i = 0; i < 21; i++)
      if (reference[i].ready()) {
        if (comma)
          s << ',';
        s << '\"' << i << '\"';
        comma = true;
      }
    s << "],\"candidate\":";
    if (!candidate.kind)
      s << "null";
    else {
      s << "{\"kind\":\"" << (candidate.kind == 1 ? "baseline" : "reference")
        << "\",\"revision\":" << candidate.revision << ",\"ready\":" << (candidate.ready ? "true" : "false")
        << ",\"healthy\":" << (candidate.healthy ? "true" : "false") << ",\"offset\":";
      number(s, candidate.offset);
      s << ",\"residual\":";
      number(s, candidate.residual);
      s << ",\"windows\":[";
      for (unsigned i = 0; i < candidate.count; i++) {
        if (i)
          s << ',';
        const auto &w = candidate.windows[i];
        s << "{\"period\":" << w.period << ",\"mean\":";
        number(s, w.mean);
        s << ",\"minimum\":";
        number(s, w.minimum);
        s << ",\"maximum\":";
        number(s, w.maximum);
        s << ",\"error\":";
        number(s, w.error);
        s << ",\"uncertainty_known\":" << (w.known ? "true" : "false") << ",\"envelope\":";
        envelope_json(s, w.envelope);
        s << '}';
      }
      s << "],\"bins\":";
      bins_json(s, candidate.bins);
      s << '}';
    }
    s << ",\"baseline\":";
    if (!baseline.valid)
      s << "null";
    else {
      s << "{\"offset\":";
      number(s, baseline.offset);
      s << ",\"residual\":";
      number(s, baseline.residual);
      s << ",\"uncertainty_known\":" << (baseline.known ? "true" : "false") << ",\"envelope\":";
      envelope_json(s, baseline.envelope);
      s << '}';
    }
    s << ",\"reference\":";
    bins_json(s, reference);
    s << "},\"delivery\":";
    score_json(s, delivery);
    s << ",\"canister\":";
    score_json(s, canister);
    s << ",\"last_valid\":{\"delivery\":{";
    s << "\"at\":";
    number(s, last_delivery_at);
    s << ",\"value\":";
    number(s, last_delivery.value);
    s << ",\"status\":\"" << last_delivery.status << "\"";
    s << "},\"canister\":{\"at\":";
    number(s, last_canister_at);
    s << ",\"value\":";
    number(s, last_canister.value);
    s << ",\"status\":\"" << last_canister.status << "\"";
    s << "}},\"alert\":{\"decision\":\"" << decision << "\",\"episode\":" << episode
      << ",\"active\":" << (alert_active ? "true" : "false") << ",\"warning\":" << config.warning
      << ",\"recovery\":" << config.recovery << ",\"evidence\":";
    score_json(s, alert_snapshot);
    s << "},\"exclusions\":{}}";
    return s.str();
  }

  // Firmware persists this versioned payload in a separate two-slot preferences
  // transaction. No control-owned preferences or legacy CHI are imported.
  std::vector<uint8_t> save() const {
    Writer w;
    w.pod(uint32_t{0x48444731});
    w.pod(config.signature);
    w.pod(uint32_t{sizeof(Bin)});
    w.pod(generation);
    w.pod(revision);
    w.pod(reference_id);
    w.pod(cycle);
    w.pod(off_period);
    w.pod(off_open);
    w.pod(episode);
    w.pod(alert_active);
    w.pod(alert_snapshot.value);
    w.pod(alert_snapshot.lower);
    w.pod(alert_snapshot.upper);
    w.pod(alert_snapshot.seconds);
    w.pod(alert_snapshot.cycles);
    w.pod(alert_snapshot.last_observed);
    w.pod(baseline);
    w.bins(reference);
    w.pod(candidate.kind);
    w.pod(candidate.revision);
    w.pod(candidate.generation);
    w.pod(candidate.count);
    w.pod(candidate.healthy);
    w.pod(candidate.ready);
    w.pod(candidate.offset);
    w.pod(candidate.residual);
    for (unsigned i = 0; i < candidate.count; i++)
      w.pod(candidate.windows[i]);
    w.bins(candidate.bins);
    w.blocks(evidence);
    w.blocks(quarantine);
    w.pod(uint32_t(archives.size()));
    for (const auto &a : archives) {
      w.pod(a.generation);
      w.pod(a.baseline);
      w.bins(a.bins);
    }
    w.pod(source_identity);
    w.pod(identity_valid);
    // Score contains reason pointers; persist only numeric last-valid values.
    w.pod(last_delivery.value);
    w.pod(std::strcmp(last_delivery.status, "valid") == 0);
    w.pod(last_delivery_at);
    w.pod(last_canister.value);
    w.pod(std::strcmp(last_canister.status, "valid") == 0);
    w.pod(last_canister_at);
    return w.bytes;
  }
  bool restore(const std::vector<uint8_t> &bytes) {
    Observer restored(config);
    Reader r(bytes);
    uint32_t magic = 0, signature = 0, bin_size = 0;
    r.pod(magic);
    r.pod(signature);
    r.pod(bin_size);
    if (magic != 0x48444731 || signature != config.signature || bin_size != sizeof(Bin))
      return false;
    r.pod(restored.generation);
    r.pod(restored.revision);
    r.pod(restored.reference_id);
    r.pod(restored.cycle);
    r.pod(restored.off_period);
    r.pod(restored.off_open);
    r.pod(restored.episode);
    r.pod(restored.alert_active);
    r.pod(restored.alert_snapshot.value);
    r.pod(restored.alert_snapshot.lower);
    r.pod(restored.alert_snapshot.upper);
    r.pod(restored.alert_snapshot.seconds);
    r.pod(restored.alert_snapshot.cycles);
    r.pod(restored.alert_snapshot.last_observed);
    if (std::isfinite(restored.alert_snapshot.value)) {
      restored.alert_snapshot.status = "valid";
      restored.alert_snapshot.ready = true;
    }
    r.pod(restored.baseline);
    r.bins(restored.reference);
    r.pod(restored.candidate.kind);
    r.pod(restored.candidate.revision);
    r.pod(restored.candidate.generation);
    r.pod(restored.candidate.count);
    r.pod(restored.candidate.healthy);
    r.pod(restored.candidate.ready);
    r.pod(restored.candidate.offset);
    r.pod(restored.candidate.residual);
    if (restored.candidate.count > 4 || restored.candidate.kind > 2)
      return false;
    for (unsigned i = 0; i < restored.candidate.count; i++)
      r.pod(restored.candidate.windows[i]);
    r.bins(restored.candidate.bins);
    r.blocks(restored.evidence, config.max_evidence);
    r.blocks(restored.quarantine, config.max_evidence);
    uint32_t archives_count = 0;
    r.pod(archives_count);
    if (archives_count > 4)
      return false;
    for (unsigned i = 0; i < archives_count; i++) {
      Archive a;
      r.pod(a.generation);
      r.pod(a.baseline);
      r.bins(a.bins);
      restored.archives.push_back(a);
    }
    r.pod(restored.source_identity);
    r.pod(restored.identity_valid);
    r.pod(restored.last_delivery.value);
    bool delivery_valid = false;
    r.pod(delivery_valid);
    restored.last_delivery.status = delivery_valid ? "valid" : "provisional";
    r.pod(restored.last_delivery_at);
    r.pod(restored.last_canister.value);
    bool canister_valid = false;
    r.pod(canister_valid);
    restored.last_canister.status = canister_valid ? "valid" : "provisional";
    r.pod(restored.last_canister_at);
    if (!r.ok || r.offset != bytes.size() || !restored.valid_state())
      return false;
    restored.interrupt("RESTART");
    *this = restored;
    return true;
  }

 private:
  Row previous;
  bool previous_valid{false}, identity_valid{false};
  std::array<uint32_t, 7> source_identity{};
  double last_mono{NAN}, last_utc{NAN}, anchor{NAN}, recovery_since{NAN}, zero_since{NAN}, demand_since{NAN},
      warmup_until{0};

  static void number(std::ostream &s, double n) {
    if (std::isfinite(n))
      s << n;
    else
      s << "null";
  }
  static void envelope_json(std::ostream &s, const std::array<std::array<double, 2>, 3> &envelope) {
    const char *keys[] = {"upstream_t", "upstream_rh", "flow"};
    s << '{';
    for (unsigned i = 0; i < 3; i++) {
      if (i)
        s << ',';
      s << '\"' << keys[i] << "\":[";
      number(s, envelope[i][0]);
      s << ',';
      number(s, envelope[i][1]);
      s << ']';
    }
    s << '}';
  }
  static void bins_json(std::ostream &s, const Bins &bins) {
    s << '{';
    bool comma = false;
    for (unsigned i = 0; i < bins.size(); i++)
      if (bins[i].used) {
        if (comma)
          s << ',';
        comma = true;
        const auto &b = bins[i];
        s << '\"' << i << "\":{\"seconds\":" << b.seconds << ",\"cycles\":[";
        for (unsigned c = 0; c < b.cycle_count; c++) {
          if (c)
            s << ',';
          s << b.cycles[c];
        }
        s << "],\"value\":";
        number(s, b.value());
        s << ",\"command\":";
        number(s, b.command());
        s << ",\"airflow\":";
        number(s, b.airflow());
        s << ",\"lower\":";
        number(s, b.lower);
        s << ",\"upper\":";
        number(s, b.upper);
        s << ",\"ready\":" << (b.ready() ? "true" : "false") << '}';
      }
    s << '}';
  }
  static void score_json(std::ostream &s, const Score &score) {
    s << "{\"value\":";
    number(s, score.value);
    s << ",\"lower\":";
    number(s, score.lower);
    s << ",\"upper\":";
    number(s, score.upper);
    s << ",\"seconds\":" << score.seconds << ",\"cycles\":" << score.cycles
      << ",\"ready\":" << (score.ready ? "true" : "false") << ",\"status\":\"" << score.status
      << "\",\"last_observed_at\":";
    number(s, score.last_observed);
    s << '}';
  }
  double slope(bool temperature) const {
    double mt = 0, mv = 0, numerator = 0, denominator = 0;
    for (const auto &r : window) {
      mt += r.mono;
      mv += temperature ? r.m.upstream_t : r.m.delta;
    }
    mt /= window.size();
    mv /= window.size();
    for (const auto &r : window) {
      double dt = r.mono - mt;
      numerator += dt * ((temperature ? r.m.upstream_t : r.m.delta) - mv);
      denominator += dt * dt;
    }
    return denominator ? 60 * numerator / denominator : 0;
  }
  bool stable(double duration) const {
    if (window.empty() || window.back().mono - window.front().mono < duration - 1e-6)
      return false;
    for (unsigned i = 0; i < 5; i++) {
      std::array<uint64_t, 3> ids{};
      unsigned count = 0;
      for (const auto &r : window) {
        bool found = false;
        for (unsigned j = 0; j < count; j++)
          found = found || ids[j] == r.m.reports[i];
        if (!found && count < 3)
          ids[count++] = r.m.reports[i];
      }
      if (count < config.minimum_reports)
        return false;
    }
    double vmin = 1e30, vmax = -1e30, fmin = 1e30, fmax = -1e30, mean = 0;
    for (const auto &r : window) {
      vmin = std::min(vmin, r.m.voltage);
      vmax = std::max(vmax, r.m.voltage);
      fmin = std::min(fmin, r.m.flow);
      fmax = std::max(fmax, r.m.flow);
      mean += r.m.flow;
    }
    return vmax - vmin <= config.command_range + 1e-9 &&
           fmax - fmin <= config.flow_range * mean / window.size() + 1e-9 &&
           std::abs(slope(true)) <= config.temperature_slope + 1e-9 &&
           std::abs(slope(false)) <= config.moisture_slope + 1e-12;
  }
  bool in_envelope(const Measurement &m) const {
    if (!baseline.valid)
      return false;
    std::array<double, 3> values{m.upstream_t, m.upstream_rh, m.flow};
    for (unsigned i = 0; i < 3; i++)
      if (values[i] < baseline.envelope[i][0] - 1e-9 || values[i] > baseline.envelope[i][1] + 1e-9)
        return false;
    return true;
  }
  void collect_baseline() {
    if (candidate.kind != 1 || candidate.count >= 4)
      return;
    for (unsigned i = 0; i < candidate.count; i++)
      if (candidate.windows[i].period == off_period)
        return;
    OffWindow w;
    w.period = off_period;
    w.known = true;
    w.minimum = 1e30;
    w.maximum = -1e30;
    for (auto &range : w.envelope)
      range = {1e30, -1e30};
    double duration = 0;
    for (unsigned i = 0; i < window.size(); i++) {
      const auto &row = window[i];
      if (i + 1 < window.size()) {
        double dt = window[i + 1].mono - row.mono;
        w.mean += row.m.delta * dt;
        duration += dt;
      }
      w.minimum = std::min(w.minimum, row.m.delta);
      w.maximum = std::max(w.maximum, row.m.delta);
      w.known = w.known && std::isfinite(row.m.uncertainty);
      if (std::isfinite(row.m.delta_error))
        w.error = std::max(w.error, row.m.delta_error);
      std::array<double, 3> values{row.m.upstream_t, row.m.upstream_rh, row.m.flow};
      for (unsigned key = 0; key < 3; key++) {
        w.envelope[key][0] = std::min(w.envelope[key][0], values[key]);
        w.envelope[key][1] = std::max(w.envelope[key][1], values[key]);
      }
    }
    w.mean /= duration;
    candidate.windows[candidate.count++] = w;
    if (candidate.count >= 3) {
      std::array<double, 3> means{candidate.windows[0].mean, candidate.windows[1].mean, candidate.windows[2].mean};
      std::sort(means.begin(), means.end());
      candidate.offset = means[1];
      candidate.residual = 0;
      for (unsigned i = 0; i < 3; i++)
        candidate.residual =
            std::max(candidate.residual, std::max(std::abs(candidate.windows[i].minimum - candidate.offset),
                                                  std::abs(candidate.windows[i].maximum - candidate.offset)));
      candidate.ready =
          candidate.count == 4 && std::abs(w.mean - candidate.offset) <= candidate.residual + w.error + 1e-12;
    }
  }
  void collect_reference(const Measurement &m, double seconds) {
    if (candidate.kind != 2)
      return;
    unsigned index = static_cast<unsigned>(std::floor(m.voltage * 2 + 0.5));
    if (index > 20)
      return;
    auto &b = candidate.bins[index];
    if (b.ready())
      return;
    if (!b.used) {
      b.used = true;
      b.vmin = b.vmax = m.voltage;
      b.fmin = b.fmax = m.flow;
    }
    double vmin = std::min(b.vmin, m.voltage), vmax = std::max(b.vmax, m.voltage);
    double fmin = std::min(b.fmin, m.flow), fmax = std::max(b.fmax, m.flow);
    if (vmax - vmin > 0.2 + 1e-9 || fmax - fmin > 0.1 * (b.flow + m.flow * seconds) / (b.seconds + seconds) + 1e-9)
      return;
    b.vmin = vmin;
    b.vmax = vmax;
    b.fmin = fmin;
    b.fmax = fmax;
    b.seconds += seconds;
    b.voltage += m.voltage * seconds;
    b.flow += m.flow * seconds;
    b.output += m.corrected * seconds;
    bool found = false;
    for (unsigned i = 0; i < b.cycle_count; i++)
      found = found || b.cycles[i] == cycle;
    if (!found && b.cycle_count < 3)
      b.cycles[b.cycle_count++] = cycle;
    b.min_output = std::min(b.min_output, m.corrected);
    b.max_output = std::max(b.max_output, m.corrected);
    b.known = b.known && std::isfinite(m.lower);
    if (std::isfinite(m.lower)) {
      b.lower = std::isfinite(b.lower) ? std::min(b.lower, m.lower) : m.lower;
      b.upper = std::isfinite(b.upper) ? std::max(b.upper, m.upper) : m.upper;
    }
  }
  void update_ready() {
    if (candidate.kind != 2)
      return;
    candidate.ready = false;
    for (auto &b : candidate.bins)
      candidate.ready = candidate.ready || b.ready();
  }
  std::array<double, 3> reference_rate(double voltage, double flow) const {
    std::array<double, 3> missing{NAN, NAN, NAN};
    int previous_index = -1;
    for (int i = 0; i < 21; i++) {
      const auto &b = reference[i];
      if (!b.ready() || std::abs(flow / b.airflow() - 1) > 0.1 + 1e-9)
        continue;
      if (std::abs(voltage - b.command()) < 1e-6)
        return {b.value(), b.known ? b.lower : NAN, b.known ? b.upper : NAN};
      if (previous_index == i - 1) {
        const auto &a = reference[previous_index];
        if (a.command() <= voltage && voltage <= b.command()) {
          double w = (voltage - a.command()) / (b.command() - a.command());
          return {a.value() * (1 - w) + b.value() * w, a.known && b.known ? std::min(a.lower, b.lower) : NAN,
                  a.known && b.known ? std::max(a.upper, b.upper) : NAN};
        }
      }
      previous_index = i;
    }
    return missing;
  }
  void archive() {
    if (!baseline.valid && !reference_id)
      return;
    if (archives.size() == 4)
      archives.pop_front();
    archives.push_back(Archive{generation, baseline, reference});
  }
  static void trim(std::deque<Block> &blocks, double seconds) {
    double total = 0;
    for (const auto &b : blocks)
      total += b.seconds;
    while (!blocks.empty() && total - blocks.front().seconds >= seconds - 1e-9) {
      total -= blocks.front().seconds;
      blocks.pop_front();
    }
    if (!blocks.empty() && total > seconds) {
      auto &first = blocks.front();
      double remaining = first.seconds - total + seconds;
      first.start = first.end - (first.end - first.start) * remaining / first.seconds;
      first.seconds = remaining;
    }
  }
  static Score score(const std::deque<Block> &blocks, double minimum, unsigned min_cycles) {
    Score s;
    std::vector<uint32_t> cycles;
    double measured = 0, reference = 0, lower = 0, upper = 0, rlower = 0, rupper = 0;
    bool known = true;
    for (const auto &b : blocks) {
      s.seconds += b.seconds;
      measured += b.measured * b.seconds;
      reference += b.reference * b.seconds;
      lower += b.lower * b.seconds;
      upper += b.upper * b.seconds;
      rlower += b.reference_lower * b.seconds;
      rupper += b.reference_upper * b.seconds;
      known = known && std::isfinite(b.lower) && std::isfinite(b.reference_lower);
      if (std::find(cycles.begin(), cycles.end(), b.cycle) == cycles.end())
        cycles.push_back(b.cycle);
    }
    s.cycles = cycles.size();
    if (s.seconds < minimum - 1e-6 || s.cycles < min_cycles || reference <= 0)
      return s;
    s.value = 100 * measured / reference;
    s.status = "provisional";
    if (known) {
      if (rlower <= 0) {
        s.value = NAN;
        s.status = "inconclusive";
        return s;
      }
      s.lower = 1e30;
      s.upper = -1e30;
      for (double m : {lower, upper})
        for (double r : {rlower, rupper}) {
          s.lower = std::min(s.lower, 100 * m / r);
          s.upper = std::max(s.upper, 100 * m / r);
        }
      s.status = "valid";
    }
    return s;
  }
  struct Writer {
    std::vector<uint8_t> bytes;
    template <class T>
    void pod(const T &value) {
      static_assert(std::is_trivially_copyable<T>::value, "Persistent data must be value-only");
      auto begin = reinterpret_cast<const uint8_t *>(&value);
      bytes.insert(bytes.end(), begin, begin + sizeof(T));
    }
    void bins(const Bins &value) {
      uint32_t count = 0;
      for (const auto &b : value)
        if (b.used)
          count++;
      pod(count);
      for (uint32_t i = 0; i < 21; i++)
        if (value[i].used) {
          pod(i);
          pod(value[i]);
        }
    }
    void blocks(const std::deque<Block> &value) {
      pod(uint32_t(value.size()));
      for (const auto &b : value)
        pod(b);
    }
  };
  struct Reader {
    const std::vector<uint8_t> &bytes;
    size_t offset{0};
    bool ok{true};
    explicit Reader(const std::vector<uint8_t> &bytes) : bytes(bytes) {}
    template <class T>
    void pod(T &value) {
      if (!ok || offset + sizeof(T) > bytes.size()) {
        ok = false;
        return;
      }
      std::memcpy(&value, bytes.data() + offset, sizeof(T));
      offset += sizeof(T);
    }
    void bins(Bins &value) {
      uint32_t count = 0;
      pod(count);
      if (count > 21) {
        ok = false;
        return;
      }
      for (unsigned j = 0; j < count; j++) {
        uint32_t index = 0;
        pod(index);
        if (index > 20 || value[index].used) {
          ok = false;
          return;
        }
        pod(value[index]);
      }
    }
    void blocks(std::deque<Block> &value, unsigned bound) {
      uint32_t count = 0;
      pod(count);
      if (count > bound) {
        ok = false;
        return;
      }
      for (unsigned i = 0; i < count; i++) {
        Block b;
        pod(b);
        value.push_back(b);
      }
    }
  };
  bool valid_state() const {
    if (baseline.valid &&
        (!std::isfinite(baseline.offset) || !std::isfinite(baseline.residual) || baseline.residual < 0))
      return false;
    if (reference_id && !baseline.valid)
      return false;
    for (const auto &b : reference)
      if (b.used && (!b.ready() || !std::isfinite(b.value()) || b.cycle_count > 3))
        return false;
    if (candidate.kind && candidate.generation != generation)
      return false;
    double end = -1e30;
    for (const auto &b : evidence) {
      if (!reference_id || b.generation != generation || !std::isfinite(b.start) || !std::isfinite(b.end) ||
          !std::isfinite(b.measured) || !std::isfinite(b.reference) || b.reference <= 0 || b.seconds <= 0 ||
          b.seconds > config.max_age || b.end <= b.start || std::abs(b.end - b.start - b.seconds) > 5 + 1e-6 ||
          b.start < end)
        return false;
      end = b.end;
    }
    return true;
  }
};

}  // namespace hapsic_diagnostics
}  // namespace esphome
