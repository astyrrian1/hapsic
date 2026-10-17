"""Export private, bounded HA controller history; never issues a service call."""

import argparse
import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENTITIES = [
    "sensor.hapsic_cleansed_supply_temp",
    "sensor.hapsic_cleansed_supply_rh",
    "sensor.hapsic_cleansed_post_steam_temp",
    "sensor.hapsic_cleansed_post_steam_rh",
    "sensor.hapsic_room_average_temp",
    "sensor.hapsic_room_average_rh",
    "sensor.hapsic_cleansed_inside_temp",
    "sensor.hapsic_cleansed_inside_rh",
    "sensor.hapsic_supply_flow",
    "sensor.hapsic_extract_flow",
    "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature",
    "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity",
    "sensor.zehnder_comfoair_q_a4cb9c_bypass_state",
    "sensor.shelly0110dimg3_28372f3e866c_input_100_analog",
    "light.shelly0110dimg3_28372f3e866c",
    "input_number.target_dew_point",
    "input_number.humidifier_max_capacity",
    "sensor.hapsic_steam_voltage",
    "sensor.hapsic_fsm_state",
    "sensor.hapsic_health_boil_status",
    "input_number.hapsic_chi_ema",
    "input_text.hapsic_boiler_curve",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.hours <= 168:
        parser.error("Choose 1..168 hours; expand only when coverage requires it")
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)

    def get(path):
        req = urllib.request.Request(
            os.environ["HA_URL"].rstrip("/") + "/api/" + path,
            headers={"Authorization": "Bearer " + os.environ["HA_TOKEN"]},
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)

    states = {row["entity_id"]: row for row in get("states") if row["entity_id"] in ENTITIES}
    history = []
    # Small batches avoid overwhelming the HA recorder API.
    for index in range(0, len(ENTITIES), 5):
        query = urllib.parse.urlencode(
            dict(
                filter_entity_id=",".join(ENTITIES[index : index + 5]),
                end_time=end.isoformat(),
                significant_changes_only=0,
            )
        )
        history.extend(get("history/period/" + urllib.parse.quote(start.isoformat(), safe="") + "?" + query))
    content = json.dumps(history, separators=(",", ":")).encode()
    path = args.output / "history.json"
    path.write_bytes(content)
    path.chmod(0o600)
    manifest = dict(
        recording_type="recorded",
        start=start.isoformat(),
        end=end.isoformat(),
        sha256=hashlib.sha256(content).hexdigest(),
        ha_version=get("config")["version"],
        states=states,
        inventory={series[0]["entity_id"]: len(series) for series in history if series},
        processing="HA controller boundary before the controller duct EMA; cleansed source values",
        native_substitution="HA equivalents replace local/CAN sensor signals; reconstructed input experiment",
        initial_state="fresh or migrated boot; no claim of historical integrator/filter restoration",
        missing=[entity for entity in ENTITIES if entity not in states],
    )
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (args.output / "manifest.json").chmod(0o600)
    print(
        json.dumps(
            dict(
                start=manifest["start"],
                end=manifest["end"],
                inventory=manifest["inventory"],
                missing=manifest["missing"],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
