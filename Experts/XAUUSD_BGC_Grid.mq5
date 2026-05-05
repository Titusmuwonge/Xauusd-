//+------------------------------------------------------------------+
//| XAUUSD_BGC_Grid.mq5                                              |
//| Bi-Directional Grid Constrained (BGC) Expert Advisor             |
//| XAU/USD · MQL5 · MT5 Strategy Tester Compatible                  |
//|                                                                   |
//| Architecture:                                                     |
//|   RegimeDetector  — CUSUM structural-break classifier             |
//|   GridManager     — ATR-spaced order engine (MGT / TGT modes)    |
//|   RiskEngine      — Basket TP · Hard SL · Age Limit · News filter |
//|                                                                   |
//| Trade-context guards:                                             |
//|   • MQLInfoInteger(MQL_TRADE_ALLOWED)                             |
//|   • AccountInfoInteger(ACCOUNT_TRADE_ALLOWED / ACCOUNT_TRADE_EXPERT)|
//|   • TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)                   |
//|   • CTrade result-retcode checked on every order                  |
//|   • Requote retry on close operations (RiskEngine)               |
//|   • IOC fill mode + deviation points to absorb spread spikes      |
//+------------------------------------------------------------------+
#property copyright   "BGC Grid EA — XAU/USD"
#property version     "2.00"
#property description "Regime-Aware Mean-Reversion / Trend Grid for Gold"
#property strict

#include <BGC\RegimeDetector.mqh>
#include <BGC\GridManager.mqh>
#include <BGC\RiskEngine.mqh>

//+------------------------------------------------------------------+
//|  Input Parameters                                                 |
//+------------------------------------------------------------------+

// --- Symbol & Timeframe ---
input string   InpSymbol          = "";            // Symbol (blank = chart symbol)
input ENUM_TIMEFRAMES InpTF       = PERIOD_M15;    // Working timeframe

// --- Regime Detection ---
input int      InpEMAPeriod       = 20;            // EMA period
input int      InpATRPeriod       = 14;            // ATR period
input double   InpCUSUMThreshold  = 4.0;           // CUSUM breakout threshold (σ)
input double   InpCUSUMAllowance  = 0.5;           // CUSUM allowance k
input double   InpDevSigma        = 2.0;           // Std-dev trigger for entry

// --- Grid Parameters ---
input double   InpLotSize         = 0.01;          // Lot size per grid level
input int      InpMaxLevels       = 4;             // Max grid levels per side
input double   InpGridATRMult     = 0.5;           // Grid spacing = ATR × mult
input double   InpTPATRMult       = 1.0;           // Per-order TP (0 = basket only)
input int      InpSlippagePts     = 30;            // Max slippage in points

// --- Risk Management ---
input double   InpBasketTPPct     = 0.50;          // Basket TP as % of balance
input double   InpBasketSLPct     = 2.00;          // Hard SL as % of balance
input int      InpAgeLimitHours   = 4;             // Max cycle age (hours)

// --- News Filter ---
input bool     InpNewsFilter      = true;          // Enable high-impact news filter
input int      InpNewsBufferMin   = 30;            // Minutes before/after news event

// --- EA Identity ---
input int      InpMagic           = 20260505;      // EA magic number

//+------------------------------------------------------------------+
//|  Module Instances                                                  |
//+------------------------------------------------------------------+
CRegimeDetector g_regime;
CGridManager    g_grid;
CRiskEngine     g_risk;

string g_symbol;
bool   g_initialized = false;

// Tick throttle: only run heavy logic on a new bar to avoid over-ordering
datetime g_last_bar_time = 0;

