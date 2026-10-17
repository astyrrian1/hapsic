# Delivery diagnostics, version 1

This is an additive observer. `HapsicController`, its actuator outputs, CHI,
learned curves, safety paths and legacy telemetry retain their release meanings.
`HapsicDiagnostics` is a separately pinned AppDaemon application in the same
canonical Python module. Only detached state snapshots cross its single storage
worker. Diagnostic work and commands have bounded queues; failures invalidate
measurement evidence and cannot invoke controller services.

## Measurements and eligibility

Estimated Steam Delivery is signed kg/h added between two measurement locations.
It uses upstream actual volume divided by dry-air specific volume, multiplied by
the downstream-minus-upstream humidity ratio after an explicitly accepted offset.
A pressure fallback is provisional and cannot learn. Temperatures support C/F,
flow supports m3/h/cfm, and absolute pressure supports Pa/kPa/hPa/mbar/inHg.
No sensor accuracy or flow standardization is inferred from displayed units.

Every input snapshot carries `source`, `value`, `unit`, `reported_at`, `report_id`,
`verified`, and optional absolute `accuracy` in the source unit. The six input keys
are `upstream_t`, `upstream_rh`, `downstream_t`, `downstream_rh`, `flow`, `pressure`.
`command` additionally reports V and fault/uncertain state. Source bindings must
record physical locations, transformations, reporting contract and accuracy in
AppDaemon configuration before enabling the corresponding flags. The raw Shelly
analog RH transformation must be verified physically, not assumed from `%`.

Defaults: air-stream age 120 s, pressure age 900 s, stream skew 30 s, at least three
independent reports from each stream source in five minutes. Polling cached HA
state never renews a report. `last_reported` must actually be available in the
AppDaemon cache, or a binding must name a verified source timestamp attribute via
`timestamp_field`. Missing provenance cannot contribute calibration or performance.
A stable value is not evidence of a new measurement.

Only completed nonoverlapping observation intervals contribute. Qualification
requires five continuous minutes: command range <=0.2 V, flow range <=5% of mean,
least-squares upstream temperature slope <=0.2 C/min and moisture-difference slope
<=0.1 g/kg/min. The initial qualification window is never credited retroactively.
Every interruption resets stability. Missing history, restart or at least 60 s off
causes a full 15-minute warmup exclusion; output success cannot shorten it.
Independent cycles require 15 continuous observed minutes off.

## Calibration and references

Use `script.hapsic_diagnostic_action` to start/cancel/reset a candidate. Review
`sensor.hapsic_diagnostic_calibration` attributes before accepting its exact
`candidate.revision`. `reference_start` additionally requires explicit
`healthy: true`, meaning owner-confirmed healthy operation. Neither acceptance
nor reset changes control CHI or learned curves. An accepted ACK means durable
storage, not merely command receipt. Stale/replayed acceptance cannot change state.

A baseline collects one ten-minute stable window after 15 minutes observed off,
flow >=50 m3/h, in each of three distinct off periods. Its offset is the median
window mean; a fourth held-out period must satisfy its residual envelope plus
known measurement bounds. The training temperature/RH/flow envelope limits
subsequent eligible operation. Outside-envelope estimates remain provisional;
coverage exclusions do not indicate a change in canister condition.

A frozen reference needs 30 qualified minutes and three contributing cycles per
0.5 V nominal bin. Pooled commanded-voltage range is <=0.2 V and pooled flow
range <=10% of its mean. Coordinates are actual mean command/flow. Adjacent bins
can interpolate only if both match current flow within 10%; never extrapolate.
Baseline, sensor identity/unit/accuracy, source configuration or algorithm changes
invalidate dependent evidence. Four previous generations are archived. No legacy
state is imported and old evidence is never reinterpreted under new calibration.

Unknown accuracy allows an explicitly provisional empirical reference; it never
produces an actionable warning. Known bounds conservatively enclose sensor error,
baseline residuals and reference variability; systematic error never shrinks with
sample count. Keep signed output and percentages above 100. An entirely negative
measurement interval is a mismatch; a near-zero interval containing zero remains
eligible. Reference lower mass must be positive to divide known uncertainty bounds.

## Scores and alerts

