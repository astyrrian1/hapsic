---
description: Release a new HAPSIC version to HACS with changelog and GitHub Release
---

# HACS Release Workflow

Release a new version of the HAPSIC Python controller to HACS.

## Prerequisites

- All changes committed and pushed to `main`
- Presubmit passing (`/presubmit`)
- `CHANGELOG.md` updated with the new version entry

## Steps

1. Update `CHANGELOG.md` with the new version entry at the top of the file. Follow the existing format — include Added, Changed, Fixed, Architecture sections as appropriate. The version number follows semver: bump patch for fixes, minor for features, major for breaking changes.

2. Update the version string in `apps/hapsic_controller/hapsic_controller.py` if there's a `VERSION` constant or docstring reference.

3. Commit the changelog and any final changes:
```bash
git add -A
git commit -m "release: vX.Y.Z — brief description"
```

4. Push to main (pre-push hook runs full CI):
```bash
git push origin main
```

5. Wait for all 12 CI stages to pass.

6. Create an annotated git tag:
```bash
git tag -a vX.Y.Z -m "HAPSIC vX.Y.Z — brief description

See CHANGELOG.md for full release notes."
```

7. Push the tag (pre-push hook runs again):
```bash
git push origin vX.Y.Z
```

8. Create a GitHub Release (this is what HACS actually tracks — bare tags are NOT enough):
```bash
gh release create vX.Y.Z --title "HAPSIC vX.Y.Z" --notes-file CHANGELOG.md --latest
```

9. Verify HACS picks up the new version. In Home Assistant, go to HACS → Automation and confirm HAPSIC Controller shows the new version with no pending update.

## Important Notes

- **HACS requires GitHub Releases**, not just git tags. Without step 8, HACS will show commit hashes instead of version numbers.
- **CHANGELOG.md is the release notes source.** Every release must have a changelog entry explaining what changed since the last version. The `--notes-file CHANGELOG.md` flag in step 8 attaches the full changelog to the GitHub Release.
- **Do not retag existing versions** unless absolutely necessary. HACS caches release metadata; moving tags can cause stale version display until HACS refreshes.
- The pre-push hook runs the full 12-stage CI pipeline on every push, including tag pushes. Budget ~90 seconds per push.
