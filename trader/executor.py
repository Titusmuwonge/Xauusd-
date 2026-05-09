"""
Executor — translates Claude's decision dict into MT5 orders via MT5Bridge.
Handles dynamic lot calculation, order placement, position closing, and logging.
"""

import math


def calc_lot(equity: float, risk_pct: float, atr: float, contract_size: float,
             volume_min: float, volume_max: float, volume_step: float) -> float:
    """Compute position size: risk_pct % of equity / (1×ATR SL value per lot)."""
    risk_amount = equity * (risk_pct / 100.0)
    sl_per_lot  = atr * contract_size  # $ loss per lot if SL = 1×ATR
    if sl_per_lot <= 0:
        return volume_min
    lot = risk_amount / sl_per_lot
    lot = max(volume_min, min(volume_max, lot))
    lot = math.floor(lot / volume_step) * volume_step
    lot = round(lot, 8)
    return max(volume_min, lot)


class Executor:
    def __init__(self, bridge, magic: int = 20260505,
                 risk_pct: float = 1.0, max_levels: int = 4,
                 grid_atr_mult: float = 0.7, tp_atr_mult: float = 1.0,
                 logger=None):
        self.bridge        = bridge
        self.magic         = magic
        self.risk_pct      = risk_pct
        self.max_levels    = max_levels
        self.grid_atr_mult = grid_atr_mult
        self.tp_atr_mult   = tp_atr_mult
        self.logger        = logger

    def execute(self, decision: dict, context: dict) -> dict:
        import MetaTrader5 as mt5

        action    = decision.get("action", "hold")
        direction = decision.get("direction", 0)
        override  = decision.get("override_params", {})

        signals  = context.get("signals", {})
        account  = context.get("account", {})
        sym_info = context.get("symbol_info", {})

        atr      = signals.get("atr", 0)
        bid      = context.get("bid", 0)
        ask      = context.get("ask", 0)
        mid      = (bid + ask) / 2.0
        equity   = account.get("equity", 0)
        ext_dir  = signals.get("ext_dir", 0)

        contract_size = sym_info.get("contract_size", 100.0)
        vol_min       = sym_info.get("volume_min",   0.01)
        vol_max       = sym_info.get("volume_max",   100.0)
        vol_step      = sym_info.get("volume_step",  0.01)

        risk_pct   = override.get("risk_pct",   self.risk_pct)
        max_levels = override.get("max_levels", self.max_levels)

        lot     = calc_lot(equity, risk_pct, atr, contract_size, vol_min, vol_max, vol_step)
        spacing = atr * self.grid_atr_mult

        result = {
            "action":           action,
            "orders_placed":    [],
            "positions_closed": [],
            "orders_cancelled": 0,
            "errors":           [],
        }

        if action == "hold":
            pass  # nothing to do

        elif action == "place_mgt":
            levels = min(max_levels, 4)
            tp_dist = atr * self.tp_atr_mult

            for i in range(1, levels + 1):
                if ext_dir > 0:  # price above EMA — sell limits (mean reversion down)
                    price = round(mid + i * spacing, sym_info.get("digits", 2))
                    sl    = round(price + atr * 1.0, sym_info.get("digits", 2))
                    tp    = round(price - tp_dist, sym_info.get("digits", 2)) if tp_dist else 0
                    ok, detail = self.bridge.place_order(
                        mt5.ORDER_TYPE_SELL_LIMIT, price, sl, tp, lot, self.magic
                    )
                    if ok:
                        result["orders_placed"].append({"type": "SELL_LIMIT", "price": price, "lot": lot})
                    else:
                        result["errors"].append(f"SELL_LIMIT@{price}: {detail}")

                elif ext_dir < 0:  # price below EMA — buy limits (mean reversion up)
                    price = round(mid - i * spacing, sym_info.get("digits", 2))
                    sl    = round(price - atr * 1.0, sym_info.get("digits", 2))
                    tp    = round(price + tp_dist, sym_info.get("digits", 2)) if tp_dist else 0
                    ok, detail = self.bridge.place_order(
                        mt5.ORDER_TYPE_BUY_LIMIT, price, sl, tp, lot, self.magic
                    )
                    if ok:
                        result["orders_placed"].append({"type": "BUY_LIMIT", "price": price, "lot": lot})
                    else:
                        result["errors"].append(f"BUY_LIMIT@{price}: {detail}")

        elif action == "place_tgt":
            levels  = min(max_levels, 3)
            tp_dist = atr * 1.5  # TGT always 1:1 RR — 1.5×ATR TP to match 1.5×ATR SL

            for i in range(1, levels + 1):
                if direction > 0:
                    price = round(mid + i * spacing, sym_info.get("digits", 2))
                    sl    = round(price - atr * 1.5,  sym_info.get("digits", 2))
                    tp    = round(price + tp_dist,     sym_info.get("digits", 2))
                    ok, detail = self.bridge.place_order(
                        mt5.ORDER_TYPE_BUY_STOP, price, sl, tp, lot, self.magic
                    )
                    if ok:
                        result["orders_placed"].append({"type": "BUY_STOP", "price": price, "lot": lot})
                    else:
                        result["errors"].append(f"BUY_STOP@{price}: {detail}")

                elif direction < 0:
                    price = round(mid - i * spacing, sym_info.get("digits", 2))
                    sl    = round(price + atr * 1.5,  sym_info.get("digits", 2))
                    tp    = round(price - tp_dist,     sym_info.get("digits", 2))
                    ok, detail = self.bridge.place_order(
                        mt5.ORDER_TYPE_SELL_STOP, price, sl, tp, lot, self.magic
                    )
                    if ok:
                        result["orders_placed"].append({"type": "SELL_STOP", "price": price, "lot": lot})
                    else:
                        result["errors"].append(f"SELL_STOP@{price}: {detail}")

        elif action == "close_all":
            for pos in context.get("positions", []):
                ok, detail = self.bridge.close_position(pos["ticket"])
                if ok:
                    result["positions_closed"].append(pos["ticket"])
                else:
                    result["errors"].append(f"close {pos['ticket']}: {detail}")
            for order in context.get("pending_orders", []):
                self.bridge.cancel_order(order["ticket"])
            result["orders_cancelled"] = len(context.get("pending_orders", []))

        elif action == "cancel_pending":
            for order in context.get("pending_orders", []):
                ok, detail = self.bridge.cancel_order(order["ticket"])
                if ok:
                    result["orders_cancelled"] += 1
                else:
                    result["errors"].append(f"cancel {order['ticket']}: {detail}")

        if self.logger and (result["orders_placed"] or result["positions_closed"]):
            self.logger.log_execution(result, decision, context)

        return result