Performance is `100 * integrated observed mass / integrated reference mass`, never
an average of percentages. Current Delivery Performance requires a complete five
eligible minutes and immediately becomes invalid on qualification loss. Separately
timestamped last-observed results retain their original status, including provisional.

Canister Performance selects the latest two eligible high-demand hours (>=9 V)
within seven days, fractionally trimming the oldest interval. It needs three
contributing cycles; 30 minutes is an early provisional result, two hours is full.
Evidence older than 24 h is stale. Availability, measurement validity, calibration
and readiness are independent. Canister performance describes sustained system
delivery capability, not scale, remaining life, or proof of canister causation.

A full non-stale result warns only when its known upper bound is below 60%; the
lower bound must exceed 65% to clear. Other results are inconclusive. Alert episode
identity and frozen warning evidence survive restart. The site HA blueprint uses
durable `house_communications` delivery IDs/receipts, retries pre-dispatch failures,
and marks its helper only after transport dispatch. This confirms dispatch, not
recipient receipt. The distributed blueprint uses the selected native notify service.

## Wire and storage contracts

`hapsic/telemetry/diagnostics` (desk: `hapsic-desk/telemetry/diagnostics`) carries
schema version 1 every five seconds, QoS 0, **not retained**. The producer is
`hapsic-python`; desk firmware has its own producer/topic. Allow only the configured
producer to write each topic using broker ACLs. HA rejects unknown schema/producer,
uses new entity IDs, expires after 30 s, and preserves numeric nulls. Legacy topics
and helpers remain available for compatibility and are labeled legacy/model estimates.

Required groups: `schema_version`, `producer_id`, `boot_id`, `sequence`,
`published_at`, `observed_at`, `generation`, `clock_valid`, `measurement`,
`qualification`, `calibration`, `delivery`, `canister`, `last_valid`, `alert`,
`exclusions`. Invalid numeric outputs are JSON null; no NaN/Infinity. Ratios carry
`value/lower/upper/status/seconds/cycles`; canister also carries `ready` and, when
present, `last_observed_at`. Alert carries decision, episode, active, warning,
recovery and frozen `evidence`. Consumers must test status, not numeric truthiness.

Raw `.../inputs` schema 1 carries producer/boot/connection/sequence/publication time plus
`samples` using the atomic input contract above. The C++ adapter verifies relay
identity, ordering and 30 s relay age, preserves original source report timestamps
and IDs, breaks continuity on producer/broker reconnect or missing relay packets, converts each value with its accompanying unit and independently computes
all diagnostic mathematics. Desk uses production's reported command; the production
variant reads its own controller's const getters. Raw relay is read-only and is
not an actuator command channel. Duplicate reconnect reports never add evidence.

Commands at `.../command` require schema 1, matching producer, unique request ID,
UTC `issued_at` within 30 s, action, and exact revision for acceptance. Supported
actions: baseline_start/accept/reset, reference_start/accept/reset, cancel. Subscribe
to `.../ack` for accepted/rejected responses; commands are not retained. One
transaction persists evidence, reference/generation, cursors, cycle and alert state.

Configuration changes use separate files suffixed with their configuration ID
and separate firmware NVS namespaces; previous configurations remain archived
without loading their evidence. Firmware binary state is local to its build ABI.
Python uses strict version-1 JSON and atomic file replacement with fsync, maximum
8 MiB, one deferred write at a time. Firmware uses a dedicated `hapsic_diag` NVS
partition, two alternating chunk sets plus committed size/CRC/transaction manifest;
no diagnostic commit flushes control preferences. Its snapshot limit is 192 KiB.
Allocation headroom covers clone and serialization peaks. Storage/capacity errors
are explicit; rejected writes never acknowledge acceptance. Checkpoints are five
minutes apart; abrupt power loss can discard recent uncommitted evidence but does
not credit downtime. Restart restores history, discards in-progress qualification
and imposes warmup. On a UTC mapping jump >5 s, affected evidence is quarantined as
unscored per-cycle mass/time summaries, preserving bounded memory, and time scoring
waits for 30 s stable synchronization. Records retain monotonic duration separately
from UTC span; small synchronized clock jitter is allowed, and partial duration
or seven-day trimming scales both spans proportionally. A >5 s step or mapping
shift suspends age-dependent scoring. Calibration records remain timeless.

