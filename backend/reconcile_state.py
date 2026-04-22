"""CLI for local orchestrator state diagnostics and reconciliation.

Usage:
  python3 -m backend.reconcile_state
  python3 -m backend.reconcile_state --apply
"""

from __future__ import annotations

import argparse
import json

from backend.services.reconcile import StateReconciler


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose and reconcile stale local orchestrator state")
    parser.add_argument("--apply", action="store_true", help="Apply safe deterministic repairs")
    args = parser.parse_args()

    reconciler = StateReconciler()
    report = reconciler.run(apply=args.apply)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
