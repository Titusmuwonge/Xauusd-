"""
AWB SetupTrader — Backtest Report Generator
Produces console stats + equity curve chart.
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"


def generate_report(trades_df: pd.DataFrame, equity_curve: list):
    OUTPUT_DIR.mkdir(exist_ok=True)

    if trades_df.empty:
        print("No trades generated — check strategy parameters or data range.")
        return

    wins    = trades_df[trades_df["result"] == "win"]
    losses  = trades_df[trades_df["result"] == "loss"]
    timeouts= trades_df[trades_df["result"] == "timeout"]

    total   = len(trades_df)
    n_win   = len(wins)
    n_loss  = len(losses)
    win_rate= n_win / total * 100

    gross_profit = wins["pnl"].sum()
    gross_loss   = abs(losses["pnl"].sum())
    net_pnl      = trades_df["pnl"].sum()
    profit_factor= gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win  = wins["pnl"].mean()   if n_win  > 0 else 0
    avg_loss = losses["pnl"].mean() if n_loss > 0 else 0
    avg_rr   = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    # Drawdown
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd   = (eq - peak) / peak * 100
    max_dd = dd.min()

    # Monthly P&L
    if "entry_time" in trades_df.columns:
        trades_df["month"] = pd.to_datetime(trades_df["entry_time"]).dt.to_period("M")
        monthly = trades_df.groupby("month")["pnl"].sum()
    else:
        monthly = pd.Series(dtype=float)

    # ---- Console report ----
    sep = "─" * 52
    print(f"\n{sep}")
    print("  AWB SetupTrader — Backtest Results")
    print(sep)
    print(f"  Total Trades   : {total}")
    print(f"  Wins / Losses  : {n_win} / {n_loss}  (timeouts: {len(timeouts)})")
    print(f"  Win Rate       : {win_rate:.1f}%")
    print(f"  Net P&L        : ${net_pnl:,.2f}")
    print(f"  Gross Profit   : ${gross_profit:,.2f}")
    print(f"  Gross Loss     : ${gross_loss:,.2f}")
    print(f"  Profit Factor  : {profit_factor:.2f}")
    print(f"  Avg Win        : ${avg_win:.2f}")
    print(f"  Avg Loss       : ${avg_loss:.2f}")
    print(f"  Avg R:R        : {avg_rr:.2f}")
    print(f"  Max Drawdown   : {max_dd:.2f}%")
    print(sep)

    if not monthly.empty:
        print("  Monthly P&L:")
        for period, pnl in monthly.items():
            bar = "█" * int(abs(pnl) / 50) if abs(pnl) > 0 else ""
            sign = "+" if pnl >= 0 else ""
            print(f"    {period}  {sign}${pnl:,.2f}  {bar}")
        print(sep)

    # ---- Chart ----
    fig = plt.figure(figsize=(14, 9), facecolor="#1a1a2e")
    gs  = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 1], hspace=0.35)

    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])
    ax3 = fig.add_subplot(gs[2])

    for ax in [ax1, ax2, ax3]:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="#aaaaaa", labelsize=8)
        ax.spines["bottom"].set_color("#444")
        ax.spines["left"].set_color("#444")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # Equity curve
    ax1.plot(eq, color="#00d4ff", linewidth=1.5, label="Equity")
    ax1.fill_between(range(len(eq)), eq, eq[0], alpha=0.1, color="#00d4ff")
    ax1.axhline(eq[0], color="#666", linewidth=0.7, linestyle="--")
    ax1.set_title("AWB SetupTrader — Equity Curve", color="white", fontsize=13, pad=10)
    ax1.set_ylabel("Account Balance ($)", color="#aaaaaa", fontsize=9)
    ax1.legend(fontsize=8, facecolor="#1a1a2e", labelcolor="white", framealpha=0.5)

    # Drawdown
    ax2.fill_between(range(len(dd)), dd, 0, color="#ff4757", alpha=0.7)
    ax2.set_ylabel("Drawdown (%)", color="#aaaaaa", fontsize=9)
    ax2.set_title("Drawdown", color="white", fontsize=10, pad=6)

    # Monthly bars
    if not monthly.empty:
        colors = ["#2ed573" if v >= 0 else "#ff4757" for v in monthly.values]
        ax3.bar(range(len(monthly)), monthly.values, color=colors, width=0.6)
        ax3.set_xticks(range(len(monthly)))
        ax3.set_xticklabels([str(p) for p in monthly.index], rotation=45, ha="right",
                            fontsize=7, color="#aaaaaa")
        ax3.set_title("Monthly P&L ($)", color="white", fontsize=10, pad=6)
        ax3.axhline(0, color="#666", linewidth=0.7)

    # Stats box on equity chart
    stats_txt = (
        f"Trades: {total}  |  Win Rate: {win_rate:.1f}%  |  "
        f"PF: {profit_factor:.2f}  |  Max DD: {max_dd:.1f}%  |  "
        f"Net P&L: ${net_pnl:,.0f}"
    )
    fig.text(0.5, 0.97, stats_txt, ha="center", va="top",
             color="#eeeeee", fontsize=9,
             bbox=dict(boxstyle="round,pad=0.3", facecolor="#0f3460", edgecolor="#444"))

    out_path = OUTPUT_DIR / "backtest_results.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"\n  Chart saved → {out_path}")

    # Save trade log
    csv_path = OUTPUT_DIR / "trades.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  Trade log saved → {csv_path}\n")
