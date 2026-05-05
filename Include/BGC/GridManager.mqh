//+------------------------------------------------------------------+
//| GridManager.mqh — ATR-spaced Bi-Directional Grid Order Engine    |
//| MGT mode: counter-trend limit orders (mean reversion)            |
//| TGT mode: trend-following stop orders after CUSUM breakout        |
//+------------------------------------------------------------------+
#pragma once

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

class CGridManager
{
private:
   CTrade         m_trade;
   CPositionInfo  m_pos;
   COrderInfo     m_ord;

   string m_symbol;
   int    m_magic;
   int    m_digits;
   double m_point;

   double m_lot_size;
   int    m_max_levels;
   double m_atr_mult;       // grid spacing = ATR * m_atr_mult
   double m_tp_atr_mult;    // individual order TP (0 = basket only)
   int    m_slippage_pts;

   bool   m_grid_active;
   double m_grid_origin;
   double m_grid_spacing;

   //--------------------------------------------------------------------
   double NormalisePrice(double price)
   {
      double tick = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tick <= 0.0) tick = m_point;
      return MathRound(price / tick) * tick;
   }

   //--------------------------------------------------------------------
   bool TradeContextOK()
   {
      if(!MQLInfoInteger(MQL_TRADE_ALLOWED))          { Print("GridManager: trade not allowed (MQL)");     return false; }
      if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))   { Print("GridManager: account trade disabled");      return false; }
      if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))    { Print("GridManager: EA trading disabled");         return false; }
      if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) { Print("GridManager: terminal trade not allowed");  return false; }
      return true;
   }

   //--------------------------------------------------------------------
   bool OrderAtLevel(double price)
   {
      double tol = m_grid_spacing * 0.3;
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         if(!m_ord.SelectByIndex(i)) continue;
         if(m_ord.Symbol() != m_symbol || m_ord.Magic() != (ulong)m_magic) continue;
         if(MathAbs(m_ord.PriceOpen() - price) < tol) return true;
      }
      return false;
   }

   //--------------------------------------------------------------------
   bool PositionAtLevel(double price)
   {
      double tol = m_grid_spacing * 0.3;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!m_pos.SelectByIndex(i)) continue;
         if(m_pos.Symbol() != m_symbol || m_pos.Magic() != (ulong)m_magic) continue;
         if(MathAbs(m_pos.PriceOpen() - price) < tol) return true;
      }
      return false;
   }

   //--------------------------------------------------------------------
   bool PlaceOrder(ENUM_ORDER_TYPE type, double price, double tp, double sl)
   {
      if(!TradeContextOK()) return false;

      price = NormalisePrice(price);
      tp    = (tp > 0.0) ? NormalisePrice(tp) : 0.0;
      sl    = (sl > 0.0) ? NormalisePrice(sl) : 0.0;

      bool ok = false;
      switch(type)
      {
         case ORDER_TYPE_BUY_LIMIT:  ok = m_trade.BuyLimit (m_lot_size, price, m_symbol, sl, tp); break;
         case ORDER_TYPE_SELL_LIMIT: ok = m_trade.SellLimit(m_lot_size, price, m_symbol, sl, tp); break;
         case ORDER_TYPE_BUY_STOP:   ok = m_trade.BuyStop  (m_lot_size, price, m_symbol, sl, tp); break;
         case ORDER_TYPE_SELL_STOP:  ok = m_trade.SellStop (m_lot_size, price, m_symbol, sl, tp); break;
         default: return false;
      }

      if(!ok || m_trade.ResultRetcode() != TRADE_RETCODE_DONE)
      {
         PrintFormat("GridManager: order %s @ %.5f failed — retcode %u (%s)",
                     EnumToString(type), price,
                     m_trade.ResultRetcode(), m_trade.ResultComment());
         return false;
      }
      return true;
   }

