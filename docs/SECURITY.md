# Security and threat model

Scope v0.x: research + paper trading on a personal Windows machine. No live broker integration exists.

| Threat | Impact | Control (implemented ✔ / planned ☐) |
|---|---|---|
| Accidental live order | real money loss | ✔ no live adapter in code; ✔ `OrderManager` refuses `is_live` brokers; ✔ `assert_live_allowed` always raises; ✔ no env var can enable live |
| Credential theft | account takeover | ✔ no credentials used; ✔ `.gitignore` blocks `.env`, `secrets/`, `*.key`; ☐ future: OS keyring (Windows Credential Manager), never files in repo |
| Unauthorised order submission | money loss | ✔ every order through risk engine + idempotency key; ☐ live: separate process with its own config and human authorisation record |
| Malicious / compromised dependency | code execution | ✔ minimal deps (numpy, pandas, scipy, pyyaml); ✔ version ranges in `pyproject.toml`; ☐ `pip-audit` in CI; ☐ hash-pinned lock file for the operator machine |
| Corrupted / manipulated historical data | wrong research conclusions | ✔ validation checks; ✔ dataset version hash recorded with every result |
| Tampering with experiment history (hiding failures) | overfitting | ✔ hash-chained registry, `omega registry` verifies |
| Stale prices / clock drift | trading on old data | ✔ stale-data risk rule; ☐ NTP check in the daily job |
| Duplicate orders / ambiguous broker responses | double exposure | ✔ deterministic keys, write-ahead SQLite, UNKNOWN state, reconciliation, halt on position break |
| Process crash mid-submission | unknown exposure | ✔ write-ahead + restart reconciliation (`test_crash_after_write_ahead_is_recovered_by_reconciliation`) |
| Database corruption / disk full | lost state | ☐ SQLite `PRAGMA integrity_check` at start-up; ☐ daily backup copy; positions can be rebuilt from broker |
| LLM-generated unsafe code | anything | ✔ tests + review; ✔ an LLM has no credentials and no live path; changes go through git |
| Prompt injection via research material | manipulated code/conclusions | ✔ external text is data; strategy changes require code review and recorded trials |
| YAML deserialisation | code execution | ✔ `yaml.safe_load` only |

Secrets policy: never commit secrets; keep them outside the repository; rotate if exposure is suspected.
