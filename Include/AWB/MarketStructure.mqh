//+------------------------------------------------------------------+
//| AWB MarketStructure.mqh                                          |
//| Swing high/low detection, BOS, and manipulation identification   |
//+------------------------------------------------------------------+
#pragma once

struct SwingPoint
{
   double price;
   int    bar;
   bool   isHigh;
};

//--- Find most recent swing high within lookback bars (on a given rates array)
SwingPoint FindSwingHigh(const MqlRates &rates[], int lookback)
{
   SwingPoint sp;
   sp.price  = 0;
   sp.bar    = -1;
   sp.isHigh = true;

   int total = ArraySize(rates);
   if(total < lookback + 2) return sp;

   // Search from most recent bar backward; bar 0 = current (incomplete), start at 1
   for(int i = 1; i <= lookback && i < total - 1; i++)
   {
      if(rates[i].high > rates[i-1].high && rates[i].high > rates[i+1].high)
      {
         if(sp.bar == -1 || rates[i].high > sp.price)
         {
            sp.price = rates[i].high;
            sp.bar   = i;
         }
      }
   }
   return sp;
}

//--- Find most recent swing low within lookback bars
SwingPoint FindSwingLow(const MqlRates &rates[], int lookback)
{
   SwingPoint sp;
   sp.price  = DBL_MAX;
   sp.bar    = -1;
   sp.isHigh = false;

   int total = ArraySize(rates);
   if(total < lookback + 2) return sp;

   for(int i = 1; i <= lookback && i < total - 1; i++)
   {
      if(rates[i].low < rates[i-1].low && rates[i].low < rates[i+1].low)
      {
         if(sp.bar == -1 || rates[i].low < sp.price)
         {
            sp.price = rates[i].low;
            sp.bar   = i;
         }
      }
   }
   return sp;
}

//--- Bearish BOS: latest closed bar closed below the recent swing low
bool DetectBOS_Bearish(const MqlRates &rates[], double swingLowPrice)
{
   if(ArraySize(rates) < 2 || swingLowPrice <= 0) return false;
   return (rates[1].close < swingLowPrice);
}

//--- Bullish BOS: latest closed bar closed above the recent swing high
bool DetectBOS_Bullish(const MqlRates &rates[], double swingHighPrice)
{
   if(ArraySize(rates) < 2 || swingHighPrice <= 0) return false;
   return (rates[1].close > swingHighPrice);
}

//--- Manipulation to the upside: wick pierced above swingHigh but candle closed back below it
//    Looks back up to lookback bars for such a candle
bool DetectManipulation_Up(const MqlRates &rates[], double swingHighPrice, int lookback = 5)
{
   int total = ArraySize(rates);
   for(int i = 1; i <= lookback && i < total; i++)
   {
      if(rates[i].high > swingHighPrice && rates[i].close < swingHighPrice)
         return true;
   }
   return false;
}

//--- Manipulation to the downside: wick pierced below swingLow but candle closed back above it
bool DetectManipulation_Down(const MqlRates &rates[], double swingLowPrice, int lookback = 5)
{
   int total = ArraySize(rates);
   for(int i = 1; i <= lookback && i < total; i++)
   {
      if(rates[i].low < swingLowPrice && rates[i].close > swingLowPrice)
         return true;
   }
   return false;
}
