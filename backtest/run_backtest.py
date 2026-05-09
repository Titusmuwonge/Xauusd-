"""
Main entry point — downloads data, runs baseline + optimisation, plots results.
Usage:
    python run_backtest.py                        # quick run (yfinance 60d)
    python run_backtest.py --source dukascopy     # deep run (3y tick data)
    python run_backtest.py --optimise             # parameter sweep
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from itertools import product

from download_data import download
from bgc_backtest  import BGCBacktest, BacktestParams, BacktestResult

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "XAUUSD_M15.csv")
OUT_DIR   = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUT_DIR, exist_ok=True)


# ──────────────────────────────────────────────────────────────
def load_data(source: str = "yfinance") -> pd.DataFrame:
    if os.path.exists(DATA_FILE):
        print(f"Using cached data: {DATA_FILE}")
        df = pd.read_csv(DATA_FILE, index_col=0, parse_dates=True)
    else:
        df = download(source=source)
    df.index = pd.to_datetime(df.index)
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    print(f"Loaded {len(df)} bars  {df.index[0].date()} → {df.index[-1].date()}")
    return df


# ──────────────────────────────────────────────────────────────
def plot_results(result: BacktestResult, title: str = "BGC Grid Backtest"):
    eq     = result.equity_series()
    trades = result.trades_df()
    m      = result.metrics()

    fig = plt.figure(figsize=(16, 10))
    gs  = gridspec.GridSpec(3, 2, figure=fig)

    # 1 — Equity curve
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(eq.index, eq.values, color="#00d4aa", linewidth=1.2, label="Equity")
    ax1.axhline(result.p.initial_balance, color="gray", linestyle="--", linewidth=0.8)
    roll_max = eq.cummax()
    ax1.fill_between(eq.index, eq.values, roll_max.values, alpha=0.25, color="red", label="Drawdown")
    ax1.set_title(f"{title}   Net P&L: ${m['Net P&L']} ({m['Net P&L %']}%)  "
                  f"PF: {m['Profit Factor']}  Sharpe: {m['Sharpe Ratio']}")
    ax1.set_ylabel("Equity ($)")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)

    if not trades.empty:
        # 2 — P&L per trade
        ax2 = fig.add_subplot(gs[1, 0])
        colors = ["#00d4aa" if p > 0 else "#ff4d4d" for p in trades["pnl"]]
        ax2.bar(range(len(trades)), trades["pnl"], color=colors, width=0.8)
        ax2.axhline(0, color="white", linewidth=0.5)
        ax2.set_title("P&L per Trade")
        ax2.set_xlabel("Trade #")
        ax2.set_ylabel("P&L ($)")
        ax2.grid(alpha=0.3)

        # 3 — Exit reason breakdown
        ax3 = fig.add_subplot(gs[1, 1])
        exits = trades["exit_reason"].value_counts()
        colors_pie = ["#00d4aa" if "TP" in r or "Basket" in r else "#ff4d4d"
                      for r in exits.index]
        ax3.pie(exits.values, labels=exits.index, colors=colors_pie,
                autopct="%1.0f%%", startangle=90)
        ax3.set_title("Exit Reasons")

        # 4 — P&L distribution
        ax4 = fig.add_subplot(gs[2, 0])
        ax4.hist(trades["pnl"], bins=30, color="#4d9fff", edgecolor="none", alpha=0.8)
        ax4.axvline(0, color="white", linewidth=1)
        ax4.axvline(trades["pnl"].mean(), color="yellow", linewidth=1.5,
                    label=f"Mean: ${trades['pnl'].mean():.2f}")
        ax4.set_title("P&L Distribution")
        ax4.set_xlabel("P&L ($)")
        ax4.legend()
        ax4.grid(alpha=0.3)

        # 5 — Rolling win rate (50-trade window)
        ax5 = fig.add_subplot(gs[2, 1])
        win_roll = (trades["pnl"] > 0).rolling(50, min_periods=10).mean() * 100
        ax5.plot(win_roll.values, color="#ffd700", linewidth=1.2)
        ax5.axhline(50, color="gray", linestyle="--", linewidth=0.8)
        ax5.set_title("Rolling Win Rate (50-trade window)")
        ax5.set_xlabel("Trade #")
        ax5.set_ylabel("Win Rate %")
        ax5.set_ylim(0, 100)
        ax5.grid(alpha=0.3)

    plt.style.use("dark_background")
    fig.patch.set_facecolor("#1a1a2e")
    for ax in fig.axes:
        ax.set_facecolor("#16213e")
    plt.tight_layout()
    out = os.path.join(OUT_DIR, "backtest_result.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Chart saved → {out}")
    plt.show()


# ──────────────────────────────────────────────────────────────
def run_optimisation(df: pd.DataFrame):
    """Grid-search key parameters. Returns sorted results dataframe."""
    print("\n" + "═"*55)
    print("  PARAMETER OPTIMISATION")
    print("═"*55)

    param_grid = {
        "cusum_threshold": [3.0, 4.0, 5.0, 6.0],
        "dev_sigma":       [1.5, 2.0, 2.5, 3.0],
        "grid_atr_mult":   [0.3, 0.5, 0.7, 1.0],
        "max_levels":      [2, 3, 4],
        "basket_tp_pct":   [0.3, 0.5, 0.75, 1.0],
        "tp_atr_mult":     [0.5, 1.0, 1.5],
    }

    keys   = list(param_grid.keys())
    combos = list(product(*param_grid.values()))
    print(f"Testing {len(combos)} parameter combinations...")

    results = []
    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        p = BacktestParams(**params)
        bt = BGCBacktest(p)
        res = bt.run(df.copy())
        m = res.metrics()

        if "error" in m or m["Total Trades"] < 20:
            continue

        # Custom fitness — same as OnTester() in MQL5
        pf       = m["Profit Factor"]
        wr       = m["Win Rate %"] / 100
        dd       = abs(m["Max Drawdown %"]) / 100
        net      = m["Net P&L"]
        if dd >= 0.15 or net <= 0:
            score = 0
        else:
            score = pf * wr * (1 - dd) * np.log(max(1, net))

        results.append({**params,
                        "score":          round(score, 4),
                        "net_pnl":        round(net, 2),
                        "profit_factor":  round(pf, 3),
                        "win_rate":       round(m["Win Rate %"], 1),
                        "max_dd_pct":     round(m["Max Drawdown %"], 2),
                        "sharpe":         round(m["Sharpe Ratio"], 3),
                        "trades":         m["Total Trades"]})

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(combos)} done...", end="\r")

    if not results:
        print("No valid results found")
        return

    df_res = pd.DataFrame(results).sort_values("score", ascending=False)
    out_csv = os.path.join(OUT_DIR, "optimisation_results.csv")
    df_res.to_csv(out_csv, index=False)
    print(f"\nTop 10 parameter sets:")
    print(df_res.head(10).to_string(index=False))
    print(f"\nFull results saved → {out_csv}")

    # Run and plot the top result
    best = df_res.iloc[0]
    print(f"\nRunning detailed backtest on best params...")
    best_p = BacktestParams(
        cusum_threshold=best["cusum_threshold"],
        dev_sigma=best["dev_sigma"],
        grid_atr_mult=best["grid_atr_mult"],
        max_levels=int(best["max_levels"]),
        basket_tp_pct=best["basket_tp_pct"],
        tp_atr_mult=best["tp_atr_mult"],
    )
    bt_best = BGCBacktest(best_p)
    res_best = bt_best.run(df.copy())
    res_best.print_metrics()
    plot_results(res_best, title=f"BGC Grid — Optimised Params")


# ──────────────────────────────────────────────────────────────
def analyse_live_trades():
    """Analyse the actual live trades from the MT5 screenshot."""
    print("\n" + "═"*55)
    print("  LIVE TRADE ANALYSIS (from MT5 history)")
    print("═"*55)

    trades = pd.DataFrame([
        {"type":"sell","entry":4762.29,"exit":4750.10,"sl":4786.67,"tp":4750.10,"pnl": 12.19,"reason":"TP"},
        {"type":"sell","entry":4691.65,"exit":4691.62,"sl":4714.28,"tp":4680.34,"pnl":  0.03,"reason":"TP"},
        {"type":"buy", "entry":4701.85,"exit":4711.99,"sl":4681.58,"tp":4711.99,"pnl": 10.14,"reason":"TP"},
        {"type":"sell","entry":4711.81,"exit":4727.62,"sl":4727.62,"tp":4701.27,"pnl":-15.81,"reason":"SL"},
        {"type":"sell","entry":4706.55,"exit":4706.73,"sl":4722.36,"tp":4696.01,"pnl": -0.18,"reason":"SL"},
        {"type":"sell","entry":4708.43,"exit":4723.68,"sl":4723.68,"tp":4700.81,"pnl":-15.25,"reason":"SL"},
        {"type":"sell","entry":4712.23,"exit":4727.48,"sl":4727.48,"tp":4704.61,"pnl":-15.25,"reason":"SL"},
        {"type":"sell","entry":4716.05,"exit":4731.30,"sl":4731.30,"tp":4708.43,"pnl":-15.25,"reason":"SL"},
        {"type":"sell","entry":4719.85,"exit":4735.10,"sl":4735.10,"tp":4712.23,"pnl":-15.25,"reason":"SL"},
        {"type":"buy", "entry":4743.94,"exit":4727.47,"sl":4727.47,"tp":4754.92,"pnl":-16.47,"reason":"SL"},
        {"type":"buy", "entry":4724.16,"exit":4703.75,"sl":4703.75,"tp":4737.77,"pnl":-20.41,"reason":"SL"},
    ])

    wins   = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] < 0]

    print(f"  Total trades:        {len(trades)}")
    print(f"  Winners:             {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
    print(f"  Losers:              {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
    print(f"  Net P&L:             ${trades['pnl'].sum():.2f}")
    print(f"  Avg win:             ${wins['pnl'].mean():.2f}")
    print(f"  Avg loss:            ${losses['pnl'].mean():.2f}")
    pf = wins['pnl'].sum() / abs(losses['pnl'].sum())
    print(f"  Profit factor:       {pf:.3f}")
    print(f"  Gross wins:          ${wins['pnl'].sum():.2f}")
    print(f"  Gross losses:        ${losses['pnl'].sum():.2f}")
    print()
    print("  DIAGNOSIS:")
    cluster = trades[(trades["type"]=="sell") &
                     (trades["entry"].between(4706,4720)) &
                     (trades["reason"]=="SL")]
    print(f"  Sell cluster (15:10–15:33 May 8): {len(cluster)} trades, "
          f"${cluster['pnl'].sum():.2f} loss")
    print("  Root cause: MGT placed 4 counter-trend sells into a rising market.")
    print("  CUSUM threshold too high — trend not detected until too late.")
    print()
    print("  RECOMMENDED PARAMETER CHANGES:")
    print("  InpCUSUMThreshold:  4.0 → 2.5  (detect trends earlier)")
    print("  InpMaxLevels:       4   → 2    (limit simultaneous exposure)")
    print("  InpGridATRMult:     0.5 → 0.7  (wider spacing = fewer fills in noise)")
    print("  InpBasketSLPct:     2.0 → 1.5  (tighter cycle stop)")


# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source",   default="yfinance",
                        choices=["yfinance","dukascopy","alphavantage"])
    parser.add_argument("--optimise", action="store_true")
    parser.add_argument("--live",     action="store_true",
                        help="Analyse the MT5 live trades from the screenshot")
    args = parser.parse_args()

    if args.live:
        analyse_live_trades()
        sys.exit(0)

    df = load_data(args.source)

    if args.optimise:
        run_optimisation(df)
    else:
        print("\nRunning baseline backtest with default parameters...")
        p   = BacktestParams()
        bt  = BGCBacktest(p)
        res = bt.run(df)
        res.print_metrics()
        trades_out = os.path.join(OUT_DIR, "trades.csv")
        res.trades_df().to_csv(trades_out, index=False)
        print(f"Trades saved → {trades_out}")
        plot_results(res)
