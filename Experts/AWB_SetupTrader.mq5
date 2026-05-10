//+------------------------------------------------------------------+
//| AWB SetupTrader EA                                               |
//| SMC/ICT dual-setup trader for currency pairs                     |
//| Setup 1: Manipulation → BOS → FVG+OB → Engulfing → Session      |
//| Setup 2: Distribution → FVG+OB → Engulfing → Session            |
//+------------------------------------------------------------------+
#property copyright "AlignedWithBanks"
#property version   "1.00"
#property strict

#include "../Include/AWB/MarketStructure.mqh"
#include "../Include/AWB/SmartMoney.mqh"
#include "../Include/AWB/Sessions.mqh"
#include "../Include/AWB/RiskManager.mqh"

//--- Input parameters
input double   RiskPercent     = 1.0;    // Risk per trade (% of balance)
input int      SL_Pips         = 15;     // Stop loss in pips
input int      TP_Pips         = 34;     // Take profit in pips
input int      SwingLookback   = 10;     // Bars for swing detection (M15/H1)
input int      FVG_Lookback    = 50;     // Bars to scan for FVG
input int      MaxTrades       = 1;      // Max simultaneous positions
input long     MagicNumber     = 20250001;
input bool     DrawSeparators  = true;   // Draw session period separators
input bool     EnableSetup1    = true;   // Enable Setup 1 (5-step)
input bool     EnableSetup2    = true;   // Enable Setup 2 (distribution-retrace)

CTrade  trade;

// Track last signal bar to avoid re-entering on same candle
datetime lastSignalTime = 0;

//+------------------------------------------------------------------+
//| Initialisation                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   if(DrawSeparators)
      DrawAllPeriodSeparators(1000);

   Print("AWB SetupTrader initialised | Risk:", RiskPercent, "% SL:", SL_Pips, "pip TP:", TP_Pips, "pip");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Main tick logic                                                  |
