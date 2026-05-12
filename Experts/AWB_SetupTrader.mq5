//+------------------------------------------------------------------+
//| AWB SetupTrader EA  v2                                           |
//| SMC/ICT strategy: BOS + 1H sweep + FVG zone touch entry         |
//|                                                                  |
//| Setup detection (on new 15M bar close):                         |
//|   SELL: bearish BOS (body>=5pip, new 12-bar low) + 1H swing-high  |
//|         swept up => FVG zone ABOVE price => zone touch => SELL    |
//|   BUY:  bullish BOS (body>=5pip, new 12-bar high) + 1H swing-low  |
//|         swept down => FVG zone BELOW price => zone touch => BUY   |
//|                                                                  |
//| Filters: Sell NY session only. Buy both sessions.                |
//|          One trade per session per day.                          |
//|          BE moved to entry+1pip after 1R profit.                 |
//| Risk:    1.5% per trade, SL=15pip, TP=34pip.                     |
//+------------------------------------------------------------------+
#property copyright "AlignedWithBanks"
#property version   "2.00"
#property strict

#include "../Include/AWB/MarketStructure.mqh"
#include "../Include/AWB/SmartMoney.mqh"
#include "../Include/AWB/Sessions.mqh"
#include "../Include/AWB/RiskManager.mqh"

//--- Inputs
input double RiskPercent    = 1.5;    // Risk per trade (% of balance)
input int    SL_Pips        = 15;     // Stop loss in pips
input int    TP_Pips        = 34;     // Take profit in pips
input int    SwingLB_1H     = 8;      // 1H swing lookback bars
input int    BOS_LB_15M     = 12;     // 15M BOS new-extreme lookback
input int    ZoneExpiry_H   = 24;     // Hours before zone expires
input int    ZoneBlown_Pips = 8;      // Pips through zone = blown
input long   MagicNumber    = 20250002;
input bool   DrawSeparators = true;

CTrade trade;

//--- Setup state
bool     g_setup    = false;
string   g_dir      = "";      // "buy" or "sell"
double   g_zone_h   = 0;
double   g_zone_l   = 0;
datetime g_expiry   = 0;

//--- Session trade-per-day trackers
datetime g_london_day = 0;   // date when London was already traded
datetime g_ny_day     = 0;   // date when NY was already traded

//--- Tracks last 15M bar to avoid re-processing
datetime g_last15 = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   if(DrawSeparators)
      DrawAllPeriodSeparators(1000);

   Print("AWB SetupTrader v2 | Risk:", RiskPercent, "% SL:", SL_Pips, " TP:", TP_Pips);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Breakeven management for open positions
   if(CountOpenPositions(MagicNumber) > 0)
      ManageBreakeven(trade, MagicNumber, SL_Pips);

   // New 15M bar close: attempt setup detection
   datetime bar15 = iTime(_Symbol, PERIOD_M15, 1);
   if(bar15 != g_last15)
   {
      g_last15 = bar15;
      TryDetectSetup();
   }

   // Zone management: expiry + blown through
   if(g_setup)
   {
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(TimeCurrent() >= g_expiry)
      {
         g_setup = false;
      }
      else if(g_dir == "sell" && price > g_zone_h + ZoneBlown_Pips * AWB_PipSize())
      {
         g_setup = false;
      }
      else if(g_dir == "buy" && price < g_zone_l - ZoneBlown_Pips * AWB_PipSize())
      {
         g_setup = false;
      }
   }

   if(!g_setup) return;
   if(CountOpenPositions(MagicNumber) > 0) return;

   // Session gate
   bool inLondon = IsLondonSession();
   bool inNY     = IsNYSession();
   if(!inLondon && !inNY) return;

   // Sell: NY session only
   if(g_dir == "sell" && !inNY) return;

   // Per-session daily limit
   datetime today = (datetime)(TimeCurrent() - TimeCurrent() % 86400);
   if(inLondon && g_dir == "buy"  && g_london_day == today) return;
   if(inNY     && g_dir == "sell" && g_ny_day     == today) return;
   if(inNY     && g_dir == "buy"  && g_ny_day     == today) return;

   // Check zone touch on the last closed 5M bar
   MqlRates r5[];
   if(CopyRates(_Symbol, PERIOD_M5, 1, 2, r5) < 2) return;
   ArraySetAsSeries(r5, true);

   double pip   = AWB_PipSize();
   bool touched = false;

   if(g_dir == "sell")
   {
      // High of last closed bar reached zone bottom; close not blown through zone top
      touched = (r5[0].high >= g_zone_l &&
                 r5[0].close <= g_zone_h + ZoneBlown_Pips * pip);
   }
   else
   {
      // Low of last closed bar reached zone top; close not blown through zone bottom
      touched = (r5[0].low <= g_zone_h &&
                 r5[0].close >= g_zone_l - ZoneBlown_Pips * pip);
   }

   if(!touched) return;

   // Fire trade
   double lots = CalcLotSize(RiskPercent, SL_Pips);
   if(lots <= 0) return;

   if(g_dir == "sell")
   {
      if(PlaceSellOrder(trade, lots, SL_Pips, TP_Pips) > 0)
      {
         Print("AWB SELL fired | zone ", DoubleToString(g_zone_l, 5),
               "-", DoubleToString(g_zone_h, 5));
         g_setup  = false;
         g_ny_day = today;
      }
   }
   else
   {
      if(PlaceBuyOrder(trade, lots, SL_Pips, TP_Pips) > 0)
      {
         Print("AWB BUY fired | zone ", DoubleToString(g_zone_l, 5),
               "-", DoubleToString(g_zone_h, 5));
         g_setup = false;
         if(inLondon) g_london_day = today;
         if(inNY)     g_ny_day     = today;
      }
   }
}

