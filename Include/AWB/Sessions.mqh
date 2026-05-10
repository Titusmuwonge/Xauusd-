//+------------------------------------------------------------------+
//| AWB Sessions.mqh                                                 |
//| EAT (UTC+3) session filtering and period separator drawing       |
//+------------------------------------------------------------------+
#pragma once

// Kampala / East Africa Time = UTC+3 (no DST)
#define EAT_OFFSET_SECONDS 10800

// London session: 10:00–12:00 EAT = 07:00–09:00 UTC
#define LONDON_START_EAT  10
#define LONDON_END_EAT    12

// New York session: 15:00–17:00 EAT = 12:00–14:00 UTC
#define NY_START_EAT      15
#define NY_END_EAT        17

//--- Convert broker server time to EAT hour
int ToEATHour(datetime serverTime)
{
   // TimeGMT() gives current UTC; we adjust server time to UTC first using the
   // server's GMT offset, then add EAT offset.
   // Use MQL5 built-in: TimeGMTOffset() returns seconds offset of server from UTC
   datetime utc = serverTime - TimeGMTOffset();
   MqlDateTime dt;
   TimeToStruct(utc + EAT_OFFSET_SECONDS, dt);
   return dt.hour;
}

int ToEATMinute(datetime serverTime)
{
   datetime utc = serverTime - TimeGMTOffset();
   MqlDateTime dt;
   TimeToStruct(utc + EAT_OFFSET_SECONDS, dt);
   return dt.min;
}

//--- Returns true if current time is within London or NY session (EAT)
bool IsInTradingSession(datetime serverTime = 0)
{
   if(serverTime == 0) serverTime = TimeCurrent();
   int h = ToEATHour(serverTime);
   return ((h >= LONDON_START_EAT && h < LONDON_END_EAT) ||
           (h >= NY_START_EAT     && h < NY_END_EAT));
}

bool IsLondonSession(datetime serverTime = 0)
{
   if(serverTime == 0) serverTime = TimeCurrent();
   int h = ToEATHour(serverTime);
   return (h >= LONDON_START_EAT && h < LONDON_END_EAT);
}

bool IsNYSession(datetime serverTime = 0)
{
   if(serverTime == 0) serverTime = TimeCurrent();
   int h = ToEATHour(serverTime);
   return (h >= NY_START_EAT && h < NY_END_EAT);
}

//--- Draw a vertical period separator at a given datetime
void DrawPeriodSeparator(string name, datetime time, color clr = clrDimGray)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_VLINE, 0, time, 0);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DOT);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   }
}

//--- Draw period separators for the visible chart range (London + NY opens)
void DrawAllPeriodSeparators(int barsBack = 500)
{
   MqlRates rates[];
   int copied = CopyRates(_Symbol, PERIOD_M5, 0, barsBack, rates);
   if(copied <= 0) return;

   string drawnDays[];
   ArrayResize(drawnDays, 0);

   for(int i = 0; i < copied; i++)
   {
      int h   = ToEATHour(rates[i].time);
      int m   = ToEATMinute(rates[i].time);

      // Mark London open (10:00 EAT)
      if(h == LONDON_START_EAT && m == 0)
      {
         string name = "AWB_LON_" + TimeToString(rates[i].time);
         DrawPeriodSeparator(name, rates[i].time, clrRoyalBlue);
      }
      // Mark NY open (15:00 EAT)
      if(h == NY_START_EAT && m == 0)
      {
         string name = "AWB_NY_" + TimeToString(rates[i].time);
         DrawPeriodSeparator(name, rates[i].time, clrOrangeRed);
      }
   }
}
