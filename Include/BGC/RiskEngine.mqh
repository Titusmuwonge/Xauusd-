//+------------------------------------------------------------------+
//| RiskEngine.mqh — Basket TP, Hard SL, Age Limit, News Filter      |
//+------------------------------------------------------------------+
#ifndef BGC_RISK_ENGINE_MQH
#define BGC_RISK_ENGINE_MQH

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

class CRiskEngine
{
private:
   CTrade        m_trade;
   CPositionInfo m_pos;
   COrderInfo    m_ord;

   string m_symbol;
   int    m_magic;
   int    m_slippage_pts;
   int    m_digits;

   double   m_basket_tp_pct;
   double   m_basket_sl_pct;
   int      m_age_limit_sec;
   int      m_news_buffer_sec;
   bool     m_news_filter_on;
   bool     m_news_warn_logged;

   datetime m_cycle_start;
   bool     m_cycle_running;

   //--------------------------------------------------------------------
   double GetAccountEquity() { return AccountInfoDouble(ACCOUNT_EQUITY); }

   //--------------------------------------------------------------------
   double CalcTotalFloatingPL()
   {
      double total = 0.0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!m_pos.SelectByIndex(i)) continue;
         if(m_pos.Symbol() != m_symbol || m_pos.Magic() != (ulong)m_magic) continue;
         total += m_pos.Profit() + m_pos.Swap() + m_pos.Commission();
      }
      return total;
   }

   //--------------------------------------------------------------------
   bool LiquidateCycle(string reason)
   {
      bool all_ok = true;
      PrintFormat("RiskEngine: LIQUIDATE [%s] | FloatPL=%.2f", reason, CalcTotalFloatingPL());

      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         if(!m_ord.SelectByIndex(i)) continue;
         if(m_ord.Symbol() != m_symbol || m_ord.Magic() != (ulong)m_magic) continue;
         if(!m_trade.OrderDelete(m_ord.Ticket()))
         {
            PrintFormat("RiskEngine: delete order %llu failed — %u %s",
                        m_ord.Ticket(), m_trade.ResultRetcode(), m_trade.ResultComment());
            all_ok = false;
         }
      }

      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!m_pos.SelectByIndex(i)) continue;
         if(m_pos.Symbol() != m_symbol || m_pos.Magic() != (ulong)m_magic) continue;

         bool closed = false;
         for(int attempt = 0; attempt < 3 && !closed; attempt++)
         {
            closed = m_trade.PositionClose(m_pos.Ticket(), m_slippage_pts);
            uint rc = m_trade.ResultRetcode();
            if(!closed && (rc == TRADE_RETCODE_REQUOTE || rc == TRADE_RETCODE_PRICE_CHANGED))
               continue;
            if(!closed)
            {
               PrintFormat("RiskEngine: close pos %llu attempt %d failed — %u %s",
                           m_pos.Ticket(), attempt + 1, rc, m_trade.ResultComment());
               all_ok = false;
            }
         }
      }

      m_cycle_running = false;
      m_cycle_start   = 0;
      return all_ok;
   }

   //--------------------------------------------------------------------
   bool IsNearHighImpactNews()
   {
      datetime now  = TimeCurrent();
      datetime from = now - (datetime)m_news_buffer_sec;
      datetime to   = now + (datetime)m_news_buffer_sec;

      MqlCalendarValue vals[];
      int count = CalendarValueHistory(vals, from, to, "USD", NULL);
      if(count < 0)
      {
         if(!m_news_warn_logged)
         {
            Print("RiskEngine: CalendarValueHistory unavailable — news filter disabled");
            m_news_warn_logged = true;
         }
         return false;
      }
      for(int i = 0; i < count; i++)
      {
         MqlCalendarEvent evt;
         if(!CalendarEventById(vals[i].event_id, evt)) continue;
         if(evt.importance == CALENDAR_IMPORTANCE_HIGH) return true;
      }
      return false;
   }

