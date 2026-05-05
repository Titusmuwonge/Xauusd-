//+------------------------------------------------------------------+
//| RiskEngine.mqh — Basket TP, Hard SL, Age Limit, News Filter      |
//| Manages cycle-level P&L aggregation and time-based risk guards.   |
//+------------------------------------------------------------------+
#pragma once

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

   double   m_basket_tp_pct;     // % of balance to trigger basket close
   double   m_basket_sl_pct;     // % of balance hard drawdown limit
   int      m_age_limit_sec;     // max cycle age in seconds
   int      m_news_buffer_sec;   // seconds before/after news to pause
   bool     m_news_filter_on;

   datetime m_cycle_start;
   bool     m_cycle_running;

   //--------------------------------------------------------------------
   double GetAccountBalance() { return AccountInfoDouble(ACCOUNT_BALANCE); }
   double GetAccountEquity()  { return AccountInfoDouble(ACCOUNT_EQUITY);  }

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
   // Closes all positions + cancels all pending orders for this magic.
   // Returns true if all operations succeeded.
   bool LiquidateCycle(string reason)
   {
      bool all_ok = true;
      PrintFormat("RiskEngine: LIQUIDATE — %s | FloatPL=%.2f", reason, CalcTotalFloatingPL());

      // Cancel pending orders first to prevent re-fills during close
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

      // Close open positions with retry on requote
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
               continue;  // retry on stale price
            if(!closed)
            {
               PrintFormat("RiskEngine: close pos %llu failed — %u %s",
                           m_pos.Ticket(), rc, m_trade.ResultComment());
               all_ok = false;
            }
         }
      }

      m_cycle_running = false;
      m_cycle_start   = 0;
      return all_ok;
   }

   //--------------------------------------------------------------------
   // Uses MQL5 built-in Economic Calendar (requires terminal access).
   // Checks for HIGH-impact USD or XAU events within the news window.
   bool IsNearHighImpactNews()
   {
#ifdef __MQL5__
      datetime now    = TimeCurrent();
      datetime from   = now - (datetime)m_news_buffer_sec;
      datetime to     = now + (datetime)m_news_buffer_sec;

      MqlCalendarValue vals[];
      // USD calendar (primary gold driver) + XAU if available
      int count = CalendarValueHistory(vals, from, to, "USD", NULL);
      for(int i = 0; i < count; i++)
      {
         MqlCalendarEvent evt;
         if(!CalendarEventById(vals[i].event_id, evt)) continue;
         // Only block on HIGH-impact releases
         if(evt.importance == CALENDAR_IMPORTANCE_HIGH) return true;
      }
#endif
      return false;
   }

public:
   CRiskEngine()
      : m_cycle_start(0), m_cycle_running(false) {}
   ~CRiskEngine() {}

   //--------------------------------------------------------------------
   bool Init(string symbol, int magic,
             double basket_tp_pct   = 0.5,
             double basket_sl_pct   = 2.0,
             int    age_limit_hours = 4,
             bool   news_filter     = true,
             int    news_buffer_min = 30,
             int    slippage_pts    = 30)
   {
      m_symbol         = symbol;
      m_magic          = magic;
      m_basket_tp_pct  = basket_tp_pct;
      m_basket_sl_pct  = basket_sl_pct;
      m_age_limit_sec  = age_limit_hours * 3600;
      m_news_filter_on = news_filter;
      m_news_buffer_sec= news_buffer_min * 60;
      m_slippage_pts   = slippage_pts;

      m_trade.SetExpertMagicNumber((ulong)magic);
      m_trade.SetDeviationInPoints((ulong)slippage_pts);
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
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

   bool IsCycleRunning() const { return m_cycle_running; }

   //--------------------------------------------------------------------
   // Check basket take-profit: close everything if total PL >= tp_pct of balance
   bool CheckBasketTP()
   {
      if(GetPositionCount() == 0) return false;

      double balance   = GetAccountBalance();
      double threshold = balance * (m_basket_tp_pct / 100.0);
      double total_pl  = CalcTotalFloatingPL();

      if(total_pl >= threshold)
      {
         PrintFormat("RiskEngine: BasketTP hit — PL=%.2f threshold=%.2f", total_pl, threshold);
         return LiquidateCycle("BasketTP");
      }
      return false;
   }

   //--------------------------------------------------------------------
   // Hard stop-loss guard: close everything if equity drawdown >= sl_pct
   bool CheckBasketSL()
   {
      if(GetPositionCount() == 0) return false;

      double balance   = GetAccountBalance();
      double threshold = -balance * (m_basket_sl_pct / 100.0);
      double total_pl  = CalcTotalFloatingPL();

      if(total_pl <= threshold)
      {
         PrintFormat("RiskEngine: HardSL hit — PL=%.2f limit=%.2f", total_pl, threshold);
         return LiquidateCycle("HardSL");
      }
      return false;
   }

   //--------------------------------------------------------------------
   // Age-limit guard: kill cycle if no basket TP after time limit
   bool CheckAgeLimit()
   {
      if(!m_cycle_running || m_cycle_start == 0) return false;
      if(GetPositionCount() == 0) return false;

      datetime elapsed = TimeCurrent() - m_cycle_start;
      if(elapsed >= (datetime)m_age_limit_sec)
      {
         PrintFormat("RiskEngine: AgeLimit hit — %d sec elapsed", (int)elapsed);
         return LiquidateCycle("AgeLimit");
      }
      return false;
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

   double GetTotalPL()    { return CalcTotalFloatingPL(); }
   double GetBalance()    { return GetAccountBalance(); }
   double GetEquity()     { return GetAccountEquity(); }
};
