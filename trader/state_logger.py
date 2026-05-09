"""
StateLogger — writes JSON snapshots of market state and trade executions.
Files are stored in trader/logs/ with daily rotation.
Paste the state file content into a Claude Code session for offline analysis.
"""

import os
import json
from datetime import datetime


class StateLogger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = os.path.join(os.path.dirname(__file__), log_dir)
        os.makedirs(self.log_dir, exist_ok=True)

    def _today(self) -> str:
        return datetime.now().strftime("%Y%m%d")

    def _append(self, file_path: str, entry: dict):
        entries = []
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    entries = json.load(f)
            except (json.JSONDecodeError, IOError):
                entries = []
        entries.append(entry)
        with open(file_path, "w") as f:
            json.dump(entries, f, indent=2, default=str)

    def log_state(self, context: dict, decision: dict):
        """Log every bar's full context + decision for post-session review."""
        entry = {
            "ts":       datetime.now().isoformat(),
            "context":  context,
            "decision": decision,
        }
        path = os.path.join(self.log_dir, f"state_{self._today()}.json")
        self._append(path, entry)

    def log_execution(self, result: dict, decision: dict, context: dict):
        """Log every order placement or position close with account snapshot."""
        entry = {
            "ts":       datetime.now().isoformat(),
            "action":   decision.get("action"),
            "direction": decision.get("direction"),
            "reason":   decision.get("reason"),
            "result":   result,
            "equity":   context.get("account", {}).get("equity", 0),
            "balance":  context.get("account", {}).get("balance", 0),
            "regime":   context.get("signals", {}).get("regime", 0),
            "atr":      context.get("signals", {}).get("atr", 0),
            "cusum_pos": context.get("signals", {}).get("cusum_pos", 0),
            "cusum_neg": context.get("signals", {}).get("cusum_neg", 0),
        }
        path = os.path.join(self.log_dir, f"trades_{self._today()}.json")
        self._append(path, entry)

    def log_error(self, error: str, context: dict = None):
        entry = {
            "ts":      datetime.now().isoformat(),
            "error":   error,
            "equity":  context.get("account", {}).get("equity", 0) if context else None,
        }
        path = os.path.join(self.log_dir, f"errors_{self._today()}.json")
        self._append(path, entry)
