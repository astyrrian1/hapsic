# Recorded-input legacy controller replay

The actual Python and compiled native C++ controllers were executed on the same
48-hour HA boundary-input recording from October 15–17, 2026. Baseline: `322e9ed`.
This is a reconstructed-input experiment with fresh/migrated initial state, not an
exact firmware incident replay. Raw recordings and the detailed manifest remain private.

| Check | Result |
| --- | ---: |
| Five-second controller ticks | 34561 |
| Eligible historical-command arithmetic frames | 6910 |
| Maximum measured-delivery disagreement | 0.000001575 lb/h |
| Repaired native command-bound violations | 0 |
| Unchanged native command-bound violations | 245 |
| Maximum repaired native command | 9.50 V |
| Maximum unchanged native command | 9.999748 V |

History SHA-256: `ac071541156ceaa4ee20c3f30b179874d9db652ff90a662ecd9d6eae5963efc5`.

Both implementations exercised standby, active cruise and fault. The arithmetic
lane compares measured delivery, instantaneous CHI, CHI EMA, counted bin values,
effective capacity, ventilation loss and net flux, accounting for publication
rounding. Counts agree exactly. Independent fixtures cover warmup/stasis eligibility,
50-sample boundaries, outliers, corrupt/queued storage and restart. Actual AppDaemon
adapter scheduling tests inject queue saturation, MQTT outages and storage failures;
controller actuator calls and learned/control state remain identical with and
without the observer. Native actual-controller isolation is also retained.

The original unit regression produced 0.327934 lb/h instead of 2.295540 lb/h for the
same constructed g/kg difference. Correcting units and training changes future
feed-forward/capacity decisions. Replay additionally exposed a clamp-after-slew
ordering defect; the regression failed before the final envelope guard was added.
These repairs explain the changed model/control paths; no PID or feasibility
hysteresis tuning was used to force full-platform equality.

Against its own baseline, repaired Python changed 3,491 command ticks and native
C++ changed 2,443 (difference greater than 0.01 V). Native state occupancy and
transitions stayed identical: 4,164 active ticks, eight entries into active cruise,
and 9,639 fault ticks, all for the existing economizer bypass interlock. Python's
fault occupancy also stayed unchanged at 9,662 ticks; active occupancy changed from
6,334 to 7,312 ticks as the counted learning initialization changed capacity and
feed-forward. These are counterfactual commands, not predicted plant improvements.

Native reported limiter ticks shifted from 7,599 up-slew / 2,113 down-slew to
1,546 / 6,487; the final envelope guard was reported on 116 ticks. Limiter text can
persist through scheduler early returns, so these counts describe reported states,
not independent clamp events. The maximum repaired-versus-baseline internal-command difference
of 18.94 V includes invalid negative baseline values; it is not a physical DAC swing.
Python/native bypass fault duration already differed before repair (9,662 versus
9,639 ticks), and their startup/filter/FSM policies remain distinct. No gains or
interlock thresholds were changed to conceal those pre-existing differences.

Coverage does not establish native raw-sensor interlock behavior or exact historical
initial state. HA source data is before the controller duct EMA; local/CAN firmware
sources are explicitly substituted. Boiling/stasis state used for arithmetic is
reconstructed, not claimed as recorded truth. Full-controller state and gate behavior
are tested independently. Seven-day/cycle quotas are not used. Synthetic CI traces
remain separate and are not substituted for this real-data run. The unavailable
original 275.6%/132% incident traces are not claimed as reproduced.

A command difference in free-running replay is a software behavior comparison.
Recorded humidity reflects historical actuation, so it cannot establish the plant's
response to a different command. Independent observer qualification and field
commissioning remain separate acceptance gates.