public:
   CGridManager()
      : m_grid_active(false), m_grid_origin(0.0), m_grid_spacing(0.0) {}
   ~CGridManager() {}

   //--------------------------------------------------------------------
   bool Init(string symbol, int magic, double lot_size,
             int max_levels, double atr_mult, double tp_atr_mult,
             int slippage_pts = 30)
   {
      m_symbol       = symbol;
      m_magic        = magic;
      m_lot_size     = lot_size;
      m_max_levels   = MathMax(1, max_levels);
      m_atr_mult     = atr_mult;
      m_tp_atr_mult  = tp_atr_mult;
      m_slippage_pts = slippage_pts;
      m_digits       = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      m_point        = SymbolInfoDouble(symbol, SYMBOL_POINT);

      m_trade.SetExpertMagicNumber((ulong)magic);
      m_trade.SetDeviationInPoints((ulong)slippage_pts);
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
      m_trade.LogLevel(LOG_LEVEL_ERRORS);

      return true;
   }

   //--------------------------------------------------------------------
   // MGT mode: drop counter-trend limit orders around EMA
   // buy limits below, sell limits above, spaced by ATR*mult
   void ManageMGTGrid(double bid, double ask, double ema, double atr)
   {
      if(!TradeContextOK()) return;

      m_grid_spacing = NormalisePrice(atr * m_atr_mult);
      if(m_grid_spacing <= 0.0) return;

      double mid     = (bid + ask) * 0.5;
      double tp_dist = (m_tp_atr_mult > 0.0) ? atr * m_tp_atr_mult : 0.0;

      for(int i = 1; i <= m_max_levels; i++)
      {
         // Sell limits above mid (mean-reversion from above)
         double sell_price = NormalisePrice(mid + i * m_grid_spacing);
         double sell_sl    = NormalisePrice(sell_price + atr * 2.0);
         double sell_tp    = (tp_dist > 0.0) ? NormalisePrice(sell_price - tp_dist) : 0.0;

         if(!OrderAtLevel(sell_price) && !PositionAtLevel(sell_price))
            PlaceOrder(ORDER_TYPE_SELL_LIMIT, sell_price, sell_tp, sell_sl);

         // Buy limits below mid (mean-reversion from below)
         double buy_price = NormalisePrice(mid - i * m_grid_spacing);
         double buy_sl    = NormalisePrice(buy_price - atr * 2.0);
         double buy_tp    = (tp_dist > 0.0) ? NormalisePrice(buy_price + tp_dist) : 0.0;

         if(!OrderAtLevel(buy_price) && !PositionAtLevel(buy_price))
            PlaceOrder(ORDER_TYPE_BUY_LIMIT, buy_price, buy_tp, buy_sl);
      }

      m_grid_active = true;
   }

   //--------------------------------------------------------------------
   // TGT mode: trend-following stop orders in breakout direction
   // direction: +1 = long breakout, -1 = short breakout
   void ManageTGTGrid(double bid, double ask, double atr, int direction)
   {
      if(!TradeContextOK()) return;
      if(direction == 0) return;

      m_grid_spacing = NormalisePrice(atr * m_atr_mult);
      if(m_grid_spacing <= 0.0) return;

      double mid     = (bid + ask) * 0.5;
      double tp_dist = (m_tp_atr_mult > 0.0) ? atr * m_tp_atr_mult : 0.0;

      // Only place stop orders in the trend direction — reduce overexposure
      int levels_to_place = MathMin(m_max_levels, 3);

      for(int i = 1; i <= levels_to_place; i++)
      {
         if(direction > 0)
         {
            double price = NormalisePrice(mid + i * m_grid_spacing);
            double sl    = NormalisePrice(price - atr * 1.5);
            double tp    = (tp_dist > 0.0) ? NormalisePrice(price + tp_dist) : 0.0;
            if(!OrderAtLevel(price)) PlaceOrder(ORDER_TYPE_BUY_STOP, price, tp, sl);
         }
         else
         {
            double price = NormalisePrice(mid - i * m_grid_spacing);
            double sl    = NormalisePrice(price + atr * 1.5);
            double tp    = (tp_dist > 0.0) ? NormalisePrice(price - tp_dist) : 0.0;
            if(!OrderAtLevel(price)) PlaceOrder(ORDER_TYPE_SELL_STOP, price, tp, sl);
         }
      }

      m_grid_active = true;
   }

   //--------------------------------------------------------------------
   void CancelAllPendingOrders()
   {
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         if(!m_ord.SelectByIndex(i)) continue;
         if(m_ord.Symbol() != m_symbol || m_ord.Magic() != (ulong)m_magic) continue;
         if(!m_trade.OrderDelete(m_ord.Ticket()))
            PrintFormat("GridManager: delete order %llu failed — %s",
                        m_ord.Ticket(), m_trade.ResultComment());
      }
   }

   //--------------------------------------------------------------------
   void CloseAllPositions()
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!m_pos.SelectByIndex(i)) continue;
         if(m_pos.Symbol() != m_symbol || m_pos.Magic() != (ulong)m_magic) continue;
         if(!m_trade.PositionClose(m_pos.Ticket(), m_slippage_pts))
            PrintFormat("GridManager: close position %llu failed — %s",
                        m_pos.Ticket(), m_trade.ResultComment());
      }
   }

   //--------------------------------------------------------------------
   int GetOpenPositionCount()
   {
      int cnt = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!m_pos.SelectByIndex(i)) continue;
         if(m_pos.Symbol() == m_symbol && m_pos.Magic() == (ulong)m_magic) cnt++;
      }
      return cnt;
   }

   int GetPendingOrderCount()
   {
      int cnt = 0;
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         if(!m_ord.SelectByIndex(i)) continue;
         if(m_ord.Symbol() == m_symbol && m_ord.Magic() == (ulong)m_magic) cnt++;
      }
      return cnt;
   }

   bool HasActiveGrid() const  { return m_grid_active; }
   void SetActive(bool active) { m_grid_active = active; }
   void Deactivate()           { m_grid_active = false; }
};
