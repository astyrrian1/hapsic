"""Link a deterministic isolation test against the actual ESPHome host objects.

Run after run_cpp.sh. No mock controller and no changes to production code.
"""
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent
build = root / ".esphome/build/hapsic-scenarios"
objects = sorted((build / ".pioenvs/hapsic-scenarios/src").rglob("*.o"))
objects = [p for p in objects if p.name != "main.o" and not str(p).endswith("/host/core.o")]
arduino = build / ".piolibdeps/hapsic-scenarios/ArduinoJson/src"
if not objects or not any(str(p).endswith("/hapsic/hapsic.o") for p in objects):
    raise SystemExit("Build the actual ESPHome host controller first: bash run_cpp.sh")
with tempfile.TemporaryDirectory(prefix="hapsic-control-isolation-") as folder:
    binary = Path(folder) / "isolation"
    subprocess.run(["c++", "-std=c++20", "-DUSE_HOST", "-I", str(build / "src"), "-I", str(root), "-I", str(arduino),
                    str(root / "tests/diagnostic_control_native.cpp"), *map(str, objects),
                    "-pthread", "-o", str(binary)], check=True)
    subprocess.run([str(binary)], cwd=folder, check=True)
