"""Authoritative actual-controller replay gate using explicitly synthetic CI inputs.

Real-data verification uses the same controller_replay.py runner with a private
capture directory. This generated fixture is never described as incident data.
"""

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from controller_replay import FIELDS
from verify_legacy_control import build, verify


def fixture(directory):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = [70, 55, 400, 400, 68, 25, 68, 30, 45, 35, 50, 4.61, 0]
    data = []
    states = {}
    for index, (key, value) in enumerate(zip(FIELDS, values)):
        unit = "°F" if index in (0, 4, 6, 8, 10) else "lbs/hr" if index == 11 else "m³/h" if index in (2, 3) else "%"
        states[key] = dict(attributes=dict(unit_of_measurement=unit))
        data.append(
            [
                dict(
                    entity_id=key,
                    state=str(value),
                    last_changed=start.isoformat(),
                    attributes=dict(unit_of_measurement=unit),
                )
            ]
        )
    for key, value in [
        ("light.shelly0110dimg3_28372f3e866c", "off"),
        ("sensor.shelly0110dimg3_28372f3e866c_input_100_analog", "55"),
    ]:
        data.append([dict(entity_id=key, state=value, last_changed=start.isoformat())])
    data.append(
        [
            dict(
                entity_id="sensor.hapsic_steam_voltage",
                state=str(v),
                last_changed=(start + timedelta(seconds=t)).isoformat(),
                attributes=dict(unit_of_measurement="V"),
            )
            for t, v in ((0, 0), (900, 9.5), (3900, 0))
        ]
    )
    states["sensor.hapsic_steam_voltage"] = dict(attributes=dict(unit_of_measurement="V"))
    content = json.dumps(data).encode()
    (directory / "history.json").write_bytes(content)
    (directory / "manifest.json").write_text(
        json.dumps(
            dict(
                recording_type="synthetic",
                start=start.isoformat(),
                end=(start + timedelta(seconds=4500)).isoformat(),
                states=states,
                sha256=hashlib.sha256(content).hexdigest(),
                processing="Constructed controller-boundary input before controller EMA",
                native_substitution="Synthetic physically equivalent native inputs",
            )
        )
    )


def main():
    with tempfile.TemporaryDirectory(prefix="hapsic-structured-replay-") as folder:
        root = Path(folder)
        fixture(root)
        binary, baseline = root / "native", root / "baseline"
        build(binary)
        verify(binary)
        build(baseline, "322e9ed")
        subprocess.run(
            [
                sys.executable,
                "controller_replay.py",
                "--input",
                str(root),
                "--output",
                str(root / "report.json"),
                "--binary",
                str(binary),
                "--baseline-binary",
                str(baseline),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