The opt-in `stamplc*_diagnostics.yaml` variants change the partition table. Their
first installation requires an explicitly reviewed USB flash. Do not OTA them over
an existing partition map. Base firmware does not enable the observer. Firmware
flashing is a separate rollout, not part of deploying Python/HA diagnostics.

For a later firmware-owned production diagnostic topic, first change HA's expected
producer to `hapsic-firmware`. Move the Python observer's `diagnostics_topic` to a
shadow namespace and set firmware `input_topic` to that namespace's `/inputs`
subtopic. This keeps its raw relay running while only firmware publishes production
results. Commission a new independent firmware baseline/reference; do not copy the
Python store into NVS. Configure broker ACLs for the chosen producer before cutover.


## Commissioning, rollout and rollback

1. Deploy compatible HA consumers first; missing diagnostics must not suppress
   existing daily, infeasible or fault-cleared notifications. Keep house routing.
2. Publish a reviewed HACS GitHub Release, install it and restart AppDaemon.
   Check the actual loaded module path; remove old manual copies that shadow HACS.
   Confirm the MQTT plugin namespace matches `mqtt_namespace` and that only one
   producer writes the production diagnostic topic.
3. Verify NTP/UTC before setting `clock_verified`. Verify sensor locations,
   report semantics, transformations and actual-upstream airflow before setting
   global/per-source reporting flags and `airflow_basis: actual_upstream`.
   Record accuracy bounds or explicitly retain provisional empirical status.
4. Collect/review/accept the four off-period baseline windows. Then collect and
   explicitly accept the owner-confirmed healthy reference. Acceptance cannot be
   inferred from elapsed time or from an apparently good score.
5. Observe >=24 h and three natural cycles. Verify source ages, exclusions,
   repeatability, flow coverage, uncertainty, timing and failure isolation. Full
   canister readiness still requires two qualified high-demand hours. Missing
   natural coverage stays insufficient/unqualified.
6. Demonstrate rollback: disable only `hapsic_diagnostics` in apps.yaml and restart
   AppDaemon. New HA entities must expire within 30 s; controller/legacy telemetry
   continue. Keep the diagnostic data file for forensic recovery; never copy it
   into a different generation. Firmware rollback uses the reviewed base variant
   and preserves the original control NVS offsets. Verify actual control state
   after rollback before declaring commissioning complete.

## Source verification and known field gaps

Implementation baseline: HAPSIC tag v2.7.4 (`f8adb98`, release tree matches
`47af93e`); site consumer baseline `e87a10f`. The unrelated safety-remediation
checkout and its dirty status file are excluded. AppDaemon add-on observed
2026-10-17: version 0.19.2, started. Exact loaded HAPSIC version/module checksum
could not be read from the SSH add-on mount and remains a rollout prerequisite.

The live HA source check on 2026-10-17 confirmed F, %, m3/h and inHg units. The
Shelly temperature source was >8 minutes old, exceeding the 120 s policy. Reporting
contracts, RH scaling and flow standardization remain unverified; defaults therefore
do not learn. This is an explicit field-validity gate, not a software test failure.
The original 275.6% and 132% incident traces were not found in the repository;
percentage regression fixtures are synthetic, not exact incident replays.

Authoritative references:

- [PsychroLib SI humidity ratio and specific volume](https://psychrometrics.github.io/psychrolib/api_docs.html#psychrolib.GetMoistAirVolume)
- [AppDaemon cached state API](https://appdaemon.readthedocs.io/en/latest/AD_API_REFERENCE.html#appdaemon.entity.Entity.get_state)
- [AppDaemon MQTT namespace, events and services](https://appdaemon.readthedocs.io/en/latest/MQTT_API_REFERENCE.html)
- [HA MQTT sensor nulls, expiry and retained replay](https://www.home-assistant.io/integrations/sensor.mqtt/)
- [HA numeric-state trigger transition semantics](https://www.home-assistant.io/docs/automation/trigger/#numeric-state-trigger)
- [ESP-IDF NVS transaction/storage API](https://docs.espressif.com/projects/esp-idf/en/v5.3.2/esp32s3/api-reference/storage/nvs_flash.html)

Single-model independent implementation reviews were applied. Cross-model review
was explicitly skipped in the approved plan. Software verification does not establish
measurement accuracy or satisfy the natural-cycle commissioning gate.
