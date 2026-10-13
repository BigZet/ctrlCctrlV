# Desktop release 0.1.1 Implementation Plan

> **For agentic workers:** Execute the independent Windows and macOS packaging tasks in parallel, then review and verify the whole release before publication.

**Goal:** Publish a GitHub release with installers for Windows x64 and macOS Apple Silicon, including the verified Mac fixes.

**Architecture:** Build each package on its target OS. GitHub Actions tests the source, builds installers, and runs the installed applications in isolated smoke profiles. Publish assets from the successful workflow for the exact tagged commit.

**Tech Stack:** Python 3.12, PySide6, PyInstaller, Inno Setup 6, hdiutil, GitHub Actions and CLI.

**Spec:** User request to prepare Windows and Mac installers and publish on GitHub; package scope and installation instructions are recorded in `docs/releases/v0.1.1.md`.

## Global Constraints

- Windows x64 and macOS Apple Silicon; no Intel Mac or Windows ARM claim.
- Version 0.1.1 must match Python metadata, UI, bundle and installer names.
- Preserve existing profiles, models and keys during upgrade/uninstall.
- No signing identities are available: disclose Windows unsigned / macOS ad-hoc and no notarization.
- Keep the repository private; publish in BigZet/ctrlCctrlV.

## Review Focus

- Installer upgrade must keep user data; test installed executable after upgrade and uninstall cleanup.
- Application copied out of DMG must retain permissions/signature and pass smoke.
- Failed tests or build must prevent asset upload and release publication.
- Release assets must come from the tested commit and have SHA256 checksums.
- Models are downloaded on request after installation; do not include user models, keys or profiles in packages.

## Tasks

- [x] Windows: extend `scripts/build_windows.py`, add Inno Setup definition and focused packaging tests. Verify silent install, repeated installation, frozen smoke and uninstall on Windows Actions runner.
- [x] macOS: extend `scripts/build_macos.py` and focused tests for DMG staging, checksum and validation. Verify a temporary installed copy; preserve existing app.
- [x] Metadata/docs: update `pyproject.toml`, `__init__.py`, `.spec`, sidebar version, README and release notes.
- [x] CI: extend `.github/workflows/desktop.yml` to build and verify both installers, upload exact packages and checksums.
- [x] Verify and publish: run local suite/source smoke, review changes, commit/push, wait for both CI jobs, download and checksum artifacts on a GitHub runner, tag tested commit and publish a fully populated GitHub release.

Completed: [v0.1.1](https://github.com/BigZet/ctrlCctrlV/releases/tag/v0.1.1). Build run `38380193396`, publication run `38382157565`; see `docs/STATUS.md`. GitHub publication was used because large downloads to the local Mac were interrupted by connection failures. The published packages retain the exact tested commit `fc87c96e27e0b96bb2a836caef37969a9e4caff3`.
