"""Freeze the detector before the held-out test run.

    python -m eval.freeze           # record the current rules fingerprint in rules/FROZEN.json
    python -m eval.freeze --check   # exit 1 if the rules changed since freezing

The fingerprint hashes the catalogue, lexicons, features, engine and calendar. Building or
evaluating the test split requires a matching freeze, so the test set is never used for tuning.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from rules.engine import Scorecard, rules_fingerprint

FROZEN_PATH = Path(__file__).resolve().parents[1] / "rules" / "FROZEN.json"


def frozen_record() -> dict | None:
    if not FROZEN_PATH.exists():
        return None
    return json.loads(FROZEN_PATH.read_text(encoding="utf-8"))


def is_frozen() -> bool:
    rec = frozen_record()
    return bool(rec and rec["fingerprint"] == rules_fingerprint())


def _git_head() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=FROZEN_PATH.parents[1], check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        rec = frozen_record()
        ok = is_frozen()
        print(f"frozen={rec and rec['version']} fingerprint={rec and rec['fingerprint']} current={rules_fingerprint()} ok={ok}")
        sys.exit(0 if ok else 1)
    sc = Scorecard()
    rec = {"version": sc.version, "fingerprint": sc.fingerprint, "frozen_at": datetime.now().isoformat(timespec="seconds"),
           "git_head": _git_head()}
    FROZEN_PATH.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    print(f"frozen rules {sc.version} fingerprint {sc.fingerprint} -> {FROZEN_PATH}")
    print(f"commit it and tag it:  git add rules/FROZEN.json && git commit -m 'Freeze rules {sc.version}' "
          f"&& git tag rules-{sc.version}")


if __name__ == "__main__":
    main()
