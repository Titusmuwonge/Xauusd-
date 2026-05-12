//+------------------------------------------------------------------+
//| AWB SmartMoney.mqh  v2                                           |
//| FVG zone detection on the BOS bar itself (no lookahead)          |
//+------------------------------------------------------------------+
#pragma once
#include "MarketStructure.mqh"

// Minimum/maximum FVG gap in pips
#define FVG_MIN_PIPS  1
#define FVG_MAX_PIPS 25

//--- Bearish FVG zone: gap between bar[3].low and bar[1].high in the 15M array
//    (bar[1] = BOS bar, bar[3] = two bars before it)
//    zone_h = bar[3].low, zone_l = bar[1].high
//    Valid when gap >= 1 pip AND zone is above bar[1].close
//    Returns true and fills zone_h / zone_l.
bool FVG_Zone_Bear(const MqlRates &r15[], double &zone_h, double &zone_l)
{
   zone_h = 0; zone_l = 0;
   if(ArraySize(r15) < 5) return false;

   double pip = AWB_PipSize();
   double minGap = FVG_MIN_PIPS * pip;
   double maxGap = FVG_MAX_PIPS * pip;

   // Primary: bars [3,1] (2-bar separation around the BOS bar)
   double gap = r15[3].low - r15[1].high;
   if(gap >= minGap && gap <= maxGap)
   {
      zone_h = r15[3].low;
      zone_l = r15[1].high;
      return (zone_l > r15[1].close);
   }

   // Secondary: bars [4,2]
   if(ArraySize(r15) >= 6)
   {
      gap = r15[4].low - r15[2].high;
      if(gap >= minGap && gap <= maxGap)
      {
         zone_h = r15[4].low;
         zone_l = r15[2].high;
         return (zone_l > r15[1].close);
      }
   }

   // Fallback: 2-bar gap [2,1]
   gap = r15[2].low - r15[1].high;
   if(gap >= minGap && gap <= maxGap)
   {
      zone_h = r15[2].low;
      zone_l = r15[1].high;
      return (zone_l > r15[1].close);
   }

   return false;
}

//--- Bullish FVG zone: gap between bar[1].low and bar[3].high in the 15M array
//    (bar[1] = BOS bar going up, bar[3] = two bars before it)
//    zone_h = bar[1].low, zone_l = bar[3].high
//    Valid when gap >= 1 pip AND zone is below bar[1].close
bool FVG_Zone_Bull(const MqlRates &r15[], double &zone_h, double &zone_l)
{
   zone_h = 0; zone_l = 0;
   if(ArraySize(r15) < 5) return false;

   double pip = AWB_PipSize();
   double minGap = FVG_MIN_PIPS * pip;
   double maxGap = FVG_MAX_PIPS * pip;

   // Primary: bars [3,1]
   double gap = r15[1].low - r15[3].high;
   if(gap >= minGap && gap <= maxGap)
   {
      zone_h = r15[1].low;
      zone_l = r15[3].high;
      return (zone_h < r15[1].close);
   }

   // Secondary: bars [4,2]
   if(ArraySize(r15) >= 6)
   {
      gap = r15[2].low - r15[4].high;
      if(gap >= minGap && gap <= maxGap)
      {
         zone_h = r15[2].low;
         zone_l = r15[4].high;
         return (zone_h < r15[1].close);
      }
   }

   // Fallback: 2-bar gap [2,1]
   gap = r15[1].low - r15[2].high;
   if(gap >= minGap && gap <= maxGap)
   {
      zone_h = r15[1].low;
      zone_l = r15[2].high;
      return (zone_h < r15[1].close);
   }

   return false;
}

//--- True if price is within 8 pips of zone (not blown through it)
bool Zone_Intact_Buy(double price, double zone_l, int blownPips = 8)
{
   return price >= zone_l - blownPips * AWB_PipSize();
}

bool Zone_Intact_Sell(double price, double zone_h, int blownPips = 8)
{
   return price <= zone_h + blownPips * AWB_PipSize();
}