//+------------------------------------------------------------------+
void OnTick()
{
   // Only act on a new 5M candle close
   datetime barTime = iTime(_Symbol, PERIOD_M5, 1);
   if(barTime == lastSignalTime) return;

   // Session gate
   if(!IsInTradingSession()) return;

   // Position limit
   if(CountOpenPositions(MagicNumber) >= MaxTrades) return;

   // --- Load multi-timeframe data ---
   MqlRates rates5m[], rates15m[], rates1h[];
   if(CopyRates(_Symbol, PERIOD_M5,  0, 100, rates5m)  < 10) return;
   if(CopyRates(_Symbol, PERIOD_M15, 0, 60,  rates15m) < 10) return;
   if(CopyRates(_Symbol, PERIOD_H1,  0, 30,  rates1h)  < 5)  return;

   // Arrays are returned oldest-first by CopyRates; reverse so index 0 = newest
   ArraySetAsSeries(rates5m,  true);
   ArraySetAsSeries(rates15m, true);
   ArraySetAsSeries(rates1h,  true);

   // --- Detect signals ---
   bool sellSignal = false;
   bool buySignal  = false;

   if(EnableSetup1)
   {
      CheckSetup1(rates5m, rates15m, rates1h, sellSignal, buySignal);
   }
   if(!sellSignal && !buySignal && EnableSetup2)
   {
      CheckSetup2(rates5m, rates15m, rates1h, sellSignal, buySignal);
   }

   // --- Execute ---
   if(sellSignal)
   {
      double lots = CalcLotSize(RiskPercent, SL_Pips);
      if(lots > 0)
      {
         if(PlaceSellOrder(trade, lots, SL_Pips, TP_Pips) > 0)
         {
            lastSignalTime = barTime;
            Print("AWB SELL | lots:", lots, " | bar:", TimeToString(barTime));
         }
      }
   }
   else if(buySignal)
   {
      double lots = CalcLotSize(RiskPercent, SL_Pips);
      if(lots > 0)
      {
         if(PlaceBuyOrder(trade, lots, SL_Pips, TP_Pips) > 0)
         {
            lastSignalTime = barTime;
            Print("AWB BUY  | lots:", lots, " | bar:", TimeToString(barTime));
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Setup 1: 5-step institutional hunt                               |
//| Timeframes: 1H structure → 15M BOS/FVG → 5M engulfing entry     |
//+------------------------------------------------------------------+
void CheckSetup1(const MqlRates &r5m[], const MqlRates &r15m[], const MqlRates &r1h[],
                 bool &sellOut, bool &buyOut)
{
   // ---- SELL SETUP 1 ----
   // Step 1: Manipulation up on 1H (swing high swept)
   SwingPoint sh1h = FindSwingHigh(r1h, SwingLookback);
   if(sh1h.bar >= 0)
   {
      bool manUp = DetectManipulation_Up(r1h, sh1h.price, 5);

      // Step 2: BOS bearish on 15M after manipulation
      SwingPoint sl15m = FindSwingLow(r15m, SwingLookback);
      bool bos_bear = DetectBOS_Bearish(r15m, sl15m.price);

      if(manUp && bos_bear)
      {
         // Step 3: FVG + OB on 15M
         FVG fvg = DetectFVG_Bearish(r15m, FVG_Lookback);
         // Step 4: Engulfing on 5M within FVG zone
         if(fvg.valid)
         {
            bool engulf = DetectEngulfing_Bearish(r5m);
            bool inZone = PriceInZone(r5m[1].close, fvg.high, fvg.low);
            if(engulf && inZone)
            {
               sellOut = true;
               return;
            }
         }
      }
   }

   // ---- BUY SETUP 1 ----
   SwingPoint sl1h = FindSwingLow(r1h, SwingLookback);
   if(sl1h.bar >= 0)
   {
      bool manDown = DetectManipulation_Down(r1h, sl1h.price, 5);

      SwingPoint sh15m = FindSwingHigh(r15m, SwingLookback);
      bool bos_bull = DetectBOS_Bullish(r15m, sh15m.price);

      if(manDown && bos_bull)
      {
         FVG fvg = DetectFVG_Bullish(r15m, FVG_Lookback);
         if(fvg.valid)
         {
            bool engulf = DetectEngulfing_Bullish(r5m);
            bool inZone = PriceInZone(r5m[1].close, fvg.high, fvg.low);
            if(engulf && inZone)
            {
               buyOut = true;
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Setup 2: Distribution-retrace hunt                               |
//| 1H/15M FVG+OB zone, 5M engulfing trigger                        |
//+------------------------------------------------------------------+
void CheckSetup2(const MqlRates &r5m[], const MqlRates &r15m[], const MqlRates &r1h[],
                 bool &sellOut, bool &buyOut)
{
   // ---- SELL SETUP 2 ----
   // Bearish FVG on 1H (distribution left an imbalance)
   FVG fvg1h_bear = DetectFVG_Bearish(r1h, FVG_Lookback);
   if(fvg1h_bear.valid)
   {
      // Confirm on 15M with a bearish FVG in the same zone
      FVG fvg15m_bear = DetectFVG_Bearish(r15m, FVG_Lookback);
      bool zoneMatch = fvg15m_bear.valid &&
                       fvg15m_bear.high <= fvg1h_bear.high &&
                       fvg15m_bear.low  >= fvg1h_bear.low;

      // 5M engulfing bearish inside the zone
      bool engulf = DetectEngulfing_Bearish(r5m);
      bool inZone = PriceInZone(r5m[1].close, fvg1h_bear.high, fvg1h_bear.low);

      if((zoneMatch || fvg1h_bear.valid) && engulf && inZone)
      {
         sellOut = true;
         return;
      }
   }

   // ---- BUY SETUP 2 ----
   FVG fvg1h_bull = DetectFVG_Bullish(r1h, FVG_Lookback);
   if(fvg1h_bull.valid)
   {
      FVG fvg15m_bull = DetectFVG_Bullish(r15m, FVG_Lookback);
      bool zoneMatch = fvg15m_bull.valid &&
                       fvg15m_bull.high <= fvg1h_bull.high &&
                       fvg15m_bull.low  >= fvg1h_bull.low;

      bool engulf = DetectEngulfing_Bullish(r5m);
      bool inZone = PriceInZone(r5m[1].close, fvg1h_bull.high, fvg1h_bull.low);

      if((zoneMatch || fvg1h_bull.valid) && engulf && inZone)
      {
         buyOut = true;
      }
   }
}

//+------------------------------------------------------------------+
//| Redraw separators when chart is refreshed                        |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam,
                  const double &dparam, const string &sparam)
{
   if(DrawSeparators && id == CHARTEVENT_CHART_CHANGE)
      DrawAllPeriodSeparators(1000);
}

void OnDeinit(const int reason)
{
   Print("AWB SetupTrader removed. Reason:", reason);
}
