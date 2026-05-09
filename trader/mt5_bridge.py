"""
MT5Bridge — wraps MetaTrader5 Python package for safe order management.
Requires MT5 terminal running on the same Windows machine.
"""

import time
import pandas as pd


class MT5Bridge:
    def __init__(self, symbol: str = "XAUUSD"):
        self.symbol = symbol

    def connect(self, login: int = None, password: str = None,
                server: str = None, path: str = None) -> bool:
        import MetaTrader5 as mt5
        kwargs = {}
        if path:     kwargs["path"]     = path
        if login:    kwargs["login"]    = login
        if password: kwargs["password"] = password
        if server:   kwargs["server"]   = server

        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(self.symbol, True):
            raise RuntimeError(f"symbol_select({self.symbol}) failed: {mt5.last_error()}")
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("No account info — is MT5 logged in?")
        print(f"Connected: {info.name} @ {info.server} | Balance: {info.balance:.2f} {info.currency}")
        return True

    def disconnect(self):
        import MetaTrader5 as mt5
        mt5.shutdown()

    # ── Market data ─────────────────────────────────────────────

    def get_account(self) -> dict:
        import MetaTrader5 as mt5
        info = mt5.account_info()
        if info is None:
            return {}
        return {
            "balance":     info.balance,
            "equity":      info.equity,
            "margin":      info.margin,
            "free_margin": info.margin_free,
            "profit":      info.profit,
            "currency":    info.currency,
            "leverage":    info.leverage,
        }

    def get_ohlcv(self, symbol: str, timeframe, n_bars: int = 150) -> pd.DataFrame:
        import MetaTrader5 as mt5
        for attempt in range(2):
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n_bars)
            if rates is not None and len(rates) > 0:
                break
            time.sleep(1)
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    def get_symbol_info(self) -> dict:
        import MetaTrader5 as mt5
        info = mt5.symbol_info(self.symbol)
        tick = mt5.symbol_info_tick(self.symbol)
        if info is None:
            return {}
        return {
            "bid":           tick.bid if tick else 0,
            "ask":           tick.ask if tick else 0,
            "point":         info.point,
            "digits":        info.digits,
            "contract_size": info.trade_contract_size,
            "volume_min":    info.volume_min,
            "volume_max":    info.volume_max,
            "volume_step":   info.volume_step,
            "spread":        info.spread,
        }

    # ── Positions & Orders ──────────────────────────────────────

    def get_positions(self, magic: int = None) -> list:
        import MetaTrader5 as mt5
        positions = mt5.positions_get(symbol=self.symbol)
        if positions is None:
            return []
        result = []
        for p in positions:
            if magic is not None and p.magic != magic:
                continue
            result.append({
                "ticket":        p.ticket,
                "type":          "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume":        p.volume,
                "price_open":    p.price_open,
                "price_current": p.price_current,
                "sl":            p.sl,
                "tp":            p.tp,
                "profit":        p.profit,
                "swap":          p.swap,
                "time":          pd.Timestamp(p.time, unit="s"),
                "magic":         p.magic,
                "comment":       p.comment,
            })
        return result

    def get_pending_orders(self, magic: int = None) -> list:
        import MetaTrader5 as mt5
        orders = mt5.orders_get(symbol=self.symbol)
        if orders is None:
            return []
        result = []
        for o in orders:
            if magic is not None and o.magic != magic:
                continue
            result.append({
                "ticket": o.ticket,
                "type":   str(o.type),
                "volume": o.volume_current,
                "price":  o.price_open,
                "sl":     o.sl,
                "tp":     o.tp,
                "time":   pd.Timestamp(o.time_setup, unit="s"),
                "magic":  o.magic,
            })
        return result

    def get_closed_deals(self, magic: int = None, count: int = 10) -> list:
        import MetaTrader5 as mt5
        from_ts = int((pd.Timestamp.now() - pd.Timedelta(days=30)).timestamp())
        to_ts   = int(pd.Timestamp.now().timestamp())
        deals = mt5.history_deals_get(from_ts, to_ts, group=f"*{self.symbol}*")
        if deals is None:
            return []
        result = []
        for d in deals:
            if magic is not None and d.magic != magic:
                continue
            if d.entry == mt5.DEAL_ENTRY_OUT:
                result.append({
                    "ticket": d.ticket,
                    "type":   "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL",
                    "volume": d.volume,
                    "price":  d.price,
                    "profit": d.profit,
                    "time":   pd.Timestamp(d.time, unit="s"),
                    "comment": d.comment,
                })
        return result[-count:] if len(result) > count else result

    # ── Order execution ─────────────────────────────────────────

    def place_order(self, order_type, price: float, sl: float, tp: float,
                    lot: float, magic: int, comment: str = "BGC") -> tuple:
        import MetaTrader5 as mt5
        request = {
            "action":      mt5.TRADE_ACTION_PENDING,
            "symbol":      self.symbol,
            "volume":      lot,
            "type":        order_type,
            "price":       price,
            "sl":          sl,
            "tp":          tp,
            "magic":       magic,
            "comment":     comment,
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else -1
            msg  = result.comment if result else str(mt5.last_error())
            return False, f"retcode={code} {msg}"
        return True, result.order

    def close_position(self, ticket: int, lot: float = None) -> tuple:
        import MetaTrader5 as mt5
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False, "Position not found"
        pos = positions[0]
        vol = lot or pos.volume
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(self.symbol)
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        request = {
            "action":      mt5.TRADE_ACTION_DEAL,
            "symbol":      self.symbol,
            "volume":      vol,
            "type":        order_type,
            "position":    ticket,
            "price":       price,
            "deviation":   30,
            "magic":       pos.magic,
            "comment":     "BGC_close",
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else -1
            msg  = result.comment if result else str(mt5.last_error())
            return False, f"retcode={code} {msg}"
        return True, result.deal

    def cancel_order(self, ticket: int) -> tuple:
        import MetaTrader5 as mt5
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order":  ticket,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else -1
            msg  = result.comment if result else str(mt5.last_error())
            return False, f"retcode={code} {msg}"
        return True, None
