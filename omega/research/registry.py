"""Append-only, hash-chained experiment registry.

Every backtest trial, holdout access and promotion decision is appended as one JSON
line whose hash covers the previous record's hash. Deleting or editing a failed
experiment breaks the chain, which ``verify`` detects. The registry is the source of
the trial counts used by the Deflated Sharpe Ratio: concealed trials cannot be
"un-counted" without leaving evidence.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

GENESIS = "0" * 64


def code_version() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                             cwd=Path(__file__).resolve().parent).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5,
                               cwd=Path(__file__).resolve().parent).stdout.strip()
        return f"{rev}{'-dirty' if dirty else ''}" if rev else "unknown"
    except Exception:  # noqa: BLE001 - git may be absent on the operator's machine
        return "unknown"


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


class Registry:
    def __init__(self, path: str | Path | None):
        """path=None keeps records in memory only (unit tests, throwaway runs)."""
        self.path = Path(path) if path else None
        self._mem: list[dict] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------- write
    def _append(self, rec: dict) -> dict:
        records = self.records()
        prev = records[-1]["hash"] if records else GENESIS
        rec = {"id": uuid.uuid4().hex[:12], "ts": pd.Timestamp.now(tz="UTC").isoformat(), **rec, "prev_hash": prev}
        body = json.dumps(rec, sort_keys=True, default=_default)
        rec["hash"] = hashlib.sha256(body.encode()).hexdigest()
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, sort_keys=True, default=_default) + "\n")
        else:
            self._mem.append(json.loads(json.dumps(rec, sort_keys=True, default=_default)))
        return rec

    def record_trial(self, *, family: str, strategy: str, params: dict, data_version: str, cost_version: str,
                     metrics: dict, period_sr: float, hypothesis: str = "", seed: int | None = None,
                     stage: str = "research", notes: str = "") -> dict:
        return self._append({"kind": "trial", "family": family, "strategy": strategy, "params": params,
                             "data_version": data_version, "cost_version": cost_version, "code_version": code_version(),
                             "metrics": metrics, "period_sr": period_sr, "hypothesis": hypothesis, "seed": seed,
                             "stage": stage, "notes": notes})

    def record_holdout_access(self, *, family: str, reason: str, data_version: str) -> dict:
        return self._append({"kind": "holdout_access", "family": family, "reason": reason, "data_version": data_version})

    def record_decision(self, *, family: str, decision: str, reasons: list[str], evidence: dict) -> dict:
        return self._append({"kind": "decision", "family": family, "decision": decision, "reasons": reasons,
                             "evidence": evidence, "code_version": code_version()})

    # --------------------------------------------------------------------- read
    def records(self) -> list[dict]:
        if not self.path:
            return list(self._mem)
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def trials(self, family: str | None = None) -> list[dict]:
        return [r for r in self.records() if r["kind"] == "trial" and (family is None or r["family"] == family)]

    def n_trials(self, family: str | None = None) -> int:
        return len(self.trials(family))

    def var_trial_sr(self, family: str | None = None) -> float:
        srs = [t["period_sr"] for t in self.trials(family) if t.get("period_sr") is not None]
        return float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0

    def holdout_accesses(self, family: str | None = None) -> list[dict]:
        return [r for r in self.records() if r["kind"] == "holdout_access" and (family is None or r["family"] == family)]

    def verify(self) -> bool:
        prev = GENESIS
        for rec in self.records():
            r = dict(rec)
            h = r.pop("hash")
            if r["prev_hash"] != prev:
                return False
            if hashlib.sha256(json.dumps(r, sort_keys=True, default=_default).encode()).hexdigest() != h:
                return False
            prev = h
        return True
