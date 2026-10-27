# Deployment (Windows, local-first)

```powershell
# 1. Python 3.11+ from python.org (tick "Add to PATH"), then:
git clone https://github.com/ciaphuscain/trading.git
cd trading
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
# Recommended (Python 3.13): exact, hash-verified versions that CI tests on Windows
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
# Alternative (any Python 3.11+): latest compatible versions
# python -m pip install -e ".[dev]"
python -m pytest -q                   # must pass before any research run
python -m omega costs --value 2500
```

No Docker, database server, cloud account or GPU is required. Data, results and the registry live under
`data_store/`, `results/` and `runs/` (git-ignored). Back them up (e.g. copy to an external drive) — the
registry is the evidence base for every conclusion.

Scheduled operation (Phase 5, not yet built): one Task Scheduler task after market close running the daily
paper job with the venv's `python.exe`, working directory = repo, logging to `runs/`.
