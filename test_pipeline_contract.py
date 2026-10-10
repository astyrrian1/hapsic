"""The local gate and CI must exercise the same compiled implementation."""

import os
import re
import shlex
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def test_ci_runs_the_full_presubmit():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    runs = [step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]]
    assert any("bash run_presubmit.sh" in command for command in runs)
    assert not any("ruff check hapsic.py" in command for command in runs)


def test_presubmit_collects_regressions_and_uses_portable_formatter():
    source = (ROOT / "run_presubmit.sh").read_text()
    assert "python3 -m pytest" in source
    assert "/opt/homebrew" not in source
    assert 'cd "$SCRIPT_DIR"' in source


def test_build_dependencies_are_pinned():
    requirements = ((ROOT / "requirements-dev.txt").read_text()
                    + (ROOT / "requirements-firmware.txt").read_text())
    for dependency in ("pytest", "ruff", "esphome", "platformio", "clang-format", "pyyaml", "paho-mqtt"):
        assert re.search(rf"(?im)^{dependency}==[\d.]+$", requirements), dependency


def test_firmware_external_components_are_immutable():
    for filename in ("stamplc.yaml", "stamplc_desk.yaml"):
        sources = re.findall(r"source: (github://\S+)", (ROOT / filename).read_text())
        assert sources
        assert all(re.search(r"@[a-f0-9]{40}$", source) for source in sources), sources


def test_presubmit_ignores_an_unrelated_active_environment(tmp_path):
    script = tmp_path / "run_presubmit.sh"
    script.write_text((ROOT / script.name).read_text())
    local_bin = tmp_path / ".venv" / "bin"
    foreign_bin = tmp_path / "foreign" / "bin"
    for directory, code in ((local_bin, 91), (foreign_bin, 92)):
        directory.mkdir(parents=True)
        for name, body in (("ruff", f"exit {code}"), ("git", "echo .githooks")):
            command = directory / name
            command.write_text(f"#!/bin/bash\n{body}\n")
            command.chmod(0o755)
    (local_bin / "activate").write_text(f'export PATH={shlex.quote(str(local_bin))}:"$PATH"\n')
    env = dict(os.environ, VIRTUAL_ENV=str(foreign_bin.parent), PATH=f"{foreign_bin}:/usr/bin:/bin")
    result = subprocess.run(["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 91, result.stdout + result.stderr


def test_aggregate_runner_cannot_use_an_old_combined_esphome_install(tmp_path):
    script = tmp_path / "run_all_tests.sh"
    script.write_text((ROOT / script.name).read_text())
    (tmp_path / "verify.sh").write_text("esphome version\n")
    local_bin = tmp_path / ".venv" / "bin"
    wrapper_bin = tmp_path / "scripts"
    for directory, code in ((local_bin, 92), (wrapper_bin, 91)):
        directory.mkdir(parents=True)
        command = directory / "esphome"
        command.write_text(f"#!/bin/bash\nexit {code}\n")
        command.chmod(0o755)
    (local_bin / "activate").write_text(f'export PATH={shlex.quote(str(local_bin))}:"$PATH"\n')
    result = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 91, result.stdout + result.stderr


def test_m5gfx_uses_available_immutable_release_source():
    """The registry pin fails clean installs; resolve the same release from Git."""
    codegen = (ROOT / "components/hapsic/__init__.py").read_text()
    assert "https://github.com/m5stack/M5GFX.git#53a7184601f3667b030ba141c58b87ce2acfaa2a" in codegen
    for filename in ("stamplc.yaml", "stamplc_desk.yaml", "stamplc_hil.yaml"):
        assert "m5stack/M5GFX@0.2.19" not in (ROOT / filename).read_text()


def test_required_firmware_builds_discard_previous_binaries():
    for filename, config in (("run_presubmit.sh", "stamplc_desk.yaml"), ("verify.sh", "stamplc.yaml")):
        commands = (ROOT / filename).read_text()
        assert f"esphome clean {config}" in commands
        assert commands.index(f"esphome clean {config}") < commands.index(f"esphome compile {config}")


def test_ci_routes_trusted_work_to_halo_and_forks_to_hosted_linux():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    for job in workflow['jobs'].values():
        routing = job['runs-on']
        assert 'halo-ci' in routing
        assert 'self-hosted' in routing
        assert 'dockge' not in routing.lower()
        assert 'github.event.pull_request.head.repo.full_name' in routing
        assert 'ubuntu-24.04' in routing
    assert workflow['permissions'] == {'contents': 'read'}


def test_ci_covers_work_branches_and_retains_failure_evidence():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    triggers = workflow.get('on', workflow.get(True))
    assert 'codex/**' in triggers['push']['branches']
    job = workflow['jobs']['python-tests']
    assert job['timeout-minutes'] >= 30
    assert any(step.get('uses', '').startswith('actions/upload-artifact@')
               and step.get('if') == 'always()' for step in job['steps'])
    assert any('pipefail' in step.get('run', '') and 'tee' in step.get('run', '')
               and 'bash run_presubmit.sh' in step.get('run', '') for step in job['steps'])
    checkout = next(step for step in job['steps'] if step.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['persist-credentials'] is False


def test_halo_runner_provisioning_is_scoped_and_resource_bounded():
    bootstrap = (ROOT / "provisioning/ci-runner/bootstrap.sh").read_text()
    hardening = (ROOT / "provisioning/ci-runner/hardening.conf").read_text()
    assert 'hapsic-ci' in bootstrap and '/opt/hapsic-ci-runner' in bootstrap
    assert 'sha256sum --check' in bootstrap
    assert 'Existing runner preserved' in bootstrap
    for requirement in ('User=hapsic-ci', 'MemoryMax=2G', 'CPUQuota=150%',
                        'NoNewPrivileges=true', 'ProtectSystem=strict',
                        'PrivateDevices=true', 'MemorySwapMax=0'):
        assert requirement in hardening
    assert '/opt/halo-ci-runner' not in bootstrap + hardening
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert str(workflow['jobs']['python-tests']['env']['ESPHOME_DEFAULT_COMPILE_PROCESS_LIMIT']) == '1'
