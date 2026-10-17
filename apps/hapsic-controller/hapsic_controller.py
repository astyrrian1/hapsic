import json
import math
import os
import queue
import tempfile
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from itertools import product
from pathlib import Path
from uuid import uuid4

import appdaemon.plugins.hass.hassapi as hass


class DeliveryObserver:
    """Diagnostic-only SI observer. Never receives a controller or actuator object.

    Saturation pressure and dry-air specific volume follow ASHRAE 2017,
    independently checked against PsychroLib 2.5.0:
    https://psychrometrics.github.io/psychrolib/api_docs.html#psychrolib.GetMoistAirVolume
    """

    AIR_KEYS = ("upstream_t", "upstream_rh", "downstream_t", "downstream_rh", "flow")
    KEYS = AIR_KEYS + ("pressure", "command")
    DEFAULTS = dict(algorithm_version="delivery-si-1", airflow_basis="unverified", reporting_verified=False,
                    max_age=120.0, pressure_max_age=900.0, max_skew=30.0,
                    minimum_reports=3, stable_seconds=300.0, warmup_seconds=900.0,
                    off_seconds=900.0, baseline_seconds=600.0, minimum_flow=50.0,
                    command_range=0.2, flow_range=0.05, temperature_slope=0.2,
                    moisture_slope=0.0001, warning=60.0, recovery=65.0,
                    max_evidence=10080, max_window=512, site_pressure_pa=88600.0)

    def __init__(self, config=None):
        self.config = {**self.DEFAULTS, **deepcopy(config or {})}
        for key in ("max_age", "pressure_max_age", "max_skew", "stable_seconds", "warmup_seconds",
                    "off_seconds", "baseline_seconds", "minimum_flow", "command_range", "flow_range",
                    "temperature_slope", "moisture_slope", "max_evidence", "max_window", "site_pressure_pa"):
            if not self._finite(self.config[key]) or self.config[key] <= 0:
                raise ValueError(f"Invalid diagnostic setting: {key}")
        if not 0 < self.config["warning"] < self.config["recovery"]:
            raise ValueError("Diagnostic recovery threshold must exceed warning")
        self.config_id = sha256(json.dumps(self.config, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
        self.generation = self.config_id
        self.baseline = None
        self.reference = None
        self.candidate = None
        self.revision = 0
        self.archives = deque(maxlen=4)
        self.evidence = deque()
        self.window = deque()
        self.delivery_window = deque()
        self.previous = None
        self.last_mono = None
        self.last_utc = None
        self.clock_valid = False
        self.clock_anchor = None
        self.clock_recovery = None
        self.zero_since = None
        self.demand_since = None
        self.cycle = 0
        self.off_period = 0
        self.off_period_open = False
        self.quarantined_evidence = []
        self.last_zero_end = None
        self.qualified_seconds = 0.0
        self.reason = "STARTUP"
        self.last_valid = {}
        self.alert_episode = 0
        self.alert_active = False
        self.alert_snapshot = None
        self.exclusions = {}
        self.cursors = {}
        self.source_identity = None
        self.last_measurement = self._missing("STARTUP")

    @staticmethod
    def _finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    @staticmethod
    def _missing(reason):
        return dict(status="unavailable", reason=reason, eligible=False, raw_kg_h=None,
                    corrected_kg_h=None, lower_kg_h=None, upper_kg_h=None, uncertainty_kg_h=None)

    @staticmethod
    def _convert(value, unit, key):
        if key.endswith("_t"):
            if unit in ("°C", "C"): return value, 1.0
            if unit in ("°F", "F"): return (value - 32.0) / 1.8, 1 / 1.8
        elif key.endswith("_rh") and unit == "%": return value, 1.0
        elif key == "flow":
            if unit in ("m³/h", "m3/h"): return value, 1.0
            if unit == "cfm": return value * 1.69901079552, 1.69901079552
        elif key == "pressure":
            factors = {"Pa": 1.0, "kPa": 1000.0, "hPa": 100.0, "mbar": 100.0, "inHg": 3386.389}
            if unit in factors: return value * factors[unit], factors[unit]
        elif key == "command" and unit == "V": return value, 1.0
        raise ValueError(f"Unsupported diagnostic unit: {key}")

    @staticmethod
    def humidity_ratio(temp, rh, pressure):
        if not -80 <= temp <= 100 or not 0 <= rh <= 100 or not 20000 <= pressure <= 120000:
            raise ValueError("Psychrometric input domain")
        kelvin = temp + 273.15
        if temp <= 0.01:
            log_p = (-5674.5359 / kelvin + 6.3925247 - 0.009677843 * kelvin
                     + 0.00000062215701 * kelvin ** 2 + 0.0000000020747825 * kelvin ** 3
                     - 0.0000000000009484024 * kelvin ** 4 + 4.1635019 * math.log(kelvin))
        else:
            log_p = (-5800.2206 / kelvin + 1.3914993 - 0.048640239 * kelvin
                     + 0.000041764768 * kelvin ** 2 - 0.000000014452093 * kelvin ** 3
                     + 6.5459673 * math.log(kelvin))
        vapor = math.exp(log_p) * rh / 100.0
        if vapor >= pressure: raise ValueError("Vapor pressure exceeds absolute pressure")
        return 0.621945 * vapor / (pressure - vapor)

    @classmethod
    def _rates(cls, values, offset):
        ut, ur, dt, dr, flow, pressure = values
        upstream = cls.humidity_ratio(ut, ur, pressure)
        downstream = cls.humidity_ratio(dt, dr, pressure)
        volume = 287.042 * (ut + 273.15) * (1 + 1.607858 * upstream) / pressure
        mass = flow / volume
        return mass * (downstream - upstream), mass * (downstream - upstream - offset), downstream - upstream

    def measure(self, snapshot, now):
        try:
            values, errors, times, ids = [], [], [], []
            verified = self.config["reporting_verified"] is True and self.config["airflow_basis"] == "actual_upstream"
            pressure_fallback = "pressure" not in snapshot
            for key in self.KEYS:
                if key == "pressure" and pressure_fallback:
                    values.append(self.config["site_pressure_pa"])
                    errors.append(None)
                    verified = False
                    continue
                item = snapshot[key]
                if not self._finite(item["value"]) or not self._finite(item["reported_at"]):
                    raise ValueError("Nonfinite source")
                age = now - item["reported_at"]
                max_age = self.config["pressure_max_age"] if key == "pressure" else self.config["max_age"]
                if not 0 <= age <= (15 if key == "command" else max_age):
                    raise ValueError("Stale or future source")
                value, factor = self._convert(item["value"], item["unit"], key)
                if key == "command":
                    if (not 0 <= value <= 10 or item.get("fault", False) or item.get("uncertain", False)
                            or item.get("verified") is not True or not item.get("source")):
                        raise ValueError("Command unavailable or faulted")
                    continue
                values.append(value)
                accuracy = item.get("accuracy")
                errors.append(abs(factor) * accuracy if self._finite(accuracy) and accuracy >= 0 else None)
                verified = verified and item.get("verified") is True and bool(item.get("source"))
                if key in self.AIR_KEYS:
                    times.append(item["reported_at"])
                    ids.append(str(item["report_id"]))
            if max(times) - min(times) > self.config["max_skew"]:
                raise ValueError("Source timestamp skew")
            if values[4] < 0: raise ValueError("Negative flow")
            offset = self.baseline["offset"] if self.baseline else 0.0
            raw, corrected, delta = self._rates(values, offset)
            lower = upper = uncertainty = None
            if all(error is not None for error in errors):
                # Enclose each w and specific-volume term independently. This
                # preserves correlated biases conservatively, without 1/sqrt(N).
                bounds = [(v - e, v + e) for v, e in zip(values, errors)]
                wu, wd, volumes = [], [], []
                for temp, rh, pressure in product(bounds[0], bounds[1], bounds[5]):
                    w = self.humidity_ratio(temp, min(100, max(0, rh)), pressure)
                    wu.append(w)
                    volumes.append(287.042 * (temp + 273.15) * (1 + 1.607858 * w) / pressure)
                for temp, rh, pressure in product(bounds[2], bounds[3], bounds[5]):
                    wd.append(self.humidity_ratio(temp, min(100, max(0, rh)), pressure))
                offset_error = self.baseline.get("residual", 0.0) if self.baseline else 0.0
                deltas = (min(wd) - max(wu) - offset - offset_error,
                          max(wd) - min(wu) - offset + offset_error)
                masses = (max(0, bounds[4][0]) / max(volumes), bounds[4][1] / min(volumes))
                rates = [m * d for m, d in product(masses, deltas)]
                lower, upper = min(rates), max(rates)
                uncertainty = max(corrected - lower, upper - corrected)
            reason = "OK" if verified else "UNVERIFIED_PROVENANCE_OR_AIRFLOW"
            if pressure_fallback: reason = "SITE_PRESSURE_ESTIMATE"
            if self.baseline and not self.baseline.get("uncertainty_known", False):
                lower = upper = uncertainty = None
            if self.baseline and snapshot["command"]["value"] > 0 and upper is not None and upper < 0:
                reason, verified = "MEASUREMENT_MISMATCH", False
            if reason == "OK" and self.baseline and not self._in_envelope(
                    dict(upstream_t=values[0], upstream_rh=values[1], flow=values[4])):
                reason = "OUTSIDE_BASELINE_ENVELOPE"
            return dict(status="valid" if verified and uncertainty is not None and reason == "OK" else "provisional",
                        reason=reason,
                        eligible=bool(verified), raw_kg_h=raw, corrected_kg_h=corrected,
                        lower_kg_h=lower, upper_kg_h=upper, uncertainty_kg_h=uncertainty,
                        delta_uncertainty=(uncertainty / (values[4] /
                                           (287.042 * (values[0] + 273.15) *
                                            (1 + 1.607858 * self.humidity_ratio(values[0], values[1], values[5])) /
                                            values[5]))) if uncertainty is not None and values[4] > 0 else None,
                        delta_w=delta, upstream_t=values[0], upstream_rh=values[1], flow=values[4],
                        report_ids=ids, source_times=times, commanded_voltage=snapshot["command"]["value"])
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
            return self._missing("INVALID_OR_MISSING_SOURCE")

    @staticmethod
    def _slope(rows, key):
        mean_t = sum(row["mono"] for row in rows) / len(rows)
        mean_v = sum(row[key] for row in rows) / len(rows)
        denominator = sum((row["mono"] - mean_t) ** 2 for row in rows)
        return (60 * sum((row["mono"] - mean_t) * (row[key] - mean_v) for row in rows) / denominator
                if denominator else 0.0)

    def _stable(self, rows, seconds):
        if not rows or rows[-1]["mono"] - rows[0]["mono"] < seconds - 1e-6: return False
        for index in range(5):
            if len({r["report_ids"][index] for r in rows}) < self.config["minimum_reports"]: return False
        volts = [row["commanded_voltage"] for row in rows]
        flow = [row["flow"] for row in rows]
        return (max(volts) - min(volts) <= self.config["command_range"] + 1e-9
                and max(flow) - min(flow) <= self.config["flow_range"] * sum(flow) / len(flow) + 1e-9
                and abs(self._slope(rows, "upstream_t")) <= self.config["temperature_slope"] + 1e-9
                and abs(self._slope(rows, "delta_w")) <= self.config["moisture_slope"] + 1e-12)

    def interrupt(self, reason):
        self.previous = None
        self.window.clear()
        self.delivery_window.clear()
        self.qualified_seconds = 0.0
        self.reason = reason
        self.exclusions[reason] = self.exclusions.get(reason, 0) + 1

    def _quarantine(self):
        # Preserve unscored mass/time summaries by cycle, not a second full ring.
        # Ambiguous UTC is never used again for retention or scoring.
        grouped = {}
        for block in self.evidence:
            cycle = block["cycle"]
            if cycle not in grouped:
                grouped[cycle] = deepcopy(block)
                continue
            merged = grouped[cycle]
            total = merged["seconds"] + block["seconds"]
            for key in ("measured", "lower", "upper", "reference", "reference_lower", "reference_upper"):
                merged[key] = ((merged[key] * merged["seconds"] + block[key] * block["seconds"]) / total
                               if merged[key] is not None and block[key] is not None else None)
            merged.update(end=block["end"], seconds=total)
        if grouped: self.quarantined_evidence = list(grouped.values())

    def _clock(self, mono, utc, synced):
        if not self._finite(mono) or (self.last_mono is not None and mono <= self.last_mono): return False
        step = (self.last_mono is not None and self._finite(utc) and self._finite(self.last_utc)
                and abs((utc - self.last_utc) - (mono - self.last_mono)) > 5)
        self.last_mono = mono
        if not synced or not self._finite(utc):
            self.clock_valid = False
            self.clock_recovery = mono
        elif self.clock_anchor is None:
            self.clock_anchor = utc - mono
            self.clock_valid = True
        elif abs(utc - mono - self.clock_anchor) > 5 or step:
            self.clock_valid = False
            self.clock_anchor = utc - mono
            self.clock_recovery = mono
            self._quarantine()
            self.evidence.clear()
        elif self.clock_recovery is not None and mono - self.clock_recovery >= 30:
            self.clock_valid = True
            self.clock_recovery = None
        self.last_utc = utc
        if self.clock_valid and self.evidence and self.evidence[-1]["end"] > utc + 5:
            self._quarantine()
            self.evidence.clear()
        return self.clock_valid

    def observe(self, snapshot, mono, utc, *, synced=False):
        if not self._clock(mono, utc, synced):
            self.zero_since = self.demand_since = None
            self.interrupt("CLOCK_UNSYNCHRONIZED")
            self.last_measurement = self._missing("CLOCK_UNSYNCHRONIZED")
            return self.telemetry(utc)
        result = self.measure(snapshot, utc)
        self.last_measurement = result
        if not result["eligible"] or result.get("flow", 0) < self.config["minimum_flow"]:
            self.zero_since = None
            self.demand_since = None
            self.interrupt(result["reason"] if not result["eligible"] else "LOW_FLOW")
            return self.telemetry(utc)
        identity = {key: [snapshot[key].get(field) for field in ("source", "unit", "accuracy")]
                    for key in self.KEYS if key in snapshot}
        if self.source_identity is not None and identity != self.source_identity:
            self.command("baseline_reset")
            self.source_identity = identity
            self.interrupt("SOURCE_IDENTITY_CHANGED")
            self.last_measurement = self._missing("SOURCE_IDENTITY_CHANGED")
            return self.telemetry(utc)
        self.source_identity = identity
        row = {**result, "mono": mono, "utc": utc}
        voltage = row["commanded_voltage"]
        if self.previous and self.previous["commanded_voltage"] != voltage:
            if (self.previous["commanded_voltage"] == 0) != (voltage == 0):
                self.interrupt("COMMAND_INTERRUPTED")
        if self.previous and row["report_ids"] == self.previous["report_ids"]:
            return self.telemetry(utc)
        if self.previous and (mono - self.previous["mono"] > self.config["max_age"]
                              or utc <= self.previous["utc"]
                              or any(a < b for a, b in zip(row["source_times"], self.previous["source_times"]))):
            self.interrupt("OBSERVATION_GAP")
            self.zero_since = None
            self.demand_since = None
        if voltage == 0:
            if self.zero_since is None:
                self.zero_since = mono
                if not self.off_period_open: self.off_period += 1
                self.off_period_open = True
            self.last_zero_end = mono
            self.demand_since = None
        else:
            self.off_period_open = False
            if self.demand_since is None:
                off = mono - self.zero_since if self.zero_since is not None else None
                if off is not None and off >= self.config["off_seconds"]: self.cycle += 1
                self.demand_since = mono
                self.warmup_until = mono + self.config["warmup_seconds"] if off is None or off >= 60 else mono
            self.zero_since = None
        if voltage > 0 and mono < self.warmup_until:
            self.interrupt("WARMUP")
            self.previous = row
            return self.telemetry(utc)
        if self.previous and (self.previous["commanded_voltage"] == 0) != (voltage == 0):
            self.interrupt("COMMAND_INTERRUPTED")
        self.window.append(row)
        duration = self.config["baseline_seconds"] if voltage == 0 else self.config["stable_seconds"]
        while len(self.window) > 1 and self.window[1]["mono"] <= mono - duration:
            self.window.popleft()
        if len(self.window) > self.config["max_window"]:
            self.interrupt("CAPACITY_EXHAUSTED")
        stable = self._stable(self.window, duration)
        prior = self.previous
        previously_qualified = self.reason == "QUALIFIED"
        self.reason = "QUALIFIED" if stable else "STABILIZING"
        if voltage == 0:
            self.delivery_window.clear()
            self.qualified_seconds = 0.0
            if stable and mono - self.zero_since >= self.config["off_seconds"] + duration:
                self._collect_baseline(row)
        elif stable and previously_qualified and prior:
            seconds = mono - prior["mono"]
            self.qualified_seconds += seconds
            if self._in_envelope(row) and self._in_envelope(prior):
                self._collect_reference(prior, seconds)
                matched = self._reference_rate(prior["commanded_voltage"], prior["flow"])
                if matched:
                    block = dict(start=prior["utc"], end=utc, seconds=seconds, cycle=self.cycle,
                                 generation=self.generation, measured=prior["corrected_kg_h"],
                                 lower=prior["lower_kg_h"], upper=prior["upper_kg_h"],
                                 reference=matched["value"], reference_lower=matched["lower"],
                                 reference_upper=matched["upper"])
                    self.delivery_window.append(block)
                    self._trim(self.delivery_window, 300)
                    if voltage >= 9 and prior["commanded_voltage"] >= 9:
                        self._append_evidence(block)
                else:
                    self.delivery_window.clear()
            else:
                self.delivery_window.clear()
                self.exclusions["OUTSIDE_BASELINE_ENVELOPE"] = self.exclusions.get("OUTSIDE_BASELINE_ENVELOPE", 0) + 1
        else:
            self.delivery_window.clear()
        self.previous = row
        self.cursors = dict(zip(self.AIR_KEYS, row["report_ids"]))
        return self.telemetry(utc)

    def _in_envelope(self, row):
        return bool(self.baseline) and all(
            lo - 1e-9 <= row[key] <= hi + 1e-9 for key, (lo, hi) in self.baseline["envelope"].items())

    def _collect_baseline(self, row):
        candidate = self.candidate
        if not candidate or candidate["kind"] != "baseline": return
        if any(window["period"] == self.off_period for window in candidate["windows"]): return
        if len(candidate["windows"]) >= 4: return
        rows = list(self.window)
        durations = [b["mono"] - a["mono"] for a, b in zip(rows, rows[1:])]
        mean = sum(a["delta_w"] * dt for a, dt in zip(rows, durations)) / sum(durations)
        window = dict(period=self.off_period, mean=mean, minimum=min(r["delta_w"] for r in rows),
                      maximum=max(r["delta_w"] for r in rows),
                      uncertainty_known=all(r["uncertainty_kg_h"] is not None for r in rows),
                      error=max(r["delta_uncertainty"] or 0 for r in rows),
                      envelope={key: [min(r[key] for r in rows), max(r[key] for r in rows)]
                                for key in ("upstream_t", "upstream_rh", "flow")})
        candidate["windows"].append(window)
        if len(candidate["windows"]) >= 3:
            training = candidate["windows"][:3]
            candidate["offset"] = sorted(w["mean"] for w in training)[1]
            candidate["residual"] = max(abs(w[k] - candidate["offset"]) for w in training
                                        for k in ("minimum", "maximum"))
            candidate["ready"] = (len(candidate["windows"]) == 4 and
                                  abs(mean - candidate["offset"]) <= candidate["residual"] + window["error"] + 1e-12)

    def command(self, action, revision=None, *, healthy=False):
        if action in ("baseline_start", "reference_start"):
            if action == "reference_start" and (not self.baseline or not healthy):
                raise ValueError("Reference requires accepted baseline and owner-confirmed healthy operation")
            self.revision += 1
            self.candidate = dict(kind=action.split("_")[0], revision=self.revision, ready=False,
                                  generation=self.generation, windows=[], bins={}, healthy=healthy)
            self.interrupt("CALIBRATION_STARTED")
        elif action == "cancel":
            self.candidate = None
        elif action in ("baseline_accept", "reference_accept"):
            candidate = self.review()
            if (type(revision) is not int or not candidate or candidate["kind"] != action.split("_")[0]
                    or candidate["revision"] != revision or not candidate["ready"]
                    or candidate["generation"] != self.generation):
                raise ValueError("Candidate is incomplete, incompatible, or stale")
            self._archive()
            if action == "baseline_accept":
                training = candidate["windows"][:3]
                self.baseline = dict(offset=candidate["offset"], residual=candidate["residual"],
                                     uncertainty_known=all(w["uncertainty_known"] for w in candidate["windows"]),
                                     envelope={key: [min(w["envelope"][key][0] for w in training),
                                                     max(w["envelope"][key][1] for w in training)]
                                               for key in ("upstream_t", "upstream_rh", "flow")})
                self.generation = sha256(json.dumps([self.config_id, self.revision, self.baseline],
                                                    sort_keys=True).encode()).hexdigest()[:16]
                self.reference = None
            else:
                self.reference = deepcopy(candidate)
                self.reference["bins"] = {k: b for k, b in self.reference["bins"].items() if b["ready"]}
            self.candidate = None
            self.evidence.clear()
            self.quarantined_evidence.clear()
            self.alert_active = False
            self.alert_snapshot = None
            self.last_valid.clear()
            self.interrupt("CALIBRATION_ACCEPTED")
        elif action in ("baseline_reset", "reference_reset"):
            self._archive()
            self.revision += 1
            if action == "baseline_reset":
                self.baseline = None
                self.generation = sha256(f"{self.config_id}:{self.revision}".encode()).hexdigest()[:16]
            self.reference = self.candidate = None
            self.evidence.clear()
            self.quarantined_evidence.clear()
            self.alert_active = False
            self.alert_snapshot = None
            self.last_valid.clear()
            self.interrupt("CALIBRATION_RESET")
        else:
            raise ValueError("Unknown diagnostic action")
        return self.review()

    def _archive(self):
        if self.baseline or self.reference:
            self.archives.append(deepcopy(dict(generation=self.generation, baseline=self.baseline,
                                               reference=self.reference)))

    def _collect_reference(self, row, seconds):
        candidate = self.candidate
        if not candidate or candidate["kind"] != "reference": return
        index = str(int(math.floor(row["commanded_voltage"] * 2 + 0.5)))
        bins = candidate["bins"]
        bucket = bins.setdefault(index, dict(seconds=0.0, cycles=[], voltage=0.0, flow=0.0, output=0.0,
                                            minimum_voltage=row["commanded_voltage"],
                                            maximum_voltage=row["commanded_voltage"],
                                            minimum_flow=row["flow"], maximum_flow=row["flow"],
                                            lower=None, upper=None, uncertainty_known=True, minimum_output=1e30,
                                            maximum_output=-1e30))
        if bucket["seconds"] >= 1800 and len(bucket["cycles"]) >= 3: return
        vmin, vmax = min(bucket["minimum_voltage"], row["commanded_voltage"]), max(
            bucket["maximum_voltage"], row["commanded_voltage"])
        fmin, fmax = min(bucket["minimum_flow"], row["flow"]), max(bucket["maximum_flow"], row["flow"])
        mean_flow = (bucket["flow"] + row["flow"] * seconds) / (bucket["seconds"] + seconds)
        if vmax - vmin > 0.2 + 1e-9 or fmax - fmin > 0.1 * mean_flow + 1e-9: return
        bucket.update(minimum_voltage=vmin, maximum_voltage=vmax, minimum_flow=fmin, maximum_flow=fmax)
        bucket["seconds"] += seconds
        if self.cycle not in bucket["cycles"]: bucket["cycles"].append(self.cycle)
        for key, source in (("voltage", "commanded_voltage"), ("flow", "flow"), ("output", "corrected_kg_h")):
            bucket[key] += row[source] * seconds
        bucket["minimum_output"] = min(bucket["minimum_output"], row["corrected_kg_h"])
        bucket["maximum_output"] = max(bucket["maximum_output"], row["corrected_kg_h"])
        bucket["uncertainty_known"] &= row["lower_kg_h"] is not None
        if row["lower_kg_h"] is not None:
            bucket["lower"] = min(bucket["lower"] if bucket["lower"] is not None else 1e30, row["lower_kg_h"])
            bucket["upper"] = max(bucket["upper"] if bucket["upper"] is not None else -1e30, row["upper_kg_h"])

    def review(self):
        candidate = deepcopy(self.candidate)
        if candidate and candidate["kind"] == "reference":
            for bucket in candidate["bins"].values():
                seconds = bucket["seconds"]
                bucket["ready"] = (seconds >= 1800 and len(bucket["cycles"]) >= 3 and bucket["output"] > 0)
                bucket["value"] = bucket["output"] / seconds if seconds else None
                bucket["command"] = bucket["voltage"] / seconds if seconds else None
                bucket["airflow"] = bucket["flow"] / seconds if seconds else None
                if not bucket["uncertainty_known"]: bucket["lower"] = bucket["upper"] = None
                if bucket["lower"] is not None and bucket["lower"] <= 0: bucket["ready"] = False
            candidate["ready"] = any(b["ready"] for b in candidate["bins"].values())
        return candidate

    def _reference_rate(self, voltage, flow):
        if not self.reference or self.reference["generation"] != self.generation: return None
        bins = sorted((int(index), bucket) for index, bucket in self.reference["bins"].items())
        matched = [(idx, b) for idx, b in bins if abs(flow / b["airflow"] - 1) <= 0.1 + 1e-9]
        for _, bucket in matched:
            if abs(voltage - bucket["command"]) < 1e-6:
                return {"value": bucket["value"], "lower": bucket["lower"], "upper": bucket["upper"]}
        for (left_idx, left), (right_idx, right) in zip(matched, matched[1:]):
            if right_idx - left_idx != 1 or not left["command"] <= voltage <= right["command"]: continue
            weight = (voltage - left["command"]) / (right["command"] - left["command"])
            known = left["lower"] is not None and right["lower"] is not None
            return dict(value=left["value"] * (1 - weight) + right["value"] * weight,
                        lower=min(left["lower"], right["lower"]) if known else None,
                        upper=max(left["upper"], right["upper"]) if known else None)
        return None

    @staticmethod
    def _trim(blocks, seconds):
        total = sum(block["seconds"] for block in blocks)
        while blocks and total - blocks[0]["seconds"] >= seconds - 1e-9:
            total -= blocks.popleft()["seconds"]
        if blocks and total > seconds:
            old = blocks[0]
            remaining = old["seconds"] - total + seconds
            blocks[0] = {**old, "start": old["end"] - (old["end"] - old["start"]) * remaining / old["seconds"],
                         "seconds": remaining}

    def _append_evidence(self, block):
        self.evidence.append(deepcopy(block))
        self._trim(self.evidence, 7200)
        if len(self.evidence) > self.config["max_evidence"]:
            self.evidence.pop()
            self.interrupt("CAPACITY_EXHAUSTED")

    @staticmethod
    def _score(blocks, minimum, cycles=0):
        seconds = sum(b["seconds"] for b in blocks)
        result = dict(value=None, lower=None, upper=None, seconds=seconds, status="insufficient_data",
                      cycles=len({b["cycle"] for b in blocks}))
        if seconds < minimum - 1e-6 or result["cycles"] < cycles: return result
        measured = sum(b["measured"] * b["seconds"] for b in blocks)
        reference = sum(b["reference"] * b["seconds"] for b in blocks)
        if reference <= 0: return result
        result.update(value=100 * measured / reference, status="provisional")
        if all(b["lower"] is not None and b["reference_lower"] is not None for b in blocks):
            lo_ref = sum(b["reference_lower"] * b["seconds"] for b in blocks)
            hi_ref = sum(b["reference_upper"] * b["seconds"] for b in blocks)
            if lo_ref <= 0: return {**result, "value": None, "status": "inconclusive"}
            lo = sum(b["lower"] * b["seconds"] for b in blocks)
            hi = sum(b["upper"] * b["seconds"] for b in blocks)
            ratios = [100 * a / b for a, b in product((lo, hi), (lo_ref, hi_ref))]
            result.update(lower=min(ratios), upper=max(ratios), status="valid")
        return result

    def telemetry(self, now):
        safe_now = now if self.clock_valid and self._finite(now) else None
        while self.evidence and safe_now is not None and self.evidence[0]["end"] < safe_now - 7 * 86400:
            self.evidence.popleft()
        if self.evidence and safe_now is not None and self.evidence[0]["start"] < safe_now - 7 * 86400:
            first = self.evidence[0]
            cutoff = now - 7 * 86400
            first["seconds"] *= (first["end"] - cutoff) / (first["end"] - first["start"])
            first["start"] = cutoff
        delivery = self._score(self.delivery_window, 300) if self.reason == "QUALIFIED" else self._score([], 300)
        canister = self._score(self.evidence, 1800, 3)
        canister["ready"] = canister["seconds"] >= 7200 and canister["cycles"] >= 3
        if not canister["ready"] and canister["value"] is not None: canister["status"] = "provisional"
        if self.evidence:
            canister["last_observed_at"] = self.evidence[-1]["end"]
            if safe_now is not None and now - self.evidence[-1]["end"] > 86400:
                canister.update(value=None, lower=None, upper=None, status="stale")
        if not self.clock_valid:
            delivery.update(value=None, status="unavailable")
            canister.update(value=None, lower=None, upper=None, status="unavailable")
        decision = "inconclusive"
        if canister["status"] == "valid" and canister["ready"]:
            if canister["upper"] < self.config["warning"]:
                if not self.alert_active:
                    self.alert_episode += 1
                    self.alert_snapshot = deepcopy(canister)
                self.alert_active = True
                decision = "warn"
            elif canister["lower"] > self.config["recovery"]:
                self.alert_active = False
                decision = "clear"
        for key, metric in (("delivery", delivery), ("canister", canister)):
            if metric["value"] is not None:
                self.last_valid[key] = {**metric, "at": (self.evidence[-1]["end"] if key == "canister"
                                                        else self.delivery_window[-1]["end"])}
        return dict(schema_version=1, generation=self.generation, clock_valid=self.clock_valid,
                    observed_at=max(self.last_measurement.get("source_times", []), default=None),
                    source_contract=deepcopy(self.source_identity),
                    measurement=deepcopy(self.last_measurement),
                    qualification=dict(stable=self.reason == "QUALIFIED", reason=self.reason,
                                       qualified_seconds=self.qualified_seconds, cycle_id=self.cycle),
                    calibration=dict(status=("uncalibrated" if not self.reference else
                                             "valid" if self.baseline["uncertainty_known"] else "provisional"),
                                     reference_id=self.reference["revision"] if self.reference else None,
                                     baseline=deepcopy(self.baseline), reference=deepcopy(self.reference),
                                     coverage=list(self.reference["bins"]) if self.reference else [],
                                     candidate=self.review()),
                    delivery=delivery, canister=canister, last_valid=deepcopy(self.last_valid),
                    alert=dict(decision=decision, episode=self.alert_episode, active=self.alert_active,
                               warning=self.config["warning"], recovery=self.config["recovery"],
                               evidence=deepcopy(self.alert_snapshot)),
                    exclusions=dict(self.exclusions))

    def export_state(self):
        return deepcopy(dict(schema_version=1, config_id=self.config_id, generation=self.generation,
                             revision=self.revision, baseline=self.baseline, reference=self.reference,
                             candidate=self.candidate, archives=list(self.archives), evidence=list(self.evidence),
                             cursors=self.cursors, cycle=self.cycle, off_period=self.off_period,
                             source_identity=self.source_identity,
                             off_period_open=self.off_period_open, quarantined_evidence=self.quarantined_evidence,
                             last_valid=self.last_valid, alert_episode=self.alert_episode,
                             alert_active=self.alert_active, alert_snapshot=self.alert_snapshot))

    def restore(self, state):
        if (state.get("schema_version") != 1 or state.get("config_id") != self.config_id
                or len(state.get("evidence", [])) > self.config["max_evidence"]):
            raise ValueError("Incompatible diagnostic storage")
        # JSON roundtrip enforces finite numbers and detaches caller-owned state.
        state = json.loads(json.dumps(state, allow_nan=False))
        self._validate_state(state)
        for key in ("generation", "revision", "baseline", "reference", "candidate", "cursors", "cycle", "off_period",
                    "last_valid", "alert_episode", "alert_active", "source_identity", "off_period_open",
                    "quarantined_evidence", "alert_snapshot"):
            setattr(self, key, state[key])
        self.evidence = deque(state["evidence"])
        self.archives = deque(state["archives"], maxlen=4)
        self.zero_since = self.demand_since = None
        self.interrupt("RESTART")

    @classmethod
    def _validate_state(cls, state):
        try:
            for key in ("revision", "cycle", "off_period", "alert_episode"):
                if type(state[key]) is not int or state[key] < 0: raise ValueError("Invalid counter")
            if not isinstance(state["generation"], str) or len(state["generation"]) != 16:
                raise ValueError("Invalid generation")
            if type(state["alert_active"]) is not bool: raise ValueError("Invalid alert")
            alert = state["alert_snapshot"]
            if alert is not None and (not isinstance(alert, dict) or alert.get("status") != "valid"
                                      or not all(cls._finite(alert.get(k)) for k in ("value", "lower", "upper"))
                                      or not alert["lower"] <= alert["value"] <= alert["upper"]
                                      or alert.get("seconds", 0) < 7200 or alert.get("cycles", 0) < 3):
                raise ValueError("Invalid warning evidence")
            if type(state["off_period_open"]) is not bool: raise ValueError("Invalid period")
            if not isinstance(state["quarantined_evidence"], list): raise ValueError("Invalid quarantine")
            if not isinstance(state["cursors"], dict) or not isinstance(state["last_valid"], dict):
                raise ValueError("Invalid cursor/history")
            if len(state["archives"]) > 4: raise ValueError("Invalid archives")
            if state["source_identity"] is not None and not isinstance(state["source_identity"], dict):
                raise ValueError("Invalid identity")
            baseline = state["baseline"]
            if baseline is not None:
                if (not cls._finite(baseline["offset"]) or not cls._finite(baseline["residual"])
                        or baseline["residual"] < 0 or type(baseline["uncertainty_known"]) is not bool):
                    raise ValueError("Invalid baseline")
                for key in ("upstream_t", "upstream_rh", "flow"):
                    lo, hi = baseline["envelope"][key]
                    if not cls._finite(lo) or not cls._finite(hi) or lo > hi: raise ValueError("Invalid envelope")
            reference = state["reference"]
            if reference is not None:
                if baseline is None or reference["generation"] != state["generation"]:
                    raise ValueError("Incompatible reference")
                if not 1 <= len(reference["bins"]) <= 21: raise ValueError("Invalid bins")
                for index, bucket in reference["bins"].items():
                    if not 0 <= int(index) <= 20 or bucket["seconds"] < 1800 or len(set(bucket["cycles"])) < 3:
                        raise ValueError("Invalid reference evidence")
                    for key in ("value", "command", "airflow"):
                        if not cls._finite(bucket[key]) or bucket[key] <= 0: raise ValueError("Invalid reference rate")
                    if bucket["lower"] is not None:
                        if not 0 < bucket["lower"] <= bucket["value"] <= bucket["upper"]:
                            raise ValueError("Invalid reference bounds")
            previous_end = -math.inf
            for block in state["evidence"]:
                if (block["generation"] != state["generation"] or reference is None
                        or not all(cls._finite(block[key]) for key in
                                   ("start", "end", "seconds", "measured", "reference"))
                        or block["start"] < previous_end or not 0 < block["seconds"] <= 120
                        or block["end"] <= block["start"]
                        or abs(block["end"] - block["start"] - block["seconds"]) > 5 + 1e-6
                        or block["reference"] <= 0):
                    raise ValueError("Invalid evidence block")
                for lo, hi in (("lower", "upper"), ("reference_lower", "reference_upper")):
                    if block[lo] is not None and (not cls._finite(block[lo]) or not cls._finite(block[hi])
                                                 or block[lo] > block[hi]):
                        raise ValueError("Invalid evidence bounds")
                previous_end = block["end"]
            if state["candidate"] is not None:
                candidate = state["candidate"]
                if (candidate["kind"] not in ("baseline", "reference") or candidate["generation"] != state["generation"]
                        or len(candidate["windows"]) > 4 or len(candidate["bins"]) > 21):
                    raise ValueError("Invalid candidate")
        except (KeyError, TypeError, OverflowError) as error:
            raise ValueError("Malformed diagnostic storage") from error


class DiagnosticStore:
    """Versioned atomic storage outside the HACS-installed source directory."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if self.path.stat().st_size > 8 * 1024 * 1024: raise ValueError("Diagnostic storage exceeds size bound")
        return json.loads(self.path.read_text(),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite")))

    def save(self, state):
        payload = json.dumps(state, allow_nan=False, separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)


class HapsicDiagnostics(hass.Hass):
    """Separately scheduled AppDaemon observer, with no actuator dependency.

    MQTT event/service API: https://appdaemon.readthedocs.io/en/latest/MQTT_API_REFERENCE.html
    Configure pin_app: true so callbacks serialize; only immutable persistence
    snapshots cross to the single I/O worker. Work queue is bounded to one save.
    """

    SOURCES = {
        "upstream_t": "sensor.zehnder_comfoair_q_a4cb9c_supply_air_temperature",
        "upstream_rh": "sensor.zehnder_comfoair_q_a4cb9c_supply_air_humidity",
        "downstream_t": "sensor.shelly0110dimg3_28372f3e866c_temperature_2",
        "downstream_rh": "sensor.shelly0110dimg3_28372f3e866c_input_100_analog",
        "flow": "sensor.zehnder_comfoair_q_a4cb9c_supply_fan_flow",
        "pressure": "sensor.gw1200b_absolute_pressure",
    }

    def initialize(self):
        self.boot_id = str(uuid4())
        self.connection_revision = 0
        self.mqtt_online = None
        self.sequence = 0
        self.producer = self.args.get("producer_id", "hapsic-python")
        self.namespace = self.args.get("mqtt_namespace", "mqtt")
        self.topic = self.args.get("diagnostics_topic", "hapsic/telemetry/diagnostics")
        self.control_topic = self.args.get("control_topic", "hapsic/telemetry/state")
        self.sources = {key: {"entity": entity, **self.args.get("sources", {}).get(key, {})}
                        for key, entity in self.SOURCES.items()}
        self.observer = DeliveryObserver({**self.args.get("diagnostic_config", {}), "sources": self.sources})
        base_path = Path(self.args.get("data_path", "/config/hapsic-data/diagnostics-v1.json"))
        # A changed binding/policy never overwrites the previous configuration's history.
        self.store = DiagnosticStore(base_path.with_name(
            f"{base_path.stem}-{self.observer.config_id}{base_path.suffix}"))
        self.commands = queue.Queue(maxsize=16)
        self.requests = deque(maxlen=64)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hapsic-diagnostic-storage")
        self.pending_save = None
        self.pending_acks = deque(maxlen=16)
        self.last_checkpoint = time.monotonic()
        self.control = None
        self.storage_error = None
        try:
            state = self.store.load()
            self.observer.restore(state)
            self.requests.extend(state.get("requests", []))
        except FileNotFoundError:
            pass
        except Exception as error:
            self.storage_error = type(error).__name__
            self.log(f"Diagnostic storage rejected: {type(error).__name__}; commissioning required", level="WARNING")
        try:
            self.listen_event(self.diagnostic_connectivity, "MQTT_MESSAGE", namespace=self.namespace)
        except Exception as error:
            self.log(f"Diagnostic connection events unavailable: {type(error).__name__}", level="WARNING")
        for topic in (self.control_topic, self.topic + "/command"):
            try:
                self.call_service("mqtt/subscribe", namespace=self.namespace, topic=topic)
                self.listen_event(self.diagnostic_message, "MQTT_MESSAGE", namespace=self.namespace, topic=topic)
            except Exception as error:
                self.log(f"Diagnostic MQTT unavailable: {type(error).__name__}", level="WARNING")
        self.run_every(self.diagnostic_tick, "now", 5)

    def diagnostic_connectivity(self, event, data, kwargs):
        state = data.get("state")
        if state not in ("Connected", "Disconnected"): return
        self.mqtt_online = state == "Connected"
        self.control = None
        self.observer.zero_since = self.observer.demand_since = None
        self.observer.interrupt("MQTT_CONNECTION_CHANGED")
        if self.mqtt_online:
            self.connection_revision += 1
            for topic in (self.control_topic, self.topic + "/command"):
                try:
                    self.call_service("mqtt/subscribe", namespace=self.namespace, topic=topic)
                except Exception as error:
                    self.log(f"Diagnostic resubscribe failed: {type(error).__name__}", level="WARNING")

    def diagnostic_message(self, event, data, kwargs):
        try:
            if data.get("retain", False): return
            payload = data.get("payload", "")
            if len(payload) > 32768: raise ValueError("Oversized diagnostic message")
            payload = json.loads(payload)
            if data["topic"] == self.control_topic:
                voltage = payload["io"]["steam_volts"]
                state = payload["fsm"]["state"]
                if state not in {"INITIALIZING", "STANDBY", "ACTIVE_CRUISE", "TURBO_PENDING", "ACTIVE_TURBO",
                                 "HYGIENIC_PURGE", "FAULT", "MAINTENANCE_LOCKOUT"}:
                    raise ValueError("Unknown controller state")
                fault = state == "FAULT"
                if not DeliveryObserver._finite(voltage) or not 0 <= voltage <= 10: raise ValueError("Invalid command")
                if self.control and (fault or voltage != self.control["value"]):
                    values = [r["commanded_voltage"] for r in self.observer.window] + [voltage, self.control["value"]]
                    if (fault or (voltage == 0) != (self.control["value"] == 0)
                            or max(values) - min(values) > self.observer.config["command_range"] + 1e-9):
                        self.observer.interrupt("COMMAND_EVENT")
                self.control = dict(value=voltage, unit="V", source=self.control_topic, verified=True,
                                    report_id=str(time.monotonic_ns()), reported_at=time.time(), fault=fault,
                                    controller_state=state)
            else:
                if (payload.get("schema_version") != 1 or payload.get("producer_id") != self.producer
                        or not isinstance(payload.get("request_id"), str) or len(payload["request_id"]) > 128):
                    raise ValueError("Invalid diagnostic command envelope")
                if payload["request_id"] in self.requests:
                    self._ack(dict(schema_version=1, producer_id=self.producer,
                                   request_id=payload["request_id"], status="accepted"))
                    return
                if (not payload["request_id"] or "\0" in payload["request_id"]
                        or not DeliveryObserver._finite(payload.get("issued_at"))
                        or not -5 <= time.time() - payload["issued_at"] <= 30):
                    raise ValueError("Expired command")
                self.commands.put_nowait((time.monotonic(), payload))
        except (ValueError, TypeError, KeyError, queue.Full, RecursionError):
            if data.get("topic") == self.control_topic:
                self.control = None
                self.observer.zero_since = self.observer.demand_since = None
            self.observer.interrupt("MESSAGE_REJECTED")

    @staticmethod
    def _timestamp(value):
        if DeliveryObserver._finite(value): return value
        if isinstance(value, str): return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        raise ValueError("Missing source timestamp")

    def _snapshot(self):
        snapshot = {}
        for key, binding in self.sources.items():
            state = self.get_state(binding["entity"], attribute="all")
            if not isinstance(state, dict): continue
            attrs = state.get("attributes", {})
            field = binding.get("timestamp_field", "last_reported")
            timestamp = attrs.get(field, state.get(field))
            try:
                value = float(state["state"])
                if not math.isfinite(value): continue
                unit = attrs.get("unit_of_measurement", binding.get("unit"))
                snapshot[key] = dict(value=value, unit=unit, reported_at=self._timestamp(timestamp),
                                     report_id=str(timestamp), source=binding["entity"],
                                     verified=binding.get("reporting_verified", False),
                                     accuracy=binding.get("accuracy"))
            except (ValueError, TypeError, KeyError):
                continue
        if self.control: snapshot["command"] = deepcopy(self.control)
        return snapshot

    def _publish(self, topic, payload):
        if self.mqtt_online is False: raise RuntimeError("Diagnostic MQTT disconnected")
        self.call_service("mqtt/publish", namespace=self.namespace, topic=topic, retain=False, qos=0,
                          payload=json.dumps(payload, allow_nan=False, separators=(",", ":")))

    def _save_completed(self):
        if self.pending_save is None or not self.pending_save[0].done(): return
        future, replacement, ack = self.pending_save
        self.pending_save = None
        try:
            future.result()
        except Exception as error:
            self.storage_error = type(error).__name__
            self.observer.interrupt("STORAGE_ERROR")
            if ack: self._ack({**ack, "status": "rejected", "reason": "STORAGE_ERROR"})
            return
        self.storage_error = None
        if replacement is not None:
            self.observer = replacement
            self.requests.append(ack["request_id"])
        if ack: self._ack({**ack, "status": "accepted"})

    def _ack(self, payload):
        try:
            self._publish(self.topic + "/ack", payload)
        except Exception:
            self.pending_acks.append(payload)

    def diagnostic_tick(self, kwargs):
        try:
            self._save_completed()
            if self.pending_acks:
                self._ack(self.pending_acks.popleft())
            if self.pending_save is None and not self.commands.empty():
                received, action = self.commands.get_nowait()
                ack = dict(request_id=action["request_id"], producer_id=self.producer, schema_version=1)
                try:
                    if time.monotonic() - received > 30 or action["request_id"] in self.requests:
                        raise ValueError("Expired or duplicate request")
                    replacement = DeliveryObserver(self.observer.config)
                    replacement.restore(self.observer.export_state())
                    replacement.command(action["action"], action.get("revision"), healthy=action.get("healthy") is True)
                    state = replacement.export_state()
                    state["requests"] = (list(self.requests) + [action["request_id"]])[-64:]
                    self.pending_save = (self.worker.submit(self.store.save, state), replacement, ack)
                    self.observer.interrupt("STORAGE_PENDING")
                except (ValueError, KeyError):
                    self._publish(self.topic + "/ack", {**ack, "status": "rejected", "reason": "INVALID_ACTION"})
            now, mono = time.time(), time.monotonic()
            snapshot = self._snapshot()
            self.sequence += 1
            self._publish(self.topic + "/inputs", dict(
                schema_version=1, producer_id=self.producer, boot_id=self.boot_id,
                sequence=self.sequence, connection_id=f"{self.boot_id}:{self.connection_revision}",
                published_at=now, samples=snapshot))
            accepting = self.pending_save is not None and self.pending_save[1] is not None
            if accepting:
                payload = self.observer.telemetry(now)
            else:
                payload = self.observer.observe(snapshot, mono, now,
                                                synced=self.args.get("clock_verified", False))
            if self.pending_save is None and mono - self.last_checkpoint >= 300:
                state = self.observer.export_state()
                state["requests"] = list(self.requests)
                self.pending_save = (self.worker.submit(self.store.save, state), None, None)
                self.last_checkpoint = mono
            payload.update(producer_id=self.producer, boot_id=self.boot_id, sequence=self.sequence,
                           published_at=now, storage_error=self.storage_error)
            self._publish(self.topic, payload)
        except Exception as error:
            self.observer.zero_since = self.observer.demand_since = None
            self.observer.interrupt("OBSERVER_ERROR")
            self.log(f"Diagnostic tick failed: {type(error).__name__}", level="WARNING")

    def terminate(self):
        self.worker.shutdown(wait=False, cancel_futures=True)


class HapsicController(hass.Hass):
    SHELLY_LIGHT_ENTITY = "light.shelly0110dimg3_28372f3e866c"
    SHELLY_DUCT_RH_ENTITY = "sensor.shelly0110dimg3_28372f3e866c_input_100_analog"
    HAPSIC_DUCT_TEMP_ENTITY = "sensor.hapsic_cleansed_post_steam_temp"
    HAPSIC_DUCT_RH_ENTITY = "sensor.hapsic_cleansed_post_steam_rh"
    SHELLY_OFFLINE_FAULT_SECONDS = 60.0
    SENSOR_LOSS_GRACE_SECONDS = 60.0

    def initialize(self):
        """
        HAPSIC 2.7.4
        Includes:
        1. Absolute Dew Point Setpoint Paradigm.
        2. Real-time Building Physics Mass Balance (1380 CFM50 / 17.0 N-Factor).
        3. 15-Minute Rolling Buffer for Structure Velocity.
        4. Loop A Feasibility Clamp & Loop B Sliding Safety Ceiling.
        5. Smart V_FF Cold-Start Jump & Derivative Boil-Detect Release.
        6. State-Based Asymmetric Batch Manager (Fast Down / Slow Up).
        7. [v2.2.4] Paradox Resolved: Loop A evaluates raw User Target.
        8. [v2.2.4] Thermodynamic Memory & 10.0V Ignition Strike.
        9. [v2.2.4] Bumpless Handoff Prime & Universal Directional Freezing.
        10. [v2.3.0] Native Notification Blueprints Integration Support.
        """
        # --- 2.1 Environmental Constants ---
        self.P_ATM = 88.6               # kPa (Hardcoded for stability)
        self.RHO = 0.065                # lbs/ft^3

        try:
            self.MAX_CAPACITY = float(self.get_state("input_number.humidifier_max_capacity"))
        except (ValueError, TypeError):
            self.MAX_CAPACITY = 2.7     # Fallback default

        self.MAX_DUCT_DP = 60.0         # °F
        self.MAX_VOLTAGE = 10.0         # V

        # --- State & Memory ---
        self.fsm_state = "STANDBY"
        self.steam_voltage = 0.0
        self.integrator_a = 0.0
        self.integrator_b = 0.0
        self.last_tick_ts = time.time()
        self.startup_ts = time.time()
        self.room_dp_buffer = []        # 15-minute rolling ring-buffer
        self.duct_dp_buffer = []        # 60-second rolling ring-buffer

        # --- 2.2 Signal Conditioning Memory (EMA) ---
        self.ema_alpha = 0.1
        self.ema_sensors = {
            "round_room": {"t": None, "rh": None},
            "kitchen": {"t": None, "rh": None},
            "element": {"t": None, "rh": None}
        }
        # Duct Plume Filter
        self.duct_ema_t = None
        self.duct_ema_rh = None

        # --- Operational Variables ---
        self.target_duct_dp = 0.0
        self.room_dp = 0.0
        self.target_room_dp = 0.0
        self.room_temp_avg = 0.0
        self.room_rh_avg = 0.0
        self.outdoor_dp = 0.0
        self.outdoor_w = 0.0
        self.room_w = 0.0
        self.supply_t = 0.0
        self.supply_rh = 0.0
        self.supply_w = 0.0
        self.supply_dp = 0.0
        self.duct_dp = 0.0
        self.duct_w = 0.0
        self.bypass_state = 0.0
        self.last_valid_room_dp_time = 0
        self.last_valid_supply_w_time = 0
        self.last_valid_outdoor_time = 0
        self.last_valid_duct_time = 0
        self.last_valid_flow_time = 0
        self.shelly_offline_since = 0
        self.sensor_value_cache = {}
        self.pending_fault_reason = None
        self.duct_temp_fallback_active = False

        self.purge_ticks = 0
        self.turbo_wait_ticks = 0
        self.tick_counter = 0
        self.last_brightness = -1
        self.turbo_lockout_ticks = 0
        self.fault_clear_ticks = 0
        self.fault_reason = "NONE"
        self.clogged_filter_ticks = 0

        # --- Batch Manager & Thermodynamic Memory ---
        self.stasis_active = False
        self.stasis_timer = 0
        self.duct_derivative = 0.0
        self.upward_rate_ticks = 0
        self.downward_rate_ticks = 0
        self.boil_achieved = False
        self.zero_volt_ticks = 0

        # --- SCADA & Diagnostics Memory ---
        self.active_cruise_ticks = 0
        self.calc_steam_mass = 0.0
        self.calc_loss_vent = 0.0
        self.calc_flux = 0.0
        self.max_achievable_dp = 0.0
        self.is_target_infeasible = False
        self.chi_instant = 0.0

        # --- Economy Advisory (observe-only; never changes target) ---
        self.economy_negative_streak_sec = 0.0
        self.economy_severe_streak_sec = 0.0
        self.economy_recovery_streak_sec = 0.0
        self.economy_advisory_active = False
        self.economy_advisory_severe = False
        self.economy_steaming_active = False
        self.economy_useful_demand = False
        self.economy_passive_import_lbs_hr = 0.0
        self.economy_passive_export_lbs_hr = 0.0
        self.economy_passive_import_candidate = False
        self.economy_advisory_reason = "CLEAR"
        self.economy_advisory_target_delta = 0.0

        # Restore CHI EMA from persistent storage
        try:
            stored_chi = float(self.get_state("input_number.hapsic_chi_ema"))
            self.chi_ema = stored_chi if stored_chi > 0 else 1.0
        except (ValueError, TypeError):
            self.chi_ema = 1.0

        self.chi_alpha = 0.00006

        # --- Online Boiler Characterization Curve ---
        # 4 voltage bins: [2-4V), [4-6V), [6-8V), [8-10V]
        # Each stores EMA of measured steam delivery in lbs/hr
        self.BOILER_CURVE_BINS = 4
        self.BOILER_CURVE_V_MIN = 2.0     # Ignore sub-2V (no meaningful steam)
        self.BOILER_CURVE_V_STEP = 2.0    # 2V per bin
        self.BOILER_CURVE_MIN_SAMPLES = 50  # Gate: don't use until 50 data points
        self.boiler_curve_alpha = 0.002   # ~6 min half-life at 5s ticks
        self.boiler_curve_counts = [0] * self.BOILER_CURVE_BINS

        # Restore boiler curve from persistent storage
        try:
            stored_curve = json.loads(
                self.get_state("input_text.hapsic_boiler_curve"))
            if isinstance(stored_curve, list) and len(stored_curve) == self.BOILER_CURVE_BINS:
                self.boiler_curve = [float(v) for v in stored_curve]
                # Mark restored bins as pre-trained
                self.boiler_curve_counts = [
                    999 if v > 0 else 0 for v in self.boiler_curve]
                self.log(f"Boiler curve restored: {self.boiler_curve}")
            else:
                self.boiler_curve = [0.0] * self.BOILER_CURVE_BINS
        except (ValueError, TypeError, json.JSONDecodeError, Exception):
            self.boiler_curve = [0.0] * self.BOILER_CURVE_BINS

        # --- 3. RESTART HANDLING (Safety Park) ---
        self.log("SYSTEM RESTART DETECTED: Executing Safety Park Protocol...", level="WARNING")
        self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
        self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
        self.turn_off(self.SHELLY_LIGHT_ENTITY)
        self.log("SAFETY PARK COMPLETE: Fan -> Auto, Valve -> 0V. Entering STANDBY.")

        self.run_every(self.master_tick, "now", 5)

    # =========================================================================
    # 2.3 EQUATION 4 (MAGNUS-TETENS) & REVERSE
    # =========================================================================

    def calc_psychrometrics(self, temp_f, rh):
        if temp_f is None or rh is None: return None, None

        temp_c = (temp_f - 32) * (5/9)
        es = 0.61121 * math.exp((17.625 * temp_c) / (temp_c + 243.04))
        ea = es * (rh / 100.0)

        if ea <= 0.001:
            dp_f = -40.0
        else:
            alpha = math.log(ea / 0.61121)
            dp_c = (243.04 * alpha) / (17.625 - alpha)
            dp_f = (dp_c * 9/5) + 32

        w = 0.62198 * (ea / (self.P_ATM - ea)) * 7000.0
        return dp_f, w

    def calc_dp_from_w(self, w_grains):
        if w_grains <= 0: return -40.0

        k = w_grains / 4353.86
        ea = (k * self.P_ATM) / (1.0 + k)

        if ea <= 0.001: return -40.0

        alpha = math.log(ea / 0.61121)
        dp_c = (243.04 * alpha) / (17.625 - alpha)
        return (dp_c * 9/5) + 32

    def get_saturation_vapor_pressure(self, temp_c):
        return 0.61121 * math.exp((17.625 * temp_c) / (temp_c + 243.04))

    # =========================================================================
    # 2. HAL & THERMODYNAMICS
    # =========================================================================

    def update_ema(self, key, raw_t, raw_rh):
        try:
            val_t = float(raw_t)
            val_rh = float(raw_rh)
        except (ValueError, TypeError):
            return None, None

        current = self.ema_sensors[key]
        if current["t"] is None:
            current["t"] = val_t
            current["rh"] = val_rh
        else:
            current["t"] = (self.ema_alpha * val_t) + ((1 - self.ema_alpha) * current["t"])
            current["rh"] = (self.ema_alpha * val_rh) + ((1 - self.ema_alpha) * current["rh"])
        return current["t"], current["rh"]

    @staticmethod
    def is_invalid_state(value):
        if value is None:
            return True
        return str(value).strip().lower() in {"unavailable", "unknown", "none", ""}

    @staticmethod
    def parse_sensor_float(value):
        if HapsicController.is_invalid_state(value):
            return None
        try:
            parsed = float(value)
        except (ValueError, TypeError):
            return None
        return None if math.isnan(parsed) else parsed

    def cached_sensor_float(self, entity_id, now_time):
        raw_value = self.get_state(entity_id)
        parsed = self.parse_sensor_float(raw_value)
        cache = self.sensor_value_cache.setdefault(
            entity_id,
            {"value": None, "last_valid": 0.0, "missing_since": 0.0},
        )

        if parsed is not None:
            cache["value"] = parsed
            cache["last_valid"] = now_time
            cache["missing_since"] = 0.0
            return parsed, raw_value, False

        if cache["missing_since"] == 0.0:
            cache["missing_since"] = now_time

        last_valid_age = now_time - cache["last_valid"] if cache["last_valid"] > 0.0 else math.inf
        if cache["value"] is not None and last_valid_age < self.SENSOR_LOSS_GRACE_SECONDS:
            return cache["value"], raw_value, True

        return None, raw_value, False

    def check_shelly_availability(self, now_time):
        control_dependencies = {
            self.SHELLY_LIGHT_ENTITY: self.get_state(self.SHELLY_LIGHT_ENTITY),
        }
        duct_rh_dependencies = {}
        for entity_id in (self.SHELLY_DUCT_RH_ENTITY, self.HAPSIC_DUCT_RH_ENTITY):
            value, raw_state, from_cache = self.cached_sensor_float(entity_id, now_time)
            if value is None:
                cache = self.sensor_value_cache[entity_id]
                loss_age = now_time - cache["missing_since"]
                if loss_age < self.SENSOR_LOSS_GRACE_SECONDS:
                    self.log(
                        f"HAL ALERT: {entity_id} unavailable for {loss_age:.0f}s "
                        f"({raw_state!r}). Waiting for recovery.",
                        level="WARNING",
                    )
                else:
                    duct_rh_dependencies[entity_id] = raw_state
            elif from_cache:
                self.log(
                    f"HAL ALERT: {entity_id} unavailable ({raw_state!r}); "
                    f"using cached value for up to {self.SENSOR_LOSS_GRACE_SECONDS:.0f}s.",
                    level="WARNING",
                )
        control_offline = {
            entity_id: state
            for entity_id, state in control_dependencies.items()
            if self.is_invalid_state(state)
        }
        duct_rh_offline = {
            entity_id: state
            for entity_id, state in duct_rh_dependencies.items()
            if self.is_invalid_state(state)
        }
        offline = {**control_offline, **duct_rh_offline}

        if not offline:
            self.shelly_offline_since = 0
            self.pending_fault_reason = None
            return True

        if duct_rh_offline:
            offline_detail = ", ".join(
                f"{entity_id}={state!r}" for entity_id, state in duct_rh_offline.items()
            )
            self.pending_fault_reason = "Duct RH Sensor Failure"
            self.log(
                f"HAL CRITICAL: Duct RH dependency unavailable ({offline_detail}).",
                level="ERROR",
            )
            return False

        if self.shelly_offline_since == 0:
            self.shelly_offline_since = now_time

        offline_age = now_time - self.shelly_offline_since
        offline_detail = ", ".join(
            f"{entity_id}={state!r}" for entity_id, state in offline.items()
        )
        if offline_age < self.SHELLY_OFFLINE_FAULT_SECONDS:
            self.log(
                f"HAL ALERT: Shelly dependency offline for {offline_age:.0f}s "
                f"({offline_detail}). Waiting for recovery.",
                level="WARNING",
            )
            return True

        self.pending_fault_reason = "Shelly Offline"
        self.log(
            f"HAL CRITICAL: Shelly dependency offline for {offline_age:.0f}s "
            f"({offline_detail}).",
            level="ERROR",
        )
        return False

    def read_and_validate_sensors(self):
        try:
            now_time = time.time()

            def safe_sensor(entity_id):
                return self.cached_sensor_float(entity_id, now_time)

            def safe_parse(t_entity, rh_entity):
                parsed_t, raw_t, cached_t = safe_sensor(t_entity)
                parsed_rh, raw_rh, cached_rh = safe_sensor(rh_entity)
                if parsed_t is None or parsed_rh is None:
                    return None, None, raw_t, raw_rh, cached_t or cached_rh
                return parsed_t, parsed_rh, raw_t, raw_rh, cached_t or cached_rh

            # --- Inside Conditions (Primary: House HA, Fallback: Extract CAN, Cache 30m) ---
            h_t_entity = "sensor.hapsic_room_average_temp"
            h_rh_entity = "sensor.hapsic_room_average_rh"
            e_t_entity = "sensor.hapsic_cleansed_inside_temp"
            e_rh_entity = "sensor.hapsic_cleansed_inside_rh"
            h_t = self.get_state(h_t_entity)
            h_rh = self.get_state(h_rh_entity)
            e_t = self.get_state(e_t_entity)
            e_rh = self.get_state(e_rh_entity)
            self.log(
                f"HAL DEBUG: room_avg_t={h_t!r}, room_avg_rh={h_rh!r}, "
                f"extract_t={e_t!r}, extract_rh={e_rh!r}",
                level="DEBUG",
            )

            effective_room_t, effective_room_rh, _, _, room_cached = safe_parse(h_t_entity, h_rh_entity)
            if effective_room_t is None:
                self.log("HAL DEBUG: Primary room sensors invalid, falling back to extract CAN.", level="DEBUG")
                effective_room_t, effective_room_rh, _, _, room_cached = safe_parse(e_t_entity, e_rh_entity)

            if effective_room_t is not None and not math.isnan(effective_room_t):
                if room_cached:
                    self.log("HAL ALERT: Inside sensor loss debounced with cached value.", level="WARNING")
                self.room_dp, self.room_w = self.calc_psychrometrics(effective_room_t, effective_room_rh)
                self.room_temp_avg = effective_room_t
                self.room_rh_avg = effective_room_rh
                self.last_valid_room_dp_time = now_time
            else:
                cache_age = now_time - self.last_valid_room_dp_time
                if self.last_valid_room_dp_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Inside sensors offline (h_t={h_t!r}, "
                        f"h_rh={h_rh!r}, e_t={e_t!r}, e_rh={e_rh!r}). "
                        f"Using cached DP {self.room_dp:.1f}F",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Inside sensors failed (h_t={h_t!r}, "
                        f"h_rh={h_rh!r}, e_t={e_t!r}, e_rh={e_rh!r}) "
                        f"and cache expired (>30m).",
                        level="ERROR",
                    )
                    return False

            # --- Config Inputs (with safe fallback) ---
            target_entity = "input_number.target_dew_point"
            max_cap_entity = "input_number.humidifier_max_capacity"
            cfg_target, cfg_max_cap, raw_target_dp, raw_max_cap, cfg_cached = safe_parse(
                target_entity,
                max_cap_entity,
            )
            if cfg_target is None:
                self.log(
                    f"HAL ALERT: Config inputs unavailable "
                    f"(target={raw_target_dp!r}, cap={raw_max_cap!r}). "
                    f"Using cached.",
                    level="WARNING",
                )
            else:
                if cfg_cached:
                    self.log("HAL ALERT: Config input loss debounced with cached value.", level="WARNING")
                self.target_room_dp = cfg_target
                self.MAX_CAPACITY = cfg_max_cap

            # --- Outdoor Data (with safe fallback + 30m cache) ---
            out_t_entity = "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature"
            out_rh_entity = "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity"
            effective_out_t, effective_out_rh, raw_out_t, raw_out_rh, out_cached = safe_parse(
                out_t_entity,
                out_rh_entity,
            )

            if effective_out_t is not None and not math.isnan(effective_out_t):
                if out_cached:
                    self.log("HAL ALERT: Outdoor sensor loss debounced with cached value.", level="WARNING")
                self.outdoor_dp, self.outdoor_w = self.calc_psychrometrics(effective_out_t, effective_out_rh)
                self.last_valid_outdoor_time = now_time
            else:
                cache_age = now_time - self.last_valid_outdoor_time
                if self.last_valid_outdoor_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Outdoor sensors offline "
                        f"(t={raw_out_t!r}, rh={raw_out_rh!r}). "
                        f"Using cached DP {self.outdoor_dp:.1f}F",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Outdoor sensors failed "
                        f"(t={raw_out_t!r}, rh={raw_out_rh!r}) "
                        f"and cache expired.",
                        level="ERROR",
                    )
                    return False

            # --- Supply / Pre-Steam (CLEANSED) ---
            s_t_entity = "sensor.hapsic_cleansed_supply_temp"
            s_rh_entity = "sensor.hapsic_cleansed_supply_rh"

            effective_sup_t, effective_sup_rh, s_t, s_rh, sup_cached = safe_parse(s_t_entity, s_rh_entity)

            if effective_sup_t is not None and not math.isnan(effective_sup_t):
                if sup_cached:
                    self.log("HAL ALERT: Supply sensor loss debounced with cached value.", level="WARNING")
                self.supply_t = effective_sup_t
                self.supply_rh = effective_sup_rh
                self.supply_dp, self.supply_w = self.calc_psychrometrics(self.supply_t, self.supply_rh)
                self.last_valid_supply_w_time = now_time
            else:
                cache_age = now_time - self.last_valid_supply_w_time
                if self.last_valid_supply_w_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Supply sensors offline "
                        f"(t={s_t!r}, rh={s_rh!r}). "
                        f"Using cached W {self.supply_w:.1f}",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Supply sensors failed "
                        f"(t={s_t!r}, rh={s_rh!r}) and cache expired.",
                        level="ERROR",
                    )
                    return False

            # --- Post-Steam (Duct) with EMA Filter (with safe fallback + 30m cache) ---
            effective_duct_t, raw_duct_t_str, duct_t_cached = safe_sensor(self.HAPSIC_DUCT_TEMP_ENTITY)
            effective_duct_rh, raw_duct_rh_str, duct_rh_cached = safe_sensor(self.HAPSIC_DUCT_RH_ENTITY)

            if effective_duct_rh is None:
                self.pending_fault_reason = "Duct RH Sensor Failure"
                self.log(
                    f"HAL CRITICAL: Duct RH sensor failed "
                    f"(t={raw_duct_t_str!r}, rh={raw_duct_rh_str!r}).",
                    level="ERROR",
                )
                return False
            if duct_rh_cached:
                self.log("HAL ALERT: Duct RH sensor loss debounced with cached value.", level="WARNING")

            if effective_duct_t is None:
                fallback_candidates = [
                    getattr(self, "duct_t", None),
                    self.duct_ema_t,
                    self.supply_t,
                    self.room_temp_avg,
                    70.0,
                ]
                effective_duct_t = max(
                    value
                    for value in fallback_candidates
                    if value is not None and not math.isnan(value)
                )
                self.duct_temp_fallback_active = True
                self.log(
                    f"HAL ALERT: Duct temp unavailable (t={raw_duct_t_str!r}); "
                    f"using conservative fallback {effective_duct_t:.1f}F with RH {effective_duct_rh:.1f}%.",
                    level="WARNING",
                )
            else:
                self.duct_temp_fallback_active = False
                if duct_t_cached:
                    self.log("HAL ALERT: Duct temp sensor loss debounced with cached value.", level="WARNING")

            if effective_duct_t is not None and not math.isnan(effective_duct_t):
                self.raw_duct_rh = effective_duct_rh

                duct_alpha = 0.2
                if self.duct_temp_fallback_active:
                    self.duct_ema_t = max(self.duct_ema_t or effective_duct_t, effective_duct_t)
                    if self.duct_ema_rh is None:
                        self.duct_ema_rh = effective_duct_rh
                    else:
                        self.duct_ema_rh = (duct_alpha * effective_duct_rh) + ((1 - duct_alpha) * self.duct_ema_rh)
                    self.duct_t = effective_duct_t
                else:
                    if self.duct_ema_t is None:
                        self.duct_ema_t = effective_duct_t
                        self.duct_ema_rh = effective_duct_rh
                    else:
                        self.duct_ema_t = (duct_alpha * effective_duct_t) + ((1 - duct_alpha) * self.duct_ema_t)
                        self.duct_ema_rh = (duct_alpha * effective_duct_rh) + ((1 - duct_alpha) * self.duct_ema_rh)
                    self.duct_t = self.duct_ema_t

                self.duct_rh = self.duct_ema_rh
                self.duct_dp, self.duct_w = self.calc_psychrometrics(self.duct_t, self.duct_rh)
                self.last_valid_duct_time = now_time
            else:
                cache_age = now_time - self.last_valid_duct_time
                if self.last_valid_duct_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Duct sensors offline "
                        f"(t={raw_duct_t_str!r}, rh={raw_duct_rh_str!r}). "
                        f"Using cached DP {self.duct_dp:.1f}F",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Duct sensors failed "
                        f"(t={raw_duct_t_str!r}, rh={raw_duct_rh_str!r}) "
                        f"and cache expired.",
                        level="ERROR",
                    )
                    return False

            # --- Flow (with safe fallback + 30m cache) ---
            supply_flow_entity = "sensor.hapsic_supply_flow"
            exhaust_flow_entity = "sensor.hapsic_extract_flow"
            bypass_entity = "sensor.zehnder_comfoair_q_a4cb9c_bypass_state"
            eff_sf, raw_supply_flow, sf_cached = safe_sensor(supply_flow_entity)
            eff_ef, raw_exhaust_flow, ef_cached = safe_sensor(exhaust_flow_entity)
            eff_bp, raw_bypass, bp_cached = safe_sensor(bypass_entity)

            if eff_sf is not None and eff_ef is not None and eff_bp is not None:
                if sf_cached or ef_cached or bp_cached:
                    self.log("HAL ALERT: Flow sensor loss debounced with cached value.", level="WARNING")
                self.supply_flow = eff_sf
                self.exhaust_flow = eff_ef
                self.bypass_state = eff_bp
                self.last_valid_flow_time = now_time
            else:
                cache_age = now_time - self.last_valid_flow_time
                if self.last_valid_flow_time > 0 and cache_age < 1800:
                    self.log(
                        f"HAL ALERT: Flow sensors offline "
                        f"(sf={raw_supply_flow!r}, ef={raw_exhaust_flow!r}, "
                        f"bp={raw_bypass!r}). Using cached.",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"HAL CRITICAL: Flow sensors failed "
                        f"(sf={raw_supply_flow!r}, ef={raw_exhaust_flow!r}, "
                        f"bp={raw_bypass!r}) and cache expired.",
                        level="ERROR",
                    )
                    return False

            # --- 2.4 Mass Balance & Feasibility Horizon Engine ---
            cfm_nat = 1380.0 / 17.0
            vent_mass_factor = ((self.supply_flow * 0.5886) * 60 * self.RHO) / 7000.0
            infil_mass_factor = (cfm_nat * 60 * self.RHO) / 7000.0
            total_mass_factor = vent_mass_factor + infil_mass_factor

            if total_mass_factor > 0:
                steam_plus_supply = self.get_effective_max_capacity() + (vent_mass_factor * self.supply_w)
                max_room_w = (steam_plus_supply + (infil_mass_factor * self.outdoor_w)) / total_mass_factor
                self.max_achievable_dp = self.calc_dp_from_w(max_room_w)
            else:
                self.max_achievable_dp = self.supply_dp

            # Evaluated strictly for Telemetry & Windup protection
            # Hysteresis: SET at +1.0°F, CLEAR at -0.5°F (deadband prevents chatter)
            if self.target_room_dp > (self.max_achievable_dp + 1.0):
                self.is_target_infeasible = True
            elif self.target_room_dp < (self.max_achievable_dp - 0.5):
                self.is_target_infeasible = False

            return True

        except (ValueError, TypeError) as e:
            tb_lines = traceback.format_exc().splitlines()
            relevant_tb = " | ".join(tb_lines[-4:])
            self.log(
                f"HAL ALERT: Critical Sensor Read Failed. "
                f"Type: {type(e).__name__}, Error: {e} "
                f"| Traceback: {relevant_tb}",
                level="ERROR",
            )
            return False

    # =========================================================================
    # 3. MASTER SCHEDULER
    # =========================================================================

    def master_tick(self, kwargs):
        now = time.time()
        self.dt = now - self.last_tick_ts
        self.last_tick_ts = now

        # Deadman Watchdog Interlock
        if self.dt > 120.0 and self.tick_counter > 0:
            self.trigger_fault(f"Deadman Watchdog: Telemetry Stale > 120s ({self.dt:.1f}s)")
            self.publish_telemetry()
            return

        if self.dt > 10.0: self.dt = 5.0

        self.tick_counter += 1
        if self.turbo_lockout_ticks > 0: self.turbo_lockout_ticks -= 1

        if not self.check_shelly_availability(now):
            self.trigger_fault(self.pending_fault_reason or "Shelly Offline")
            self.publish_telemetry()
            return

        valid_sensors = self.read_and_validate_sensors()
        if not valid_sensors:
            startup_age = now - self.startup_ts
            if startup_age < 90.0:
                self.log(
                    f"HAL: Sensors not ready ({startup_age:.0f}s "
                    f"since startup). Waiting in STANDBY.",
                    level="WARNING",
                )
                self.publish_telemetry()
                return
            self.trigger_fault(self.pending_fault_reason or "Sensor Failure")
            self.publish_telemetry()
            return

        self.room_dp_buffer.append(self.room_dp)
        if len(self.room_dp_buffer) > 180:
            self.room_dp_buffer.pop(0)

        # 60-Second Duct EMA Rolling Buffer for Boil-Detect Derivative
        self.duct_dp_buffer.append(self.duct_dp)
        if len(self.duct_dp_buffer) > 13:
            self.duct_dp_buffer.pop(0)

        if len(self.duct_dp_buffer) >= 13:
            self.duct_derivative = self.duct_dp - self.duct_dp_buffer[0]
        else:
            self.duct_derivative = 0.0

        # Thermodynamic Memory Tracking (15-Minute Cooldown)
        if self.steam_voltage == 0.0:
            self.zero_volt_ticks += 1
        else:
            self.zero_volt_ticks = 0

        if self.zero_volt_ticks >= 180:
            self.boil_achieved = False

        # --- PRIORITY 0 INTERLOCKS ---
        if self.supply_flow < 20.0:
            self.trigger_fault(f"Zero Flow ({self.supply_flow} m3/h)")
            self.publish_telemetry()
            return

        imbalance = self.exhaust_flow - self.supply_flow
        if imbalance > 50.0:
            self.clogged_filter_ticks += 1
            if self.clogged_filter_ticks > 2880:
                 self.trigger_fault("CLOGGED FILTER (Requires Reboot)")
                 self.publish_telemetry()
                 return
            self.trigger_fault(f"Defrost Active (Delta: {imbalance:.1f})")
            self.publish_telemetry()
            return
        else:
            if self.fault_reason != "CLOGGED FILTER (Requires Reboot)":
                self.clogged_filter_ticks = 0

        if self.bypass_state > 5.0:
            self.trigger_fault(f"Economizer Bypass Active ({self.bypass_state}%)")
            self.publish_telemetry()
            return

        self.evaluate_fsm()

        if self.tick_counter % 12 == 0:
            self.execute_loop_a()

        if self.fsm_state in ["ACTIVE_CRUISE", "ACTIVE_TURBO", "TURBO_PENDING"]:
            self.execute_loop_b()
        else:
            self.steam_voltage = 0.0
            self.stasis_active = False
            self.stasis_timer = 0
            self.upward_rate_ticks = 0
            self.downward_rate_ticks = 0
            self.boil_achieved = False

        # Unfiltered Hardware E-Stop (Overrides ALL loops and states)
        if hasattr(self, 'raw_duct_rh') and self.raw_duct_rh >= 88.0:
            if self.steam_voltage > 0.0:
                self.log(
                    f"SAFETY ABORT: Raw Duct RH ({self.raw_duct_rh:.1f}%) critically high! Forcing 0.0V.",
                    level="WARNING",
                )
            self.steam_voltage = 0.0
            self.stasis_active = False

        self.write_output()
        self.run_diagnostics()
        self.update_economy_advisory()
        self.publish_telemetry()

        # --- HEARTBEAT ---
        if self.tick_counter % 2 == 0:
            self.log(
                f"[HEARTBEAT] FSM: {self.fsm_state} | "
                f"Room DP: {self.room_dp:.1f}F | "
                f"Target DP: {self.target_room_dp:.1f}F | "
                f"Duct DP: {self.duct_dp:.1f}F | "
                f"d(DuctDP)/dt: {self.duct_derivative:.2f}F/m | "
                f"Duct RH: {self.duct_rh:.1f}% | "
                f"Out: {self.steam_voltage:.1f}V | "
                f"EcoAdv: {self.economy_advisory_reason} "
                f"neg={self.economy_negative_streak_sec / 60.0:.0f}m "
                f"severe={self.economy_severe_streak_sec / 60.0:.0f}m "
                f"delta={self.economy_advisory_target_delta:.1f}F",
                level="INFO"
            )

    # =========================================================================
    # 5. FINITE STATE MACHINE
    # =========================================================================

    def evaluate_fsm(self):
        if self.fsm_state in ["ACTIVE_CRUISE", "ACTIVE_TURBO"]:
            self.active_cruise_ticks += 1
        else:
            self.active_cruise_ticks = 0

        if self.fsm_state == "FAULT":
            self.fault_clear_ticks += 1
            if self.fault_clear_ticks >= 12:
                self.log("FSM: Plant stable (60s). Auto-clearing FAULT -> STANDBY.", level="INFO")
                self.fsm_state = "STANDBY"
                self.fault_reason = "NONE"
                self.boil_achieved = False
            return

        if self.fsm_state == "STANDBY":
            if self.room_dp < (self.target_room_dp - 1.0):
                deficit = self.target_room_dp - self.room_dp
                self.log(f"FSM TRANSITION: STANDBY -> ACTIVE_CRUISE. Deficit {deficit:.2f}F", level="INFO")
                self.fsm_state = "ACTIVE_CRUISE"
                self.integrator_a = 0.0
                self.integrator_b = 0.0
                self.call_service("switch/turn_off", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
                self.call_service(
                    "select/select_option",
                    entity_id="select.zehnder_comfoair_q_a4cb9c_fan_speed",
                    option="Medium",
                )
                self.tick_counter = 1
                self.execute_loop_a()

        elif self.fsm_state == "ACTIVE_CRUISE":
            if self.room_dp >= (self.target_room_dp + 1.0):
                self.log("FSM TRANSITION: ACTIVE_CRUISE -> HYGIENIC_PURGE.", level="INFO")
                self.fsm_state = "HYGIENIC_PURGE"
                self.purge_ticks = 0
                self.boil_achieved = False
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_15_min")
                return

            room_deficit = self.target_room_dp - self.room_dp

            # Target Met: Room DP at or above setpoint → coast to STANDBY
            if room_deficit <= 0.0:
                self.log(
                    f"FSM TRANSITION: ACTIVE_CRUISE -> STANDBY (Target Met: "
                    f"room={self.room_dp:.1f}F >= target={self.target_room_dp:.1f}F).",
                    level="INFO",
                )
                self.fsm_state = "STANDBY"
                self.boil_achieved = False
                self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
                return

            # Satisfaction Coasting: Nearly satisfied AND voltage already wound down
            if self.steam_voltage == 0.0 and room_deficit < 0.5:
                self.log("FSM TRANSITION: ACTIVE_CRUISE -> STANDBY (Satisfaction Coasting).", level="INFO")
                self.fsm_state = "STANDBY"
                self.boil_achieved = False
                self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
                return

            if (self.steam_voltage > 9.5 and
                self.duct_rh > 82.0 and
                room_deficit > 3.0 and
                self.turbo_lockout_ticks == 0):

                self.log("FSM TRANSITION: ACTIVE_CRUISE -> TURBO_PENDING.", level="INFO")
                self.fsm_state = "TURBO_PENDING"
                self.turbo_wait_ticks = 0
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_60_min")

        elif self.fsm_state == "TURBO_PENDING":
            self.turbo_wait_ticks += 1
            if self.supply_flow > 200.0:
                self.log(f"FSM TRANSITION: TURBO_PENDING -> ACTIVE_TURBO. Flow {self.supply_flow}", level="INFO")
                self.fsm_state = "ACTIVE_TURBO"
            elif self.turbo_wait_ticks > 12:
                self.log("FSM WARNING: Turbo Boost Failed. Lockout 30m.", level="WARNING")
                self.fsm_state = "ACTIVE_CRUISE"
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
                self.turbo_lockout_ticks = 360

        elif self.fsm_state == "ACTIVE_TURBO":
            if (self.target_room_dp - self.room_dp) < 1.0:
                self.log("FSM TRANSITION: ACTIVE_TURBO -> ACTIVE_CRUISE.", level="INFO")
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
                self.fsm_state = "ACTIVE_CRUISE"

        elif self.fsm_state == "HYGIENIC_PURGE":
            self.purge_ticks += 1
            if self.outdoor_dp > self.room_dp:
                self.log(f"FSM SAFETY: Outdoor air wet ({self.outdoor_dp:.1f}F). Aborting Purge.", level="WARNING")
                self.fsm_state = "STANDBY"
                self.boil_achieved = False
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
                self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")
                return

            room_deficit = self.target_room_dp - self.room_dp
            if self.purge_ticks >= 120 or room_deficit > 2.0:
                self.log("FSM TRANSITION: HYGIENIC_PURGE -> STANDBY.", level="INFO")
                self.fsm_state = "STANDBY"
                self.boil_achieved = False
                self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
                self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")

    def trigger_fault(self, reason):
        if self.fsm_state != "FAULT":
            self.log(f"FSM: FAULT TRIGGERED - {reason}", level="ERROR")
            self.fsm_state = "FAULT"
            self.fault_reason = reason
            self.call_service("button/press", entity_id="button.zehnder_comfoair_q_a4cb9c_boost_off")
            self.call_service("switch/turn_on", entity_id="switch.zehnder_comfoair_q_a4cb9c_auto_ventilation")

        self.steam_voltage = 0.0
        self.integrator_a = 0.0
        self.integrator_b = 0.0
        self.fault_clear_ticks = 0
        self.stasis_active = False
        self.stasis_timer = 0
        self.boil_achieved = False
        self.upward_rate_ticks = 0
        self.downward_rate_ticks = 0
        self.write_output()

    # =========================================================================
    # 6. PID LOOPS
    # =========================================================================

    def execute_loop_a(self):
        if self.fsm_state not in ["ACTIVE_CRUISE", "ACTIVE_TURBO", "TURBO_PENDING"]: return

        dt_factor = (self.dt * 12.0) / 60.0

        # Paradox Fix: Loop A evaluates raw User Target directly
        active_target_dp = self.target_room_dp

        error = active_target_dp - self.room_dp
        Kp, Ki = 2.0, 0.1

        # Calculate ideal output prior to limits
        ideal_output = active_target_dp + (Kp * error) + (Ki * (self.integrator_a + (error * dt_factor)))

        min_clamp = max(30.0, self.supply_dp)
        max_clamp = self.MAX_DUCT_DP

        # Strict Directional Freezing (Feasibility & Hardware Clamps)
        freeze_integrator = False

        if self.is_target_infeasible and error > 0:
            freeze_integrator = True
        elif ideal_output < min_clamp and error < 0:
            freeze_integrator = True
        elif ideal_output > max_clamp and error > 0:
            freeze_integrator = True

        if not freeze_integrator:
            self.integrator_a += (error * dt_factor)

        output_dp = active_target_dp + (Kp * error) + (Ki * self.integrator_a)

        self.target_duct_dp = max(min_clamp, min(max_clamp, output_dp))

    def execute_loop_b(self):
        # 1. Calculate Psychrometrics & V_FF
        target_c = (self.target_duct_dp - 32) * 5/9
        target_vp = self.get_saturation_vapor_pressure(target_c)
        target_w = 0.62198 * (target_vp / (self.P_ATM - target_vp)) * 7000.0

        w_req_grains = max(0, target_w - self.supply_w)
        cfm = self.supply_flow * 0.5886
        lbs_hr_req = (w_req_grains * cfm * 60 * self.RHO) / 7000.0

        # Use learned boiler curve when trained, falls back to linear model
        v_ff = self.voltage_for_steam_rate(lbs_hr_req)

        # 2. Continuous Ideal PID
        error = self.target_duct_dp - self.duct_dp
        if abs(error) < 1.5:
            error = 0.0

        Kp_b, Ki_b = 0.1, 0.02
        dt_factor = self.dt / 5.0

        # Compute Ideal Voltage using CURRENT Integrator State
        v_trim = (Kp_b * error) + (Ki_b * self.integrator_b)
        ideal_voltage = v_ff + v_trim
        quantized_target = round(ideal_voltage * 2.0) / 2.0

        next_voltage = self.steam_voltage
        shattered_this_tick = False

        # =========================================================
        # 4. STATE-BASED GLIDE-PATH BATCH SEQUENCER
        # =========================================================

        # Phase 1: Cold Start Ignition Overdrive (Now strikes at 9.5V)
        if not self.boil_achieved and self.steam_voltage == 0.0 and quantized_target >= 3.5:
            next_voltage = 9.5
            self.stasis_active = True
            self.stasis_timer = 180
            self.upward_rate_ticks = 0
            self.downward_rate_ticks = 0

        # Phase 2: Thermodynamic State Release (Dynamic Stasis Shatter)
        elif self.stasis_active:
            self.stasis_timer -= 1
            self.integrator_b = 0.0  # Rigidly force to 0.0 to prevent windup
            next_voltage = 9.5

            # Shatter Condition
            if self.duct_derivative >= 1.0 or self.stasis_timer <= 0:
                self.stasis_active = False
                self.boil_achieved = True
                shattered_this_tick = True
                # BUMPLESS HANDOFF PRIME: Seed the PID instantly using the new 9.5V reality
                self.integrator_b = max(0.0, (9.5 - v_ff - (Kp_b * error)) / Ki_b)

        # Phase 3: Glide-Path Asymmetric Limiting (Modulation Phase)
        anti_short_cycle_active = False

        if not self.stasis_active and not shattered_this_tick:
            if self.steam_voltage == 0.0 and quantized_target >= 3.5:
                # [NEW] 5-Minute Anti-Short Cycle Lockout (60 ticks)
                if self.zero_volt_ticks >= 60:
                    next_voltage = 3.5
                    self.upward_rate_ticks = 0
                    self.downward_rate_ticks = 0
                else:
                    anti_short_cycle_active = True
            elif self.steam_voltage != quantized_target:
                # Asymmetric Slew Limits
                if quantized_target > self.steam_voltage:
                    self.downward_rate_ticks = 0
                    self.upward_rate_ticks += 1
                    if self.upward_rate_ticks >= 12:
                        next_voltage = self.steam_voltage + 0.5
                        self.upward_rate_ticks = 0
                elif quantized_target < self.steam_voltage:
                    self.upward_rate_ticks = 0
                    self.downward_rate_ticks += 1
                    if self.downward_rate_ticks >= 6:
                        next_voltage = self.steam_voltage - 0.5
                        self.downward_rate_ticks = 0

        # =========================================================
        # 5. HARDWARE PROTECTION & UNIVERSAL DIRECTIONAL FREEZING
        # =========================================================

        # Evaluate Safety Limits (Overwrites Phase 3)
        ceiling_volts = max(0.0, 9.5 - ((self.duct_rh - 82.0) * 1.6))

        if next_voltage > ceiling_volts:
            next_voltage = ceiling_volts
            self.stasis_active = False
            self.stasis_timer = 0

        if next_voltage > 0.0 and next_voltage < 3.5:
            next_voltage = 0.0
            self.stasis_active = False
            self.stasis_timer = 0

        next_voltage = max(0.0, min(9.5, next_voltage))

        # Universal Directional Freezing Rule (Matrix)
        if not self.stasis_active and not shattered_this_tick:
            if anti_short_cycle_active:
                pass # [NEW] FREEZE to prevent infinite windup during ASCT lockout
            elif (next_voltage < ideal_voltage) and (error > 0):
                if next_voltage == 0.0:
                    # Exception: Allow windup to escape the 3.5V min-fire deadzone
                    self.integrator_b += (error * dt_factor)
                else:
                    pass # FREEZE: Prevents positive windup against Ceilings, HW Max, or Up-Slew
            elif (next_voltage > ideal_voltage) and (error < 0):
                pass # FREEZE: Prevents negative windup against HW Min or Down-Slew
            else:
                self.integrator_b += (error * dt_factor)

        self.steam_voltage = next_voltage

    def write_output(self):
        physical_volts = self.steam_voltage
        if physical_volts < 2.0:
            physical_volts = 0.0

        brightness = int((physical_volts / 10.0) * 255)
        brightness = max(0, min(255, brightness))

        # Closed-loop override logic
        ha_state = self.get_state(self.SHELLY_LIGHT_ENTITY)

        if brightness == 0:
            if ha_state != "off":
                self.log(f"SAFETY: Shelly is {ha_state} but requested 0%. Forcing OFF.", level="WARNING")
                self.turn_off(self.SHELLY_LIGHT_ENTITY)
                self.last_brightness = 0
        else:
            if brightness != self.last_brightness or ha_state == "off":
                self.turn_on(self.SHELLY_LIGHT_ENTITY, brightness=brightness)
                self.last_brightness = brightness

    # =========================================================================
    # 8 & 9. DIAGNOSTICS & TELEMETRY
    # =========================================================================

    # =========================================================================
    # BOILER CHARACTERIZATION — Online voltage→delivery curve learning
    # =========================================================================

    def _boiler_curve_bin_idx(self, voltage):
        """Map a voltage to a boiler curve bin index, or -1 if out of range."""
        if voltage < self.BOILER_CURVE_V_MIN:
            return -1
        idx = int((voltage - self.BOILER_CURVE_V_MIN) / self.BOILER_CURVE_V_STEP)
        return min(idx, self.BOILER_CURVE_BINS - 1)

    def voltage_for_steam_rate(self, target_lbs_hr):
        """Invert the learned boiler curve to find the voltage that produces target_lbs_hr.

        Falls back to the linear MAX_CAPACITY model if the curve is untrained.
        """
        # Check if any bin is trained
        any_trained = any(
            c >= self.BOILER_CURVE_MIN_SAMPLES and v > 0
            for c, v in zip(self.boiler_curve_counts, self.boiler_curve))

        if not any_trained:
            # Fallback: linear nameplate model
            return min(9.5, (target_lbs_hr / self.MAX_CAPACITY) * 10.0)

        # Walk bins low→high to find where target_lbs_hr falls
        for i in range(self.BOILER_CURVE_BINS):
            if self.boiler_curve_counts[i] < self.BOILER_CURVE_MIN_SAMPLES:
                continue
            if self.boiler_curve[i] <= 0:
                continue

            bin_mid_v = self.BOILER_CURVE_V_MIN + (i + 0.5) * self.BOILER_CURVE_V_STEP

            if self.boiler_curve[i] >= target_lbs_hr:
                # Target falls at or below this bin's measured rate
                # Find previous trained bin for interpolation
                prev_rate = 0.0
                prev_v = 0.0
                for j in range(i - 1, -1, -1):
                    if (self.boiler_curve_counts[j] >= self.BOILER_CURVE_MIN_SAMPLES
                            and self.boiler_curve[j] > 0):
                        prev_rate = self.boiler_curve[j]
                        prev_v = self.BOILER_CURVE_V_MIN + (j + 0.5) * self.BOILER_CURVE_V_STEP
                        break

                if self.boiler_curve[i] == prev_rate:
                    return min(9.5, bin_mid_v)

                frac = ((target_lbs_hr - prev_rate)
                        / (self.boiler_curve[i] - prev_rate))
                return min(9.5, prev_v + frac * (bin_mid_v - prev_v))

        # Target exceeds all learned bins — clamp to max
        return 9.5

    def get_effective_max_capacity(self):
        """Return the best estimate of max steam delivery (lbs/hr).

        Uses the highest trained boiler curve bin if available,
        falls back to CHI-corrected nameplate.
        """
        trained_vals = [
            v for v, c in zip(self.boiler_curve, self.boiler_curve_counts)
            if c >= self.BOILER_CURVE_MIN_SAMPLES and v > 0]
        if trained_vals:
            return max(trained_vals)
        # Fallback: CHI-corrected nameplate
        return self.MAX_CAPACITY * self.chi_ema

    def run_diagnostics(self):
        self.calc_steam_mass = (self.steam_voltage / 10.0) * self.MAX_CAPACITY

        cfm_nat = 1380.0 / 17.0

        vent_mass_factor = ((self.supply_flow * 0.5886) * 60 * self.RHO) / 7000.0
        infil_mass_factor = (cfm_nat * 60 * self.RHO) / 7000.0

        self.calc_loss_vent = vent_mass_factor * max(0, self.room_w - self.supply_w)
        loss_infil = infil_mass_factor * max(0, self.room_w - self.outdoor_w)
        self.calc_flux = self.calc_steam_mass - (self.calc_loss_vent + loss_infil)

        self._last_measured_steam = 0.0
        self.boil_status = "COLD"
        if self.boil_achieved and self.steam_voltage > 1.0:
            self.boil_status = "BOILING"

            dry_air_mass_lbs_hr = (self.supply_flow * 0.5886) * 60 * self.RHO
            theo_grains = self.calc_steam_mass * (7000 / 60)
            actual_grains = (self.duct_w - self.supply_w) * dry_air_mass_lbs_hr / 60.0

            # CHI Gating strictly evaluated off physical boil state
            if theo_grains > 100 and self.boil_achieved and not self.stasis_active:
                self.chi_instant = actual_grains / theo_grains
                self.chi_instant = max(0.0, min(2.0, self.chi_instant))
                self.chi_ema = (self.chi_alpha * self.chi_instant) + ((1 - self.chi_alpha) * self.chi_ema)

                # --- Boiler Curve Learning ---
                # Convert actual grains/min delivery to lbs/hr
                actual_lbs_hr = actual_grains * 60.0 / 7000.0
                self._last_measured_steam = actual_lbs_hr

                bin_idx = self._boiler_curve_bin_idx(self.steam_voltage)
                if bin_idx >= 0 and actual_lbs_hr > 0:
                    a = self.boiler_curve_alpha
                    self.boiler_curve[bin_idx] = (
                        a * actual_lbs_hr
                        + (1 - a) * self.boiler_curve[bin_idx])
                    self.boiler_curve_counts[bin_idx] += 1

                # Persist CHI + boiler curve every 5 minutes (60 ticks)
                if self.tick_counter % 60 == 0:
                    self.call_service("input_number/set_value",
                                      entity_id="input_number.hapsic_chi_ema",
                                      value=round(self.chi_ema, 3))
                    self.call_service("input_text/set_value",
                                      entity_id="input_text.hapsic_boiler_curve",
                                      value=json.dumps(
                                          [round(v, 4) for v in self.boiler_curve]))
            else:
                self.chi_instant = 0.0

    def update_economy_advisory(self):
        """Observe periods where active steaming is not producing useful net flux.

        This is intentionally advisory only. It publishes enough state for HA
        history to answer, after a week, whether target reduction should be
        promoted into the control loop.
        """
        cfm = max(0.0, self.supply_flow * 0.5886)
        dry_air_lbs_hr = cfm * 60.0 * self.RHO
        outdoor_delta_grains = self.outdoor_w - self.room_w
        self.economy_passive_import_lbs_hr = (
            dry_air_lbs_hr * max(0.0, outdoor_delta_grains) / 7000.0
        )
        self.economy_passive_export_lbs_hr = (
            dry_air_lbs_hr * max(0.0, -outdoor_delta_grains) / 7000.0
        )

        active_steam = (
            self.fsm_state in ["ACTIVE_CRUISE", "ACTIVE_TURBO", "TURBO_PENDING"]
            or self.steam_voltage > 0.1
            or self.calc_steam_mass > 0.05
        )
        room_deficit = self.target_room_dp - self.room_dp
        useful_demand = room_deficit > 0.2
        self.economy_steaming_active = active_steam
        self.economy_useful_demand = useful_demand
        self.economy_passive_import_candidate = (
            useful_demand
            and self.economy_passive_import_lbs_hr >= 0.05
            and self.outdoor_dp <= (self.target_room_dp + 0.5)
        )
        negative_net = self.calc_flux <= 0.0
        severe_negative_net = self.calc_flux <= -0.5
        recovering = self.calc_flux > 0.25

        if active_steam and useful_demand and negative_net:
            self.economy_negative_streak_sec += self.dt
        else:
            self.economy_negative_streak_sec = 0.0

        if active_steam and useful_demand and severe_negative_net:
            self.economy_severe_streak_sec += self.dt
        else:
            self.economy_severe_streak_sec = 0.0

        if active_steam and recovering:
            self.economy_recovery_streak_sec += self.dt
        else:
            self.economy_recovery_streak_sec = 0.0

        self.economy_advisory_active = self.economy_negative_streak_sec >= 900.0
        self.economy_advisory_severe = self.economy_severe_streak_sec >= 900.0

        if self.economy_advisory_severe:
            self.economy_advisory_reason = "SEVERE_NEGATIVE_NET_FLUX"
            self.economy_advisory_target_delta = -1.0
        elif self.economy_advisory_active:
            self.economy_advisory_reason = "NEGATIVE_NET_FLUX"
            self.economy_advisory_target_delta = -0.5
        elif self.economy_negative_streak_sec > 0.0:
            self.economy_advisory_reason = "EVALUATING_NEGATIVE_NET_FLUX"
            self.economy_advisory_target_delta = 0.0
        elif self.economy_recovery_streak_sec >= 900.0:
            self.economy_advisory_reason = "RECOVERED"
            self.economy_advisory_target_delta = 0.0
        else:
            self.economy_advisory_reason = "CLEAR"
            self.economy_advisory_target_delta = 0.0

    def publish_telemetry(self):
        struct_vel = 0.0
        if len(self.room_dp_buffer) > 0:
            struct_vel = (self.room_dp - self.room_dp_buffer[0]) * 4

        payload = {
            "fsm": {
                "state": self.fsm_state,
                "fault": self.fault_reason,
            },
            "process": {
                "user_target": round(self.target_room_dp, 2),
                "effective_target": round(
                    self.max_achievable_dp if self.is_target_infeasible else self.target_room_dp, 2
                ),
                "is_infeasible": self.is_target_infeasible,
                "max_achievable": round(self.max_achievable_dp, 2),
                "max_achievable_dp": round(self.max_achievable_dp, 2),
                "room_deficit": round(self.target_room_dp - self.room_dp, 2), # Now strictly User Target
                "duct_target": round(self.target_duct_dp, 2),
                "structure_velocity": round(struct_vel, 2),
                "stasis_active": self.stasis_active,
                "duct_derivative": round(self.duct_derivative, 2),
                "is_boiling": self.boil_achieved
            },
            "psychrometrics": {
                "room_dp": round(self.room_dp, 2) if hasattr(self, 'room_dp') else 0.0,
                "room_avg_rh": round(self.room_rh_avg, 2) if hasattr(self, 'room_rh_avg') else 0.0,
                "room_avg_temp": round(self.room_temp_avg, 1) if hasattr(self, 'room_temp_avg') else 0.0,
                "pre_steam_dp": round(self.supply_dp, 2) if hasattr(self, 'supply_dp') else 0.0,
                "post_steam_dp": round(self.duct_dp, 2) if hasattr(self, 'duct_dp') else 0.0,
                "outdoor_dp": round(self.outdoor_dp, 2),
                "bypass_state": self.bypass_state,
                "duct_temp_fallback_active": self.duct_temp_fallback_active,
            },
            "io": {
                "steam_volts": round(self.steam_voltage, 2),
                "steam_mass": round(self.calc_steam_mass, 2) if hasattr(self, 'calc_steam_mass') else 0.0
            },
            "physics": {
                "flux_net": round(self.calc_flux, 2) if hasattr(self, 'calc_flux') else 0.0,
                "loss_vent": round(self.calc_loss_vent, 2) if hasattr(self, 'calc_loss_vent') else 0.0
            },
            "advisory": {
                "economy_active": self.economy_advisory_active,
                "economy_severe": self.economy_advisory_severe,
                "steaming_active": self.economy_steaming_active,
                "useful_demand": self.economy_useful_demand,
                "passive_import_candidate": self.economy_passive_import_candidate,
                "passive_import_lbs_hr": round(self.economy_passive_import_lbs_hr, 3),
                "passive_export_lbs_hr": round(self.economy_passive_export_lbs_hr, 3),
                "economy_reason": self.economy_advisory_reason,
                "negative_net_minutes": round(self.economy_negative_streak_sec / 60.0, 1),
                "severe_negative_minutes": round(self.economy_severe_streak_sec / 60.0, 1),
                "recovery_minutes": round(self.economy_recovery_streak_sec / 60.0, 1),
                "suggested_target_delta": round(self.economy_advisory_target_delta, 1),
            },
            "health": {
                "boil_status": getattr(self, 'boil_status', "COLD"),
                "chi_ratio": round(self.chi_instant, 3) if hasattr(self, 'chi_instant') else 0.0,
                "chi_ema": round(self.chi_ema, 3),
                "boiler_curve": [round(v, 3) for v in self.boiler_curve],
                "boiler_curve_samples": list(self.boiler_curve_counts),
                "effective_max_capacity": round(self.get_effective_max_capacity(), 3),
                "measured_steam_lbs_hr": round(getattr(self, '_last_measured_steam', 0.0), 3),
                "production_efficiency": round(
                    (self._last_measured_steam / self.calc_steam_mass * 100.0)
                    if self.calc_steam_mass > 0.1 and getattr(self, '_last_measured_steam', 0.0) > 0.0
                    else 0.0, 1
                )
            }
        }

        self.call_service("mqtt/publish", topic="hapsic/telemetry/state", payload=json.dumps(payload))
