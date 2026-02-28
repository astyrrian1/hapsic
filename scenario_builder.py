import csv
from datetime import datetime, timedelta

start_time = datetime(2026, 2, 17, 10, 0, 0)

csv_data = []

def add_tick(t_offset_sec, extract_temp, extract_rh, duct_temp, duct_rh, supply_flow):
    dt = start_time + timedelta(seconds=t_offset_sec)
    time_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    csv_data.append(["sim_extract_temp", extract_temp, time_str])
    csv_data.append(["sim_extract_rh", extract_rh, time_str])
    csv_data.append(["sim_avg_temp", extract_temp, time_str])
    csv_data.append(["sim_avg_rh", extract_rh, time_str])
    csv_data.append(["sim_duct_temp", duct_temp, time_str])
    csv_data.append(["sim_duct_rh", duct_rh, time_str])
    csv_data.append(["sim_supply_flow", supply_flow, time_str])

def add_tick_detailed(t_offset_sec, avg_temp, avg_rh, extract_temp, extract_rh, duct_temp, duct_rh, supply_flow):
    dt = start_time + timedelta(seconds=t_offset_sec)
    time_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    csv_data.append(["sim_extract_temp", extract_temp, time_str])
    csv_data.append(["sim_extract_rh", extract_rh, time_str])
    csv_data.append(["sim_avg_temp", avg_temp, time_str])
    csv_data.append(["sim_avg_rh", avg_rh, time_str])
    csv_data.append(["sim_duct_temp", duct_temp, time_str])
    csv_data.append(["sim_duct_rh", duct_rh, time_str])
    csv_data.append(["sim_supply_flow", supply_flow, time_str])

# Scenario 1: ACTIVE_CRUISE (Deficit > 0.55C)
# Target DP is 10.0C. We need room DP < 9.45C. 
# Room temp = 20C, RH = 30% -> Room DP = 1.9C -> Deficit is 8.1C. (Will enter ACTIVE_CRUISE)
tick = 0
for i in range(20):
    add_tick(tick, 20.0, 30.0, 20.0, 30.0, 400.0)
    tick += 5

# Scenario 2: TURBO_PENDING -> ACTIVE_TURBO
# Keep Deficit high. But make pre_steam_temp (extract_temp) very low so target RH > 82%.
# Extract Temp = 12C, RH = 30%. Target DP is 10C. Deficit is high. 
# The engine will demand target_duct_dp up to 15.56C. 
# Relative humidity of 15.56C DP at 12C Temp is basically > 100%, exceeding 82%.
# Will trigger TURBO_PENDING. Keep flow > 340 so it transitions to ACTIVE_TURBO.
for i in range(150): # Give it time to wind up steam to > 9.5V
    add_tick(tick, 12.0, 30.0, 12.0, 30.0, 400.0)
    tick += 5
    
# Scenario 3: ACTIVE_CRUISE -> STANDBY
# Drop Target DP deficit to 0 by making room DP = 10.0C.
# Room temp = 20C, RH = 52.8% -> DP approx 10.0C. Deficit = 0.0.
# Will transition to STANDBY.
for i in range(10):
    add_tick(tick, 20.0, 52.8, 20.0, 52.8, 400.0)
    tick += 5

# Scenario 4: STANDBY -> HYGIENIC_PURGE
# Make room DP > 10.55C -> Deficit < -0.55C
# Room temp = 20C, RH = 60% -> DP approx 12.0C. Deficit = -2.0C.
for i in range(10):
    add_tick(tick, 20.0, 60.0, 20.0, 60.0, 400.0)
    tick += 5

# Scenario 5: HYGIENIC_PURGE -> STANDBY (After 10 mins)
# Maintain Purge conditions for 10 minutes (120 ticks) -> should revert to STANDBY automatically
for i in range(125):
    add_tick(tick, 20.0, 60.0, 20.0, 60.0, 400.0)
    tick += 5

# Scenario 6: BOILING_NO_AIRFLOW FAULT
# Deficit high -> ACTIVE_CRUISE. Steam voltage climbs.
# Flow drops to 0. (Below 17.0 m3/h).
# After 10 mins (120 ticks), FAULT.
for i in range(50): # enter cruise and wind up steam
    add_tick(tick, 20.0, 30.0, 20.0, 30.0, 400.0)
    tick += 5

for i in range(130): # drop flow to 0 for 10+ mins
    add_tick(tick, 20.0, 30.0, 20.0, 30.0, 0.0)
    tick += 5

# Scenario 7: SENSOR FALLBACK & 30-MIN CACHE FAULT
# Restore flow and valid state.
for i in range(20): # Normal cruise recovery
    add_tick_detailed(tick, 20.0, 30.0, 20.0, 30.0, 20.0, 30.0, 400.0)
    tick += 5

# House sensors die (NaN). Will fallback to Extract CAN.
for i in range(12): # 1 minute
    add_tick_detailed(tick, "NAN", "NAN", 20.0, 30.0, 20.0, 30.0, 400.0)
    tick += 5

# Extract CAN & HA die (NaN), and Supply CAN & HA die (NaN).
# Both sets of sensors drop, engaging the 30-minute fallback cache for both Inside DP and Supply W.
for i in range(370): # Wait 30.8 minutes (370 ticks). Cache expires at 30.0 minutes (360 ticks).
    add_tick_detailed(tick, "NAN", "NAN", "NAN", "NAN", 20.0, 30.0, 400.0)
    tick += 5

# Write out to scenario_data.csv
with open('scenario_data.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['entity_id', 'state', 'last_changed'])
    for row in csv_data:
        writer.writerow(row)

print("Generated scenario_data.csv with", len(csv_data), "rows.")