//+------------------------------------------------------------------+
//| Detect setup on the most recently closed 15M bar                 |
//+------------------------------------------------------------------+
void TryDetectSetup()
{
   if(g_setup) return;

   int bars15 = BOS_LB_15M + 10;
   int bars1h = SwingLB_1H + 10;

   MqlRates r15[], r1h[];
   if(CopyRates(_Symbol, PERIOD_M15, 0, bars15, r15) < bars15) return;
   if(CopyRates(_Symbol, PERIOD_H1,  0, bars1h, r1h) < bars1h) return;
   ArraySetAsSeries(r15, true);
   ArraySetAsSeries(r1h, true);

   // SELL: bearish BOS + 1H swing-high swept up
   if(Is_BOS_Bear(r15, BOS_LB_15M))
   {
      double sh = Swing_High_1H(r1h, SwingLB_1H);
      if(sh > 0 && Swept_Up(r1h, sh, SwingLB_1H))
      {
         double zh = 0, zl = 0;
         if(FVG_Zone_Bear(r15, zh, zl))
         {
            g_setup  = true;
            g_dir    = "sell";
            g_zone_h = zh;
            g_zone_l = zl;
            g_expiry = TimeCurrent() + ZoneExpiry_H * 3600;
            Print("AWB SELL setup | zone ", DoubleToString(zl,5),
                  "-", DoubleToString(zh,5));
            return;
         }
      }
   }

   // BUY: bullish BOS + 1H swing-low swept down
   if(Is_BOS_Bull(r15, BOS_LB_15M))
   {
      double sl_price = Swing_Low_1H(r1h, SwingLB_1H);
      if(sl_price > 0 && Swept_Down(r1h, sl_price, SwingLB_1H))
      {
         double zh = 0, zl = 0;
         if(FVG_Zone_Bull(r15, zh, zl))
         {
            g_setup  = true;
            g_dir    = "buy";
            g_zone_h = zh;
            g_zone_l = zl;
            g_expiry = TimeCurrent() + ZoneExpiry_H * 3600;
            Print("AWB BUY setup | zone ", DoubleToString(zl,5),
                  "-", DoubleToString(zh,5));
         }
      }
   }
}

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam,
                  const double &dparam, const string &sparam)
{
   if(DrawSeparators && id == CHARTEVENT_CHART_CHANGE)
      DrawAllPeriodSeparators(1000);
}

void OnDeinit(const int reason)
{
   Print("AWB SetupTrader v2 removed. Reason:", reason);
}
