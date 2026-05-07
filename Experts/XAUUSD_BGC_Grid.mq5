//+------------------------------------------------------------------+
//| XAUUSD_BGC_Grid.mq5                                              |
//| Bi-Directional Grid Constrained (BGC) Expert Advisor             |
//| XAU/USD · MQL5 · MT5 Strategy Tester Compatible                  |
//+------------------------------------------------------------------+
#property copyright   "BGC Grid EA — XAU/USD"
#property version     "2.01"
#property description "Regime-Aware Mean-Reversion / Trend Grid for Gold"
#property strict

#include <BGC\RegimeDetector.mqh>
#include <BGC\GridManager.mqh>
#include <BGC\RiskEngine.mqh>

//+------------------------------------------------------------------+
//|  Input Parameters                                                 |
//+------------------------------------------------------------------+

input string          InpSymbol         = "";           // Symbol (blank = chart symbol)
input ENUM_TIMEFRAMES InpTF             = PERIOD_M15;   // Working timeframe

input int    InpEMAPeriod      = 20;     // EMA period
input int    InpATRPeriod      = 14;     // ATR period
input double InpCUSUMThreshold = 4.0;   // CUSUM breakout threshold
input double InpCUSUMAllowance = 0.5;   // CUSUM allowance k
input double InpDevSigma       = 2.0;   // Std-dev extension trigger

input double InpLotSize        = 0.01;  // Lot size per grid level
input int    InpMaxLevels      = 4;     // Max grid levels per side
input double InpGridATRMult    = 0.5;   // Grid spacing = ATR x this
input double InpTPATRMult      = 1.0;   // Per-order TP (0 = basket only)
input int    InpSlippagePts    = 30;    // Max slippage in points

input double InpBasketTPPct    = 0.50;  // Basket TP as % of balance
input double InpBasketSLPct    = 2.00;  // Hard SL as % of balance
input int    InpAgeLimitHours  = 4;     // Max cycle age (hours)

input bool   InpNewsFilter     = true;  // Enable high-impact news filter
input int    InpNewsBufferMin  = 30;    // Minutes before/after news event

input int    InpMagic          = 20260505; // EA magic number

//+------------------------------------------------------------------+
//|  Globals                                                           |
//+------------------------------------------------------------------+
CRegimeDetector g_regime;
CGridManager    g_grid;
CRiskEngine     g_risk;

string   g_symbol;
bool     g_initialized    = false;
datetime g_last_bar_time  = 0;
datetime g_last_news_warn = 0;   // throttle for news-filter log message

//+------------------------------------------------------------------+
//|  Helpers                                                           |
//+------------------------------------------------------------------+
bool IsTradeContextReady()
{
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))          return false;
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))   return false;
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))    return false;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return false;
   return true;
}

bool IsNewBar()
{
   datetime bar_time = iTime(g_symbol, InpTF, 0);
   if(bar_time == 0 || bar_time == g_last_bar_time) return false;
   g_last_bar_time = bar_time;
   return true;
}

//+------------------------------------------------------------------+
//|  OnInit                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   g_symbol = (InpSymbol == "" || InpSymbol == NULL) ? _Symbol : InpSymbol;

   // Warn if symbol doesn't look like gold
   string sym_upper = g_symbol;
   StringToUpper(sym_upper);
   if(StringFind(sym_upper, "XAU") < 0 && StringFind(sym_upper, "GOL") < 0)
      PrintFormat("BGC Grid: WARNING — symbol '%s' may not be XAU/USD.", g_symbol);

   if(InpLotSize <= 0.0)
   { Print("BGC Grid: InpLotSize must be > 0"); return INIT_PARAMETERS_INCORRECT; }
   if(InpMaxLevels < 1)
   { Print("BGC Grid: InpMaxLevels must be >= 1"); return INIT_PARAMETERS_INCORRECT; }
   if(InpBasketTPPct <= 0.0)
   { Print("BGC Grid: InpBasketTPPct must be > 0"); return INIT_PARAMETERS_INCORRECT; }

   double min_lot  = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double lot_step = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   if(InpLotSize < min_lot)
   {
      PrintFormat("BGC Grid: InpLotSize %.2f below broker min %.2f", InpLotSize, min_lot);
      return INIT_PARAMETERS_INCORRECT;
   }
   double adj_lot = MathRound(InpLotSize / lot_step) * lot_step;

   if(!g_regime.Init(g_symbol, InpTF, InpEMAPeriod, InpATRPeriod,
                     InpCUSUMThreshold, InpCUSUMAllowance, InpDevSigma))
   { Print("BGC Grid: RegimeDetector init failed"); return INIT_FAILED; }

   if(!g_grid.Init(g_symbol, InpMagic, adj_lot, InpMaxLevels,
                   InpGridATRMult, InpTPATRMult, InpSlippagePts))
   { Print("BGC Grid: GridManager init failed"); return INIT_FAILED; }

   if(!g_risk.Init(g_symbol, InpMagic, InpBasketTPPct, InpBasketSLPct,
                   InpAgeLimitHours, InpNewsFilter, InpNewsBufferMin, InpSlippagePts))
   { Print("BGC Grid: RiskEngine init failed"); return INIT_FAILED; }

   g_initialized   = true;
   g_last_bar_time = 0;
   g_last_news_warn= 0;

   PrintFormat("BGC Grid READY | Symbol=%s TF=%s Magic=%d Lot=%.2f Levels=%d",
               g_symbol, EnumToString(InpTF), InpMagic, adj_lot, InpMaxLevels);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//|  OnDeinit                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   g_regime.Deinit();
   PrintFormat("BGC Grid DEINIT | reason=%d", reason);
}

