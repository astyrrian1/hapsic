# Legacy control calculations and learning, revision 2

Revision 2 corrects the firmware's mixed humidity-ratio and mass units. Firmware
inputs remain Celsius, g/kg, m³/h and kg/h. Legacy controller calculations and
pound-labeled telemetry use grains/lb and lb/h, matching the Python controller.
The separate delivery observer, calibration generations and schema 1 are unchanged.

## Behavior and compatibility

- Convert g/kg to grains/lb by multiplying by seven. Convert kg/h capacity to lb/h
  before theoretical delivery, CHI, learned-capacity fallback and feed-forward.
- Legacy bins use the arithmetic mean of their first 50 eligible positive ticks;
  observation 51 onward uses the existing EMA coefficient 0.002. Counts survive
  restart. These are control ticks using the existing input/cache policy, not
  independent sensor observations or owner-confirmed performance references.
- `health.math_revision` is 2. `health.chi_eligible` describes algorithm eligibility,
  not measurement accuracy or verified source provenance. Ineligible CHI and measured
  delivery are null; unavailable loss/net flux are null. Eligible negative measured
  delivery remains signed; legacy CHI remains clipped to 0–2 and eligible efficiency
  retains its positive-delivery convention. Values above 100% remain visible.
- MQTT and native API mass values use the same pound basis. Native instantaneous
  health sensors publish unavailable when ineligible. Existing v1 HA consumers are
  supported; v2 consumers gate eligibility and preserve real zero values.
- Real-data replay exposed a pre-existing C++ clamp ordering defect: slew could
  restore voltage after earlier clamping. The final step now enforces the existing
  0–9.5 V envelope, humidity ceiling outside stasis, and minimum-fire limit before
  output. PID gains, feasibility hysteresis and stasis policy were not retuned.

## State migration and rollback

The Python helper `input_text.hapsic_boiler_curve` uses compact JSON:

```json
{"v":2,"curve":[0,0,0,0],"counts":[0,0,0,0]}
```

The helper must allow 255 characters and restore its last state (no `initial`).
Reuse an existing UI helper; do not define a duplicate YAML helper. The distributed
package supplies a definition for new installations; omit that block if the helper
already exists in the UI. The site's existing UI helper is reused and checked live.
The controller checks the actual helper maximum before checkpointing.

Old rate-only arrays have unknown sample counts. They are backed up verbatim to
`learning_backup_path` (default `/config/hapsic-data/legacy-curve-v1.json`) and bins
restart untrained. Existing Python CHI is retained only when finite and in 0–2.
The original backup is never replaced. Both its file and directory are synchronized
and its contents verified before migration, including retries after an interrupted
attempt. Backup failures prevent overwriting the old helper. An initially unavailable
helper is retried at checkpoints without discarding fresh in-memory observations.

Firmware uses a new `hapsic_learning_v2` preference record containing actual counts.
Old unit-contaminated CHI/curves are not rescaled. The original `hapsic_state` key is
left intact. Only a validated Celsius target from known magic 0xABCD1235 can migrate;
ambiguous older target/RH records are not reinterpreted. Current HA settings win once
received. Bad versions, nonfinite values and inconsistent count/rate pairs are rejected.

Checkpoints use normal ESPHome preference queuing and HA helper services; queued
writes are not durability acknowledgements. Abrupt loss can discard unflushed learning
but cannot promote a partial bin into a trained one. No global preference flush or
custom journal was added. For Python rollback, stop the app, restore the verified
legacy helper backup through `input_text.set_value`, restore the prior code and restart
AppDaemon. Preserve the v2 helper value separately if recovery may be needed. Firmware
rollback uses the preserved original preference key. Independent diagnostic state needs
no reset or migration.

## Reproducible verification

Build the actual host controller, then run:

```sh
python3 verify_legacy_control.py --binary /private/tmp/hapsic-legacy-native
python3 verify_legacy_control.py --binary /private/tmp/hapsic-legacy-baseline --baseline 322e9ed
python3 test_offline_parity.py
```

`test_offline_parity.py` is the authoritative structured synthetic CI replay. Its
fixture is explicitly synthetic. The old accelerated CSV runner is not evidence of
five-second controller timing; the new runner uses a virtual clock for every tick.
The older Python scenario adapter now leaves airflow in m³/h rather than converting
it a second time.

For private real-history verification, set `HA_URL` and `HA_TOKEN` in the environment:

```sh
python3 capture_controller_history.py --output /private/tmp/hapsic-history --hours 48
python3 controller_replay.py --input /private/tmp/hapsic-history \
  --output /private/tmp/hapsic-replay-report.json \
  --binary /private/tmp/hapsic-legacy-native \
  --baseline-binary /private/tmp/hapsic-legacy-baseline
```

Capture is read-only and keeps credentials out of the export. The manifest hashes
history and records units, processing stage, missing sources and HA version. Replay
rejects incompatible units and historical unit changes. The numeric lane compares
identical processed states and historical commands; shared reconstructed eligibility
proves arithmetic only. Independent actual-controller tests exercise eligibility.
Missing historical commands never count as recorded-command evidence. Historical
unit changes are rejected, including the historical commanded-voltage source. For
recordings, a gap over five minutes with no input events, including the recording
boundaries, marks continuity unknown: split the recording before scoring. Empty or
inverted recording windows are rejected. This conservative
export-quality check does not claim the sensors disconnected. Known synthetic holds
are identified explicitly; they are never used to certify real recording continuity.

The free-running lane preserves each implementation's filters and control logic.
Native GPIO/CAN inputs are represented by HA counterparts and identified as
substitutions. Starting state is a constructed fresh/migrated boot. Recorder change
history does not provide independent report heartbeats. After a calculated command
leaves history, downstream measurements reflect historical commands, so subsequent
learning is counterfactual. This does not predict the house's humidity response or
validate absolute moisture delivery.

Recorded-input results and remaining coverage limits are in
[legacy-replay-report.md](legacy-replay-report.md). Full recordings remain private.
