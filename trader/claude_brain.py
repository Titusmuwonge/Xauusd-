"""
ClaudeBrain — calls Claude API with a structured market briefing and returns a
trading decision via tool_use (guarantees parseable JSON output).

Claude acts as regime supervisor and risk governor, NOT as a tick-by-tick calculator.
The signal engine handles all indicator math; Claude handles judgment calls.
"""

import anthropic
import pandas as pd

SYSTEM_PROMPT = """You are a quantitative trading risk supervisor for a bi-directional grid EA on XAUUSD (Gold).

Your role is NARROW and SPECIFIC:
1. Confirm or override the CUSUM regime classification based on full context
2. Decide whether current conditions warrant trading (time of day, loss streaks, news risk)
3. Reduce position sizing when equity is in significant drawdown (>5%)
4. Call close_all when you detect dangerous accumulation (many losing positions, regime flip)
5. Return hold when conditions are ambiguous — never force a trade

STRATEGY RULES:
- MGT mode (RANGING + price extended): Mean reversion grid
  ext_dir=+1 → price above EMA → SELL limits only (reversion back down)
  ext_dir=-1 → price below EMA → BUY limits only (reversion back up)
- TGT mode (TRENDING): Trend-following stop orders in the trend direction
- NEVER trade during Asian session (22:00–01:00 server time) unless closing positions
- NEVER add new grid if 5+ consecutive recent losses — wait for regime to reset

ACTIONS:
- hold:           No action needed; manage existing positions passively
- place_mgt:      Place mean-reversion grid (RANGING regime, ext_dir != 0)
- place_tgt:      Place trend-following grid (TRENDING regime detected)
- close_all:      Emergency close — dangerous exposure or regime flip with losses
- cancel_pending: Cancel stale pending orders (wrong-directioned for current regime)

OVERRIDE PARAMS (optional, only include when non-default is warranted):
- max_levels: reduce from default 4 when in drawdown
- risk_pct:   reduce from default 1.0% when equity DD > 5%

Keep your reason to 1 sentence. Be decisive — ambiguity defaults to hold."""

DECISION_TOOL = {
    "name": "trading_decision",
    "description": "Submit a trading decision based on the market briefing",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["hold", "place_mgt", "place_tgt", "close_all", "cancel_pending"],
                "description": "The trading action to take"
            },
            "direction": {
                "type": "integer",
                "enum": [1, -1, 0],
                "description": "1=bullish/long, -1=bearish/short, 0=neutral"
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the decision"
            },
            "override_params": {
                "type": "object",
                "description": "Optional parameter overrides when risk needs adjustment",
                "properties": {
                    "max_levels": {"type": "integer", "minimum": 1, "maximum": 4},
                    "risk_pct":   {"type": "number",  "minimum": 0.1, "maximum": 2.0}
                },
                "additionalProperties": False
            }
        },
        "required": ["action", "direction", "reason"]
    }
}


def _build_briefing(context: dict) -> str:
    signals   = context.get("signals", {})
    account   = context.get("account", {})
    positions = context.get("positions", [])
    pending   = context.get("pending_orders", [])
    closed    = context.get("recent_closed", [])
    bars      = context.get("recent_bars", [])

    regime_map = {0: "RANGING", 1: "TRENDING_UP", -1: "TRENDING_DOWN"}
    ext_map    = {1: "+1 (ABOVE EMA — sell signal)", -1: "-1 (BELOW EMA — buy signal)", 0: "0 (normal)"}

    # Positions summary
    if positions:
        now = pd.Timestamp.now()
        pos_lines = []
        for p in positions:
            age_min = int((now - p["time"]).total_seconds() / 60)
            pos_lines.append(
                f"  {p['type']:4s} {p['volume']:.2f}lot @ {p['price_open']:.2f} | "
                f"P&L: ${p['profit']:.2f} | SL: {p['sl']:.2f} | Age: {age_min}min"
            )
        pos_text = "\n".join(pos_lines)
    else:
        pos_text = "  None"

    # Pending orders summary
    if pending:
        pend_text = "\n".join(f"  {o['type']} @ {o['price']:.2f}" for o in pending)
    else:
        pend_text = "  None"

    # Win/loss streak
    win_streak = loss_streak = 0
    for deal in reversed(closed):
        if deal["profit"] > 0:
            if loss_streak > 0: break
            win_streak += 1
        else:
            if win_streak > 0: break
            loss_streak += 1
    if win_streak > 0:
        streak_text = f"{win_streak} consecutive wins"
    elif loss_streak > 0:
        streak_text = f"{loss_streak} consecutive losses"
    else:
        streak_text = "no recent trades"

    bars_text = " | ".join(
        f"O:{b['open']} H:{b['high']} L:{b['low']} C:{b['close']}" for b in bars
    )

    return f"""MARKET BRIEFING — {context.get('timestamp', 'now')}
Symbol: {context.get('symbol', 'XAUUSD')} | Bid: {context.get('bid', 0):.2f} / Ask: {context.get('ask', 0):.2f}

REGIME SIGNALS:
  Classification : {regime_map.get(signals.get('regime', 0), '?')}
  CUSUM+  / CUSUM- : {signals.get('cusum_pos', 0):.2f} / {signals.get('cusum_neg', 0):.2f}  (threshold: 2.5)
  Z-score / ext_dir: {signals.get('z_score', 0):.2f} / {ext_map.get(signals.get('ext_dir', 0), '?')}
  ATR: {signals.get('atr', 0):.2f} | EMA: {signals.get('ema', 0):.2f} | StdDev: {signals.get('std', 0):.4f}

ACCOUNT:
  Equity: ${account.get('equity', 0):.2f} | Balance: ${account.get('balance', 0):.2f}
  Drawdown from peak: {context.get('dd_pct', 0):.1f}%
  Open P&L: ${account.get('profit', 0):.2f}

OPEN POSITIONS ({len(positions)}):
{pos_text}

PENDING ORDERS ({len(pending)}):
{pend_text}

RECENT TRADE HISTORY:
  Last {len(closed)} closed deals: {streak_text}

RECENT 5 M15 BARS (oldest → newest):
  {bars_text}

Decide now."""


def make_decision(context: dict, client: anthropic.Anthropic = None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    briefing = _build_briefing(context)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        tools=[DECISION_TOOL],
        tool_choice={"type": "tool", "name": "trading_decision"},
        messages=[{"role": "user", "content": briefing}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "trading_decision":
            return block.input

    return {"action": "hold", "direction": 0, "reason": "Claude response parse failed — defaulting to hold"}