public:
   CRiskEngine() : m_cycle_start(0), m_cycle_running(false), m_news_warn_logged(false) {}
   ~CRiskEngine() {}

   //--------------------------------------------------------------------
   bool Init(string symbol, int magic,
             double basket_tp_pct   = 1.5,
             double basket_sl_pct   = 2.0,
             int    age_limit_hours = 4,
             bool   news_filter     = true,
             int    news_buffer_min = 30,
             int    slippage_pts    = 30)
   {
      m_symbol          = symbol;
      m_magic           = magic;
      m_basket_tp_pct   = basket_tp_pct;
      m_basket_sl_pct   = basket_sl_pct;
      m_age_limit_sec   = age_limit_hours * 3600;
      m_news_filter_on  = news_filter;
      m_news_buffer_sec = news_buffer_min * 60;
      m_slippage_pts    = slippage_pts;
      m_digits          = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

      m_trade.SetExpertMagicNumber((ulong)magic);
      m_trade.SetDeviationInPoints((ulong)slippage_pts);

      int fill_mode = (int)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
      if(fill_mode & SYMBOL_FILLING_RETURN)
         m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
      else if(fill_mode & SYMBOL_FILLING_IOC)
         m_trade.SetTypeFilling(ORDER_FILLING_IOC);
      else
         m_trade.SetTypeFilling(ORDER_FILLING_FOK);

      m_trade.LogLevel(LOG_LEVEL_ERRORS);
      return true;
   }

   //--------------------------------------------------------------------
   void StartCycle()
   {
      m_cycle_start   = TimeCurrent();
      m_cycle_running = true;
   }

   void StopCycle()
   {
      m_cycle_running = false;
      m_cycle_start   = 0;
   }

   bool IsCycleRunning() { return m_cycle_running; }

   //--------------------------------------------------------------------
   bool CheckBasketTP()
   {
      if(GetPositionCount() == 0) return false;
      double equity    = GetAccountEquity();
      double threshold = equity * (m_basket_tp_pct / 100.0);
      double total_pl  = CalcTotalFloatingPL();
      if(total_pl >= threshold)
      {
         PrintFormat("RiskEngine: BasketTP hit — PL=%.2f threshold=%.2f", total_pl, threshold);
         return LiquidateCycle("BasketTP");
      }
      return false;
   }

   //--------------------------------------------------------------------
   bool CheckBasketSL()
   {
      if(GetPositionCount() == 0) return false;
      double equity    = GetAccountEquity();
      double threshold = -equity * (m_basket_sl_pct / 100.0);
      double total_pl  = CalcTotalFloatingPL();
      if(total_pl <= threshold)
      {
         PrintFormat("RiskEngine: HardSL hit — PL=%.2f limit=%.2f", total_pl, threshold);
         return LiquidateCycle("HardSL");
      }
      return false;
   }

   //--------------------------------------------------------------------
   bool CheckAgeLimit()
   {
      if(!m_cycle_running || m_cycle_start == 0) return false;
      if(GetPositionCount() == 0 && GetPendingOrderCount() == 0) return false;
      datetime elapsed = TimeCurrent() - m_cycle_start;
      if(elapsed >= (datetime)m_age_limit_sec)
      {
         PrintFormat("RiskEngine: AgeLimit hit — %d sec elapsed | pos=%d pend=%d",
                     (int)elapsed, GetPositionCount(), GetPendingOrderCount());
         return LiquidateCycle("AgeLimit");
      }
      return false;
   }

   //--------------------------------------------------------------------
   // Move SL to break-even once price has moved break_even_atr_mult × ATR in our favour.
   bool CheckBreakEven(double atr, double break_even_atr_mult)
   {
      if(atr <= 0.0 || break_even_atr_mult <= 0.0) return false;
      double bid      = SymbolInfoDouble(m_symbol, SYMBOL_BID);
      double ask      = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      double tick     = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tick <= 0.0) tick = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double threshold = atr * break_even_atr_mult;
      bool any = false;

      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!m_pos.SelectByIndex(i)) continue;
         if(m_pos.Symbol() != m_symbol || m_pos.Magic() != (ulong)m_magic) continue;

         double entry      = m_pos.PriceOpen();
         double current_sl = m_pos.StopLoss();

         if(m_pos.PositionType() == POSITION_TYPE_BUY)
         {
            double be_sl = NormalizeDouble(entry + tick, m_digits);
            if(bid - entry >= threshold && (current_sl == 0.0 || current_sl < be_sl))
            {
               if(m_trade.PositionModify(m_pos.Ticket(), be_sl, m_pos.TakeProfit()))
                  any = true;
            }
         }
         else
         {
            double be_sl = NormalizeDouble(entry - tick, m_digits);
            if(entry - ask >= threshold && (current_sl == 0.0 || current_sl > be_sl))
            {
               if(m_trade.PositionModify(m_pos.Ticket(), be_sl, m_pos.TakeProfit()))
                  any = true;
            }
         }
      }
      return any;
   }

   //--------------------------------------------------------------------
   // Trail SL at trail_atr_mult × ATR behind current price once profit ≥ 1×ATR.
   bool CheckTrailingStop(double atr, double trail_atr_mult)
   {
      if(atr <= 0.0 || trail_atr_mult <= 0.0) return false;
      double bid       = SymbolInfoDouble(m_symbol, SYMBOL_BID);
      double ask       = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      double trail_gap = atr * trail_atr_mult;
      bool any = false;

      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!m_pos.SelectByIndex(i)) continue;
         if(m_pos.Symbol() != m_symbol || m_pos.Magic() != (ulong)m_magic) continue;

         double entry      = m_pos.PriceOpen();
         double current_sl = m_pos.StopLoss();

         if(m_pos.PositionType() == POSITION_TYPE_BUY)
         {
            if(bid - entry >= atr)  // profit threshold: 1×ATR before trailing begins
            {
               double new_sl = NormalizeDouble(bid - trail_gap, m_digits);
               if(new_sl > current_sl)
               {
                  if(m_trade.PositionModify(m_pos.Ticket(), new_sl, m_pos.TakeProfit()))
                     any = true;
               }
            }
         }
         else
         {
            if(entry - ask >= atr)  // profit threshold: 1×ATR before trailing begins
            {
               double new_sl = NormalizeDouble(ask + trail_gap, m_digits);
               if(current_sl == 0.0 || new_sl < current_sl)
               {
                  if(m_trade.PositionModify(m_pos.Ticket(), new_sl, m_pos.TakeProfit()))
                     any = true;
               }
            }
         }
      }
      return any;
   }

   //--------------------------------------------------------------------
   bool IsNewsFilterActive()
   {
      if(!m_news_filter_on) return false;
      return IsNearHighImpactNews();
   }

   //--------------------------------------------------------------------
   int GetPositionCount()
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

   double GetTotalPL()  { return CalcTotalFloatingPL(); }
   double GetEquity()   { return GetAccountEquity();    }
};

#endif // BGC_RISK_ENGINE_MQH
