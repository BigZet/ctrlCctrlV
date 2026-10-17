# ctrlcctrlv-core

The Rust core of ctrlCctrlV, without UI. Targets: Windows x64 and macOS ARM64.

| Module | Replaces (Python) | What it does |
|---|---|---|
| `settings` | `settings.py` | `settings.json` in the same format; invalid files fall back to defaults; unknown keys from newer versions are kept |
| `history` | `history.py` | `history.sqlite3` with the same schema and Unicode case-folded search; extra `runs` table keeps several model answers per capture |
| `credentials` | `settings.Credentials` | OpenRouter key in Credential Manager / Keychain under the Python `keyring` names |
| `catalog`, `models` | `catalog.py`, `custom_models.py` | Pinned models and engine packages, `custom-models.json` |
| `ocr`, `lighton` | `ocr.py` | One shared `reqwest` client, same request body, error messages and LightOn cleanup; `recognize_all` asks several models at once |
| `download`, `extract` | `runtime.py` | Resumable, range-checked, SHA256-verified downloads; safe archive extraction |
| `runtime`, `hardware`, `keyfile` | `runtime.py`, `hardware.py`, `security.py` | Owned loopback `llama-server` with a per-session key file; on Windows a job object ends the engine with the app |

## Tests

```sh
cargo test --workspace
```

`tests/python_compat.rs` runs the Python app's code (`tests/python/compat.py`)
against the Rust code: settings and history written by one are read by the
other, and request bodies, error texts, hotkey parsing and LightOn cleanup are
identical. It needs Python with `httpx`; without it the tests are skipped unless
`CTRLCCTRLV_REQUIRE_PYTHON=1` (set in CI). `tests/credentials.rs` checks the
real key store against Python `keyring` with `CTRLCCTRLV_KEYRING_TEST=1`.