//+------------------------------------------------------------------+
//|  Utility                                                           |
//+------------------------------------------------------------------+
bool IsTradeContextReady()
{
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))          { return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))   { return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))    { return false; }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) { return false; }
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

   // Sanity: confirm this is a gold instrument
   string sym_upper = g_symbol;
   StringToUpper(sym_upper);
   if(StringFind(sym_upper, "XAU") < 0 && StringFind(sym_upper, "GOL") < 0)
      PrintFormat("BGC Grid: WARNING — symbol '%s' may not be XAU/USD. Proceeding.", g_symbol);

   // Validate inputs
   if(InpLotSize <= 0.0)    { Print("BGC Grid: InpLotSize must be > 0"); return INIT_PARAMETERS_INCORRECT; }
   if(InpMaxLevels < 1)     { Print("BGC Grid: InpMaxLevels must be >= 1"); return INIT_PARAMETERS_INCORRECT; }
   if(InpBasketTPPct <= 0.0){ Print("BGC Grid: InpBasketTPPct must be > 0"); return INIT_PARAMETERS_INCORRECT; }

   // Validate lot size against broker minimum
   double min_lot  = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double lot_step = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   if(InpLotSize < min_lot)
   {
      PrintFormat("BGC Grid: InpLotSize %.2f below broker minimum %.2f", InpLotSize, min_lot);
      return INIT_PARAMETERS_INCORRECT;
   }

   // Round lot to broker step
   double adj_lot = MathRound(InpLotSize / lot_step) * lot_step;

   // Initialise modules
   if(!g_regime.Init(g_symbol, InpTF, InpEMAPeriod, InpATRPeriod,
                     InpCUSUMThreshold, InpCUSUMAllowance, InpDevSigma))
   {
      Print("BGC Grid: RegimeDetector init failed");
      return INIT_FAILED;
   }

   if(!g_grid.Init(g_symbol, InpMagic, adj_lot, InpMaxLevels,
                   InpGridATRMult, InpTPATRMult, InpSlippagePts))
   {
      Print("BGC Grid: GridManager init failed");
      return INIT_FAILED;
   }

   if(!g_risk.Init(g_symbol, InpMagic, InpBasketTPPct, InpBasketSLPct,
                   InpAgeLimitHours, InpNewsFilter, InpNewsBufferMin, InpSlippagePts))
   {
      Print("BGC Grid: RiskEngine init failed");
      return INIT_FAILED;
   }

   g_initialized    = true;
   g_last_bar_time  = 0;

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
//|  OnTick — Main control loop                                        |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_initialized) return;

   //--- 1. Trade-context guard — absolute first check
   if(!IsTradeContextReady()) return;

   //--- 2. News filter — pause all activity around high-impact events
   if(g_risk.IsNewsFilterActive())
   {
      // If a cycle is running, leave existing positions open (don't add more)
      // New grid orders are simply blocked; existing positions ride it out
      static datetime last_news_warn = 0;
      if(TimeCurrent() - last_news_warn > 300)
      {
         Print("BGC Grid: News filter active — new orders blocked");
         last_news_warn = TimeCurrent();
      }
      g_grid.CancelAllPendingOrders(); // cancel unfilled pending to avoid bad fills
      return;
   }

   //--- 3. Risk checks — run every tick (not just new bar) for fast response
   bool basket_tp_hit = g_risk.CheckBasketTP();
   bool basket_sl_hit = g_risk.CheckBasketSL();
   bool age_limit_hit = g_risk.CheckAgeLimit();

   if(basket_tp_hit || basket_sl_hit || age_limit_hit)
   {
      // Cycle ended; cancel remaining pending orders and reset regime
      g_grid.CancelAllPendingOrders();
      g_grid.Deactivate();
      g_regime.Reset();
      return;
   }

   //--- 4. Bar-rate logic (regime detection + grid placement once per bar)
   if(!IsNewBar()) return;

   //--- 5. Update regime detector
   if(!g_regime.Update())
   {
      Print("BGC Grid: RegimeDetector update failed (waiting for data)");
      return;
   }

   double atr = g_regime.GetATR();
   double ema = g_regime.GetEMA();
   if(atr <= 0.0) return;

   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   if(bid <= 0.0 || ask <= 0.0) return;

   ENUM_MARKET_REGIME regime = g_regime.GetRegime();

   //--- 6. Regime-based grid management
   switch(regime)
   {
      case REGIME_RANGING:
      {
         // Cancel any trend-following (TGT) orders from a previous mode switch
         // before placing mean-reversion (MGT) orders
         int ext_dir = 0;
         if(g_regime.IsPriceExtended((bid + ask) * 0.5, ext_dir))
         {
            // Only build the grid when price is actually stretched
            if(!g_risk.IsCycleRunning())
            {
               g_risk.StartCycle();
               PrintFormat("BGC Grid: NEW CYCLE — MGT mode | ATR=%.2f EMA=%.2f CUSUM+/−=%.2f/%.2f",
                           atr, ema, g_regime.GetCUSUMPos(), g_regime.GetCUSUMNeg());
            }
            g_grid.ManageMGTGrid(bid, ask, ema, atr);
         }
         break;
      }

      case REGIME_TRENDING_UP:
      case REGIME_TRENDING_DOWN:
      {
         int direction = (regime == REGIME_TRENDING_UP) ? 1 : -1;

         // Cancel all counter-trend (MGT) limit orders — they fight the trend
         g_grid.CancelAllPendingOrders();

         if(!g_risk.IsCycleRunning())
         {
            g_risk.StartCycle();
            PrintFormat("BGC Grid: NEW CYCLE — TGT mode | direction=%s ATR=%.2f CUSUM+/−=%.2f/%.2f",
                        (direction > 0 ? "UP" : "DOWN"), atr,
                        g_regime.GetCUSUMPos(), g_regime.GetCUSUMNeg());
         }
         g_grid.ManageTGTGrid(bid, ask, atr, direction);
         break;
      }
   }

   //--- 7. If no positions and no pending orders, reset cycle state
   if(g_risk.GetPositionCount() == 0 && g_grid.GetPendingOrderCount() == 0)
   {
      if(g_risk.IsCycleRunning())
      {
         g_risk.StopCycle();
         g_grid.Deactivate();
         PrintFormat("BGC Grid: Cycle closed naturally — resetting");
      }
   }
}

//+------------------------------------------------------------------+
//|  OnTradeTransaction — detect basket fill to start cycle timer     |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
{
   // When the first position in a new cycle opens, ensure cycle timer is running
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      if((int)trans.position != 0 && !g_risk.IsCycleRunning())
         g_risk.StartCycle();
   }

   // If a deal closes the last position (DEAL_ENTRY_OUT), stop the cycle
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD &&
      trans.deal_type == DEAL_TYPE_SELL || trans.deal_type == DEAL_TYPE_BUY)
   {
      // RiskEngine will self-correct on the next tick check; no action needed here
   }
}

//+------------------------------------------------------------------+
//|  OnTester — return a sortable optimization metric                  |
//+------------------------------------------------------------------+
double OnTester()
{
   double profit       = TesterStatistics(STAT_PROFIT);
   double max_dd       = TesterStatistics(STAT_EQUITY_DDREL_PERCENT);
   double trades       = TesterStatistics(STAT_TRADES);
   double win_rate     = (trades > 0) ? TesterStatistics(STAT_PROFIT_TRADES) / trades : 0.0;

   // Penalise high drawdown heavily; reward win rate and profit
   if(max_dd >= InpBasketSLPct * 1.5 || trades < 10) return 0.0;

   // Custom fitness: profit-factor weighted by inverse drawdown and win-rate
   double profit_factor = TesterStatistics(STAT_PROFIT_FACTOR);
   double score = profit_factor * win_rate * (1.0 - max_dd / 100.0) * MathLog(MathMax(1.0, profit));

   return score;
}
