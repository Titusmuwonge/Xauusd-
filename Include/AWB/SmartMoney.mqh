//+------------------------------------------------------------------+
//| AWB SmartMoney.mqh                                               |
//| Fair Value Gap (FVG), Order Block (OB), Engulfing detection      |
//+------------------------------------------------------------------+
#pragma once

struct FVG
{
   double high;
   double low;
   bool   bearish; // true = price came from above (sell FVG), false = buy FVG
   int    bar;     // index of the middle candle of the 3-candle pattern
   bool   valid;
};

struct OrderBlock
{
   double high;
   double low;
   bool   bearish; // true = last bullish candle before bearish impulse
   int    bar;
   bool   valid;
};

//--- Detect the most recent bearish FVG within lookback bars
//    Pattern: rates[i+2].low > rates[i].high  (gap between candle[i+2] and candle[i])
//    In array notation (index 0 = most recent): need 3 consecutive candles
FVG DetectFVG_Bearish(const MqlRates &rates[], int lookback = 50)
{
   FVG fvg;
   fvg.valid = false;
   int total = ArraySize(rates);

   for(int i = 1; i <= lookback && i + 2 < total; i++)
   {
      // rates[i-1] = most recent of the trio, rates[i] = middle, rates[i+1] = oldest
      // Bearish FVG: high of candle[i+1] < low of candle[i-1]
      if(rates[i+1].high < rates[i-1].low)
      {
         fvg.high    = rates[i-1].low;
         fvg.low     = rates[i+1].high;
         fvg.bearish = true;
         fvg.bar     = i;
         fvg.valid   = true;
         return fvg;
      }
   }
   return fvg;
}

//--- Detect the most recent bullish FVG
//    Bullish FVG: low of candle[i+1] > high of candle[i-1]
FVG DetectFVG_Bullish(const MqlRates &rates[], int lookback = 50)
{
   FVG fvg;
   fvg.valid = false;
   int total = ArraySize(rates);

   for(int i = 1; i <= lookback && i + 2 < total; i++)
   {
      if(rates[i+1].low > rates[i-1].high)
      {
         fvg.high    = rates[i+1].low;
         fvg.low     = rates[i-1].high;
         fvg.bearish = false;
         fvg.bar     = i;
         fvg.valid   = true;
         return fvg;
      }
   }
   return fvg;
}

//--- Detect the Order Block: last bullish candle before a bearish BOS impulse
//    bosBar = bar index where BOS occurred; search backward from there
OrderBlock DetectOrderBlock_Bearish(const MqlRates &rates[], int bosBar, int lookback = 10)
{
   OrderBlock ob;
   ob.valid = false;
   int total = ArraySize(rates);

   for(int i = bosBar + 1; i <= bosBar + lookback && i < total; i++)
   {
      // Last bullish candle (close > open) before the impulse move
      if(rates[i].close > rates[i].open)
      {
         ob.high    = rates[i].high;
         ob.low     = rates[i].low;
         ob.bearish = true;
         ob.bar     = i;
         ob.valid   = true;
         return ob;
      }
   }
   return ob;
}

//--- Last bearish candle before bullish BOS impulse
OrderBlock DetectOrderBlock_Bullish(const MqlRates &rates[], int bosBar, int lookback = 10)
{
   OrderBlock ob;
   ob.valid = false;
   int total = ArraySize(rates);

   for(int i = bosBar + 1; i <= bosBar + lookback && i < total; i++)
   {
      if(rates[i].close < rates[i].open)
      {
         ob.high    = rates[i].high;
         ob.low     = rates[i].low;
         ob.bearish = false;
         ob.bar     = i;
         ob.valid   = true;
         return ob;
      }
   }
   return ob;
}

//--- Price is inside a zone
bool PriceInZone(double price, double zoneHigh, double zoneLow)
{
   return (price >= zoneLow && price <= zoneHigh);
}

//--- Bearish engulfing on the current closed bar (bar index 1)
//    Current candle opens above previous close and closes below previous open
bool DetectEngulfing_Bearish(const MqlRates &rates[])
{
   if(ArraySize(rates) < 3) return false;
   double curOpen  = rates[1].open;
   double curClose = rates[1].close;
   double prevOpen = rates[2].open;
   double prevClose= rates[2].close;

   // Previous candle must be bullish
   if(prevClose <= prevOpen) return false;
   // Current candle must be bearish and fully engulf previous body
   return (curOpen >= prevClose && curClose < prevOpen);
}

//--- Bullish engulfing
bool DetectEngulfing_Bullish(const MqlRates &rates[])
{
   if(ArraySize(rates) < 3) return false;
   double curOpen  = rates[1].open;
   double curClose = rates[1].close;
   double prevOpen = rates[2].open;
   double prevClose= rates[2].close;

   // Previous candle must be bearish
   if(prevClose >= prevOpen) return false;
   // Current candle must be bullish and fully engulf previous body
   return (curOpen <= prevClose && curClose > prevOpen);
}
