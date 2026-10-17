"""Build and run the actual host-controller legacy regressions and replay driver."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build(binary, baseline=None):
    folder = ROOT / ".esphome/build/hapsic-scenarios"
    objects = sorted((folder / ".pioenvs/hapsic-scenarios/src").rglob("*.o"))
    objects = [path for path in objects if path.name != "main.o" and not str(path).endswith("/host/core.o")]
    if not any(str(path).endswith("/hapsic/hapsic.o") for path in objects):
        raise SystemExit("Build the actual host controller first: bash run_cpp.sh")
    extra = []
    if baseline:
        source = binary.with_suffix(".cpp")
        source.write_bytes(
            subprocess.check_output(["git", "show", baseline + ":components/hapsic/hapsic.cpp"], cwd=ROOT)
        )
        source.with_name("hapsic.h").write_bytes((folder / "src/esphome/components/hapsic/hapsic.h").read_bytes())
        objects = [p for p in objects if not str(p).endswith("/hapsic/hapsic.o")]
        extra = ["-DLEGACY_BASELINE", str(source)]
    subprocess.run(
        [
            "c++",
            "-std=c++20",
            "-DUSE_HOST",
            "-I",
            str(folder / "src"),
            "-I",
            str(ROOT),
            "-I",
            str(folder / ".piolibdeps/hapsic-scenarios/ArduinoJson/src"),
            str(ROOT / "tests/legacy_replay_native.cpp"),
            *map(str, objects),
            *extra,
            "-pthread",
            "-o",
            str(binary),
        ],
        check=True,
    )


def verify(binary):
    data = json.loads(
        subprocess.check_output([str(binary)], text=True),
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    assert data["health"]["chi_ratio"] != 1
    assert data["health"]["chi_eligible"] is True
    assert data["health"]["math_revision"] == 2
    assert data["io"]["steam_mass_lbs"] > 4
    assert data["physics"]["flux_net"] is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--baseline", help="Build unchanged controller from this Git revision")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="hapsic-legacy-native-") as folder:
        binary = args.binary.resolve() if args.binary else Path(folder) / "legacy"
        build(binary, args.baseline)
        if not args.baseline:
            verify(binary)
    print("Actual native legacy math, training, persistence and MQTT regressions passed")


if __name__ == "__main__":
    main()
