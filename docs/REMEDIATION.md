# Remediation work

Baseline: `b59d672`, branch `fix/vdi-special-characters`.
Initial tracked/staged diff: empty; `.context-compress/` was already untracked.
One writer/reviewer: coordinator, no subagents at the user's request.
No commit, publish, paid inference, real screen capture or foreign-window input.

## Acceptance and ownership

All paths below are owned serially by the coordinator.

- [x] 1. Resumable typing: cursor, pending autoclose action, worker ownership; platform/app tests.
- [x] 2. Common profile lock: core/Python startup and subprocess compatibility tests.
- [x] 3. Transactional history deletion with in-memory, bounded, retryable undo.
- [x] 4. Unknown profile fields retained; invalid settings cannot be overwritten silently.
- [x] 5. Rust capture formats with literal-text fallback and raw response preserved.
- [x] 6. Idle engine unloading coordinated with active operations.
- [x] 7. End-to-end clipboard timing separate from inference timing.
- [x] 8. Owned engine process lifetime and asynchronous Python shutdown.
- [x] 9. Safe provider errors, tar disk preflight, nonblocking status refresh.
- [x] 10. CI, JS checks, dependency checks and honest platform documentation.

## Integrated checks (Windows x64)

- Full pytest invocation: **862 passed, 1 skipped**, 133.97 s.
- `cargo fmt --all --check`: passed.
- `cargo clippy --workspace --all-targets --locked -- -D warnings`: passed.
- `cargo test --workspace --locked`: **134 passed**, including 11 required
  Python compatibility tests. The opt-in real-keyring Rust test returns early
  unless `CTRLCCTRLV_KEYRING_TEST` is set; it is not native keyring acceptance.
- `cargo build -p ctrlcctrlv-app --locked`: passed.
- `node scripts/verify_rust_ui.mjs`: passed (IPC stubs, no webview rendering).
- `python -m ruff check --select E9,F63,F7,F82 src tests scripts`: passed.
- `python -m pip_audit --skip-editable`: no known vulnerabilities after upgrading
  pip in this task's ignored `.venv`. The editable application is skipped.
- `cargo audit`: exit 0, no vulnerability findings, 10 warnings remain visible.
  Patched SQLite, rustls-webpki, webpki-root-certs and rustix; replaced two yanked
  package versions. Unmaintained transitive dependencies need upstream work.
- `cargo +1.89.0 check --workspace --locked --target-dir artifacts/msrv-1.89 -j 2`:
  passed, 5 min 02 s.
- `python scripts/build_windows.py --console --output artifacts/remediation-build/windows`:
  passed, including frozen GUI/diagnostics, OCR transport/cancellation,
  formatters, text/CSV/XLSX export and disposable credential smoke checks.
  Both new Python lock/job modules are present in the frozen module inventory.
  Output: `artifacts/remediation-build/windows/ctrlCctrlV-windows-x64.zip`.
  PyInstaller warned about the elevated shell; no installed user app was replaced.

Rust check/build logs: `artifacts/remediation-checks/` (ignored).
Native Windows Job Object tests use owned disposable processes and passed.
macOS, true VDI, interactive editor behavior and remote GitHub Actions were not
executed here. No release, install-over-user-app, commit or push.

## Review

Self-review only, as requested: traced cursor/pending-delete boundaries, lock
ordering, deletion identity and DB rollback, unknown-field roundtrips, untrusted
provider-body output and shared-profile startup. Added regressions instead of
counting model agreement. No independent or cross-family review was performed.

The pre-existing `.context-compress/` is untouched. Tool-triggered Python
formatting also normalized layout in edited legacy modules; no unrelated module
was deliberately reformatted.

## Verification gate

Add regressions before or alongside each change. Final: Rust fmt/clippy/workspace
tests with Python compatibility required; one complete pytest invocation; JS smoke.
Use synthetic temporary profiles and owned test processes only. Keep skips,
pre-existing failures, unavailable platforms and unresolved risks explicit.
Do not treat unit tests as VDI/macOS/native-editor acceptance.

## Design constraints

- Preserve profile paths, credential service, existing JSON fields and SQLite tables.
- Delete history rows immediately; undo content belongs only in process memory.
- No automatic paid retries or arbitrary provider error bodies in UI/logs.
- Recovery of invalid settings is explicit; preserve original bytes first.
- New profile lock protects updated versions; old binaries must be closed for upgrade.
- Context7 quota was exhausted; consult official crate/platform documentation instead.
- Rust 1.88 check passed before the security upgrade. Patched rusqlite requires
  1.89, so the workspace minimum and CI were deliberately raised to 1.89 rather
  than retaining a known-vulnerable SQLite dependency.
- Remaining process-lifetime and native-acceptance limitations are explicit in
  `PROFILE_COMPATIBILITY.md`; kill-on-close starts only after job attachment.
