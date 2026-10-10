# HAPSIC on the existing Halo CI VM

HAPSIC uses a separate repository-scoped registration on the existing Ubuntu
24.04 x64 Halo CI guest (the VM hosting `proxmox-halo-ci-110`). The routing labels
are `self-hosted`, `Linux`, `X64`, `halo-ci`, and `hapsic`. This does not install
anything on the Halo inference host or change another repository's runner.

## Register or recover

1. Inspect guest capacity and existing runner services. Run `bootstrap.sh` as the
   guest administrator. It creates the unprivileged `hapsic-ci` account, installs
   dependencies, and verifies the official runner archive SHA256. Existing
   registrations are preserved. Official runner auto-update remains enabled.
2. Obtain a short-lived token with
   `gh api --method POST repos/astyrrian1/hapsic/actions/runners/registration-token`.
   Deliver only that token to the guest over SSH stdin; do not copy an account PAT
   or save tokens in this repository, shell history, or CI logs.
3. As `hapsic-ci`, run `/opt/hapsic-ci-runner/config.sh --unattended` from that
   directory with repository URL `https://github.com/astyrrian1/hapsic`, runner
   name `proxmox-hapsic-ci-110`, labels `halo-ci,hapsic`, and the token. Do not use
   `--replace` on another registration.
4. As the administrator, use the generated `svc.sh install hapsic-ci`. Install
   `hardening.conf` as a drop-in for **that HAPSIC service only**, reload systemd,
   and start/enable the service. Verify the merged unit before starting it.
5. Verify GitHub reports the HAPSIC registration online and run the full workflow
   on a work branch. An online listener alone is not acceptance.

The service has no sudo, Docker socket, GPU, production credentials, or private
network access. Its writable directories are its own runner, home, and Python
cache. It is capped at 2 GiB and 150% CPU; firmware compilation uses one worker.
Stop only the HAPSIC runner service to pause it. Preserve unrelated services and
registrations during recovery. Keep the VM and runner security updates current.

## Required validation

Pushes to `main` and `codex/**`, PRs to `main`, and manual runs execute
`bash run_presubmit.sh`: lint, all discovered pytest regressions (including
actual-source Python/C++ and sanitizers), standalone suites, scenarios, native
simulation, and fresh production/desk builds. New `test_*.py` regression files
are automatically collected. Both historical required check names remain.
Fork PRs run the identical gate on hosted Ubuntu; checkout credentials are not
persisted. Presubmit and scenario logs are uploaded even after failure.

CI uses synthetic firmware build secrets and never flashes or controls hardware.
The optional live MQTT checks and physical qualification remain separate,
authorized desk procedures. Incomplete planned remediation is not represented
as passing coverage. See `safety-remediation-status.json` for outstanding work.

Runner archive provenance:
[official v2.337.0 release and checksums](https://github.com/actions/runner/releases/tag/v2.337.0).
