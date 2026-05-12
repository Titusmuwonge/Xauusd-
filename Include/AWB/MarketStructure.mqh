//+------------------------------------------------------------------+
//| AWB MarketStructure.mqh  v2                                      |
//| BOS detection (strong body + new N-bar extreme) + sweep filter   |
//+------------------------------------------------------------------+
#pragma once

// Pip helpers (5-digit brokers)
double AWB_PipSize(string sym = NULL)
{
   if(sym == NULL || sym == "") sym = _Symbol;
   int d = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   return ((d == 3 || d == 5) ? SymbolInfoDouble(sym, SYMBOL_POINT) * 10.0
                               : SymbolInfoDouble(sym, SYMBOL_POINT));
}

//--- Bearish BOS: bar[1] has bearish body >= minBodyPips AND close below prior lb-bar lows
//    rates[] must be series (index 0 = newest / forming, index 1 = last closed)
bool Is_BOS_Bear(const MqlRates &r15[], int lb = 12, int minBodyPips = 5)
{
   if(ArraySize(r15) < lb + 3) return false;
   double pip = AWB_PipSize();
   if((r15[1].open - r15[1].close) < minBodyPips * pip) return false;

   // close must be strictly below the lowest LOW of bars [2 .. lb+1]
   double minLow = r15[2].low;
   for(int i = 3; i <= lb + 1 && i < ArraySize(r15); i++)
      minLow = MathMin(minLow, r15[i].low);

   return r15[1].close < minLow;
}

//--- Bullish BOS: bar[1] has bullish body >= minBodyPips AND close above prior lb-bar highs
bool Is_BOS_Bull(const MqlRates &r15[], int lb = 12, int minBodyPips = 5)
{
   if(ArraySize(r15) < lb + 3) return false;
   double pip = AWB_PipSize();
   if((r15[1].close - r15[1].open) < minBodyPips * pip) return false;

   double maxHigh = r15[2].high;
   for(int i = 3; i <= lb + 1 && i < ArraySize(r15); i++)
      maxHigh = MathMax(maxHigh, r15[i].high);

   return r15[1].close > maxHigh;
}

//--- Find highest swing high in bars [2 .. lb+2] of the 1H array
double Swing_High_1H(const MqlRates &r1h[], int lb = 8)
{
   double best = 0;
   int total = ArraySize(r1h);
   for(int i = 2; i < lb + 3 && i < total - 1; i++)
   {
      if(r1h[i].high > r1h[i-1].high && r1h[i].high > r1h[i+1].high)
         best = MathMax(best, r1h[i].high);
   }
   return best;
}

//--- Find lowest swing low in bars [2 .. lb+2] of the 1H array
double Swing_Low_1H(const MqlRates &r1h[], int lb = 8)
{
   double best = DBL_MAX;
   int total = ArraySize(r1h);
   for(int i = 2; i < lb + 3 && i < total - 1; i++)
   {
      if(r1h[i].low < r1h[i-1].low && r1h[i].low < r1h[i+1].low)
         best = MathMin(best, r1h[i].low);
   }
   return (best == DBL_MAX) ? 0 : best;
}

//--- Swept Up: within last lb bars of 1H, any bar had wick > swingHigh + 3 pips AND close < swingHigh - 3 pips
bool Swept_Up(const MqlRates &r1h[], double swingHigh, int lb = 8)
{
   if(swingHigh <= 0) return false;
   double pip = AWB_PipSize();
   double minWick = 3 * pip;
   for(int k = 1; k <= lb && k < ArraySize(r1h); k++)
   {
      if(r1h[k].high > swingHigh + minWick &&
         r1h[k].close < swingHigh - minWick)
         return true;
   }
   return false;
}

//--- Swept Down: within last lb bars of 1H, any bar had wick < swingLow - 3 pips AND close > swingLow + 3 pips
bool Swept_Down(const MqlRates &r1h[], double swingLow, int lb = 8)
{
   if(swingLow <= 0) return false;
   double pip = AWB_PipSize();
   double minWick = 3 * pip;
   for(int k = 1; k <= lb && k < ArraySize(r1h); k++)
   {
      if(r1h[k].low < swingLow - minWick &&
         r1h[k].close > swingLow + minWick)
         return true;
   }
   return false;
}
