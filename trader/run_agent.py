"""
BGC Grid Autonomous Agent — 24/7 trading daemon.

Fires every M15 bar close:
  1. Pulls live market data + positions from MT5
  2. Runs CUSUM/ATR/EMA indicators via signal_engine
  3. Calls Claude API for a trading decision
  4. Executes orders via MT5Bridge

Usage:
    python run_agent.py             # live trading
    python run_agent.py --dry-run   # print decisions, execute nothing
    python run_agent.py --dry-run --once  # single decision then exit (safe test)
"""

import os
import sys
import time
import argparse
import traceback
from datetime import datetime

import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))

from mt5_bridge   import MT5Bridge
from signal_engine import get_context
from claude_brain  import make_decision
from executor      import Executor
from state_logger  import StateLogger
from bgc_backtest  import BacktestParams

# ── Configuration ────────────────────────────────────────────────
SYMBOL          = os.environ.get("BGC_SYMBOL",  "XAUUSD")
MAGIC           = int(os.environ.get("BGC_MAGIC",   "20260505"))
RISK_PCT        = float(os.environ.get("BGC_RISK",  "1.0"))
MAX_LEVELS      = int(os.environ.get("BGC_LEVELS",  "4"))
GRID_ATR_MULT   = float(os.environ.get("BGC_GRID",  "0.7"))
TP_ATR_MULT     = float(os.environ.get("BGC_TP",    "1.0"))

POLL_SEC        = 30   # seconds between bar checks
QUIET_INTERVAL  = 4    # call Claude every N bars when idle (no positions, not trending)
MAX_ERRORS      = 5    # consecutive errors before 5-min pause
# ─────────────────────────────────────────────────────────────────


def is_asian_session() -> bool:
    hour = datetime.utcnow().hour
    return hour >= 22 or hour < 1


def status_line(context: dict, action: str, reason: str) -> str:
    signals  = context.get("signals", {})
    account  = context.get("account", {})
    positions = context.get("positions", [])
    regime_map = {0: "RANGING", 1: "TGT_UP", -1: "TGT_DN"}
    regime = regime_map.get(signals.get("regime", 0), "?")
    return (
        f"[{datetime.now():%H:%M}] {regime:8s} | "
        f"Eq=${account.get('equity', 0):,.0f} | "
        f"Pos={len(positions)} | "
        f"CS+={signals.get('cusum_pos', 0):.1f} "
        f"CS-={signals.get('cusum_neg', 0):.1f} | "
        f"ATR={signals.get('atr', 0):.2f} | "
        f"{action.upper():15s} | {reason[:60]}"
    )


def main(dry_run: bool = False, once: bool = False):
    print(f"[{datetime.now():%H:%M}] BGC Autonomous Agent — {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"  Symbol={SYMBOL}  Magic={MAGIC}  Risk={RISK_PCT}%  Levels={MAX_LEVELS}")

    bridge = MT5Bridge(symbol=SYMBOL)
    try:
        bridge.connect()
    except RuntimeError as e:
        print(f"FATAL: Cannot connect to MT5 — {e}")
        print("  Ensure MT5 is running, logged in, and AutoTrading is enabled.")
        sys.exit(1)

    logger   = StateLogger(log_dir="logs")
    client   = anthropic.Anthropic()
    params   = BacktestParams(cusum_threshold=2.5, dev_sigma=2.0, grid_atr_mult=GRID_ATR_MULT)
    executor = Executor(
        bridge=bridge, magic=MAGIC,
        risk_pct=RISK_PCT, max_levels=MAX_LEVELS,
        grid_atr_mult=GRID_ATR_MULT, tp_atr_mult=TP_ATR_MULT,
        logger=logger,
    )

    last_bar_time    = None
    bars_since_call  = 0
    consecutive_errs = 0

    while True:
        try:
            import MetaTrader5 as mt5
            df = bridge.get_ohlcv(SYMBOL, mt5.TIMEFRAME_M15, 10)
            if df is None or df.empty:
                time.sleep(POLL_SEC)
                continue

            current_bar = df.index[-1]
            if last_bar_time is not None and current_bar == last_bar_time:
                time.sleep(POLL_SEC)
                continue

            last_bar_time    = current_bar
            bars_since_call += 1
            consecutive_errs = 0

            context   = get_context(bridge, magic=MAGIC, params=params)
            signals   = context.get("signals", {})
            account   = context.get("account", {})
            positions = context.get("positions", [])

            has_positions = len(positions) > 0
            is_trending   = signals.get("regime", 0) != 0
            is_extended   = signals.get("ext_dir", 0) != 0
            asian         = is_asian_session()

            # Decide whether to call Claude this bar
            should_call = (
                has_positions
                or is_trending
                or (is_extended and not asian)
                or bars_since_call >= QUIET_INTERVAL
            )

            if not should_call:
                regime_map = {0: "RANGING", 1: "TGT_UP", -1: "TGT_DN"}
                print(f"[{datetime.now():%H:%M}] {regime_map.get(signals.get('regime',0)):8s} | "
                      f"Eq=${account.get('equity', 0):,.0f} | idle ({bars_since_call}/{QUIET_INTERVAL})")
                if once:
                    break
                continue

            bars_since_call = 0

            decision = make_decision(context, client=client)
            action   = decision.get("action", "hold")
            reason   = decision.get("reason", "")

            print(status_line(context, action, reason))
            logger.log_state(context, decision)

            if not dry_run:
                result = executor.execute(decision, context)
                if result.get("orders_placed"):
                    for o in result["orders_placed"]:
                        print(f"  → Placed {o['type']} @ {o['price']:.2f}  lot={o['lot']:.2f}")
                if result.get("positions_closed"):
                    print(f"  → Closed {len(result['positions_closed'])} positions")
                if result.get("errors"):
                    for e in result["errors"]:
                        print(f"  ! {e}")
            else:
                print(f"  [DRY RUN] Would execute: {action}")

            if once:
                break

        except KeyboardInterrupt:
            print(f"\n[{datetime.now():%H:%M}] Shutting down — Ctrl+C received")
            bridge.disconnect()
            sys.exit(0)

        except Exception as exc:
            consecutive_errs += 1
            msg = f"ERROR #{consecutive_errs}: {exc}"
            print(f"\n[{datetime.now():%H:%M}] {msg}")
            traceback.print_exc()
            try:
                logger.log_error(msg)
            except Exception:
                pass
            if consecutive_errs >= MAX_ERRORS:
                print(f"  {MAX_ERRORS} consecutive errors — pausing 5 minutes")
                time.sleep(300)
                consecutive_errs = 0
            else:
                time.sleep(POLL_SEC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BGC Grid Autonomous Trading Agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print Claude decisions without placing any orders")
    parser.add_argument("--once",    action="store_true",
                        help="Process one bar then exit (use with --dry-run for safe testing)")
    args = parser.parse_args()
    main(dry_run=args.dry_run, once=args.once)