//+------------------------------------------------------------------+
//|  OnTick                                                            |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_initialized) return;

   //--- 1. Trade-context guard
   if(!IsTradeContextReady()) return;

   //--- 2. News filter — block new orders, cancel unfilled pending
   if(g_risk.IsNewsFilterActive())
   {
      if(TimeCurrent() - g_last_news_warn > 300)
      {
         Print("BGC Grid: News filter active — new orders blocked");
         g_last_news_warn = TimeCurrent();
      }
      g_grid.CancelAllPendingOrders();
      return;
   }

   //--- 3. Risk checks — every tick for fast response
   bool cycle_closed = false;
   if(g_risk.CheckBasketTP()) cycle_closed = true;
   if(!cycle_closed && g_risk.CheckBasketSL()) cycle_closed = true;
   if(!cycle_closed && g_risk.CheckAgeLimit()) cycle_closed = true;

   if(cycle_closed)
   {
      g_grid.CancelAllPendingOrders();
      g_grid.Deactivate();
      g_regime.Reset();
      return;
   }

   //--- 4. New-bar gate for grid placement
   if(!IsNewBar()) return;

   //--- 5. Update regime
   if(!g_regime.Update()) return;

   double atr = g_regime.GetATR();
   double ema = g_regime.GetEMA();
   if(atr <= 0.0) return;

   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   if(bid <= 0.0 || ask <= 0.0) return;

   ENUM_MARKET_REGIME regime = g_regime.GetRegime();

   //--- 6. Regime-based grid management
   if(regime == REGIME_RANGING)
   {
      int ext_dir = 0;
      if(g_regime.IsPriceExtended((bid + ask) * 0.5, ext_dir))
      {
         if(!g_risk.IsCycleRunning())
         {
            g_risk.StartCycle();
            PrintFormat("BGC Grid: NEW CYCLE MGT | ATR=%.2f EMA=%.2f CS+/−=%.2f/%.2f",
                        atr, ema, g_regime.GetCUSUMPos(), g_regime.GetCUSUMNeg());
         }
         g_grid.ManageMGTGrid(bid, ask, ema, atr);
      }
   }
   else
   {
      int direction = (regime == REGIME_TRENDING_UP) ? 1 : -1;
      g_grid.CancelAllPendingOrders();

      if(!g_risk.IsCycleRunning())
      {
         g_risk.StartCycle();
         PrintFormat("BGC Grid: NEW CYCLE TGT | dir=%s ATR=%.2f CS+/−=%.2f/%.2f",
                     (direction > 0 ? "UP" : "DOWN"), atr,
                     g_regime.GetCUSUMPos(), g_regime.GetCUSUMNeg());
      }
      g_grid.ManageTGTGrid(bid, ask, atr, direction);
   }

   //--- 7. Cycle cleanup when all positions and orders are gone
   if(g_risk.GetPositionCount() == 0 && g_grid.GetPendingOrderCount() == 0)
   {
      if(g_risk.IsCycleRunning())
      {
         g_risk.StopCycle();
         g_grid.Deactivate();
         Print("BGC Grid: Cycle closed naturally — resetting");
      }
   }
}

//+------------------------------------------------------------------+
//|  OnTradeTransaction                                                |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeResult      &request,
                        const MqlTradeResult      &result)
{
   // Start cycle timer on first fill if not already running
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD && !g_risk.IsCycleRunning())
      g_risk.StartCycle();
}

//+------------------------------------------------------------------+
//|  OnTester — custom optimisation fitness                            |
//+------------------------------------------------------------------+
double OnTester()
{
   double profit        = TesterStatistics(STAT_PROFIT);
   double max_dd_pct    = TesterStatistics(STAT_EQUITY_DDREL_PERCENT);
   double trades        = TesterStatistics(STAT_TRADES);
   double profit_trades = TesterStatistics(STAT_PROFIT_TRADES);
   double profit_factor = TesterStatistics(STAT_PROFIT_FACTOR);

   if(max_dd_pct >= InpBasketSLPct * 1.5 || trades < 10) return 0.0;

   double win_rate = (trades > 0) ? profit_trades / trades : 0.0;
   double score    = profit_factor * win_rate * (1.0 - max_dd_pct / 100.0) * MathLog(MathMax(1.0, profit));
   return score;
}
