//+------------------------------------------------------------------+
//| AWB RiskManager.mqh                                              |
//| Dynamic lot sizing + order placement                             |
//+------------------------------------------------------------------+
#pragma once
#include <Trade\Trade.mqh>

//--- Calculate lot size so that SL_pips represents riskPct% of account balance
double CalcLotSize(double riskPct, int slPips, string symbol = NULL)
{
   if(symbol == NULL || symbol == "") symbol = _Symbol;

   double balance     = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount  = balance * (riskPct / 100.0);

   double tickSize    = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue   = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double point       = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int    digits      = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

   // pip value per lot = (pip_size / tick_size) * tick_value
   double pipSize     = (digits == 3 || digits == 5) ? point * 10 : point;
   double pipValue    = (pipSize / tickSize) * tickValue;

   if(pipValue <= 0) return 0.01;

   double lots = riskAmount / (slPips * pipValue);

   // Round to broker step
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   lots = MathFloor(lots / step) * step;

   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   lots = MathMax(minLot, MathMin(maxLot, lots));

   return lots;
}

//--- Convert pips to price distance
double PipsToPrice(int pips, string symbol = NULL)
{
   if(symbol == NULL || symbol == "") symbol = _Symbol;
   int    digits  = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double point   = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double pipSize = (digits == 3 || digits == 5) ? point * 10 : point;
   return pips * pipSize;
}

//--- Place a sell order
ulong PlaceSellOrder(CTrade &trade, double lots, int slPips, int tpPips, string symbol = NULL)
{
   if(symbol == NULL || symbol == "") symbol = _Symbol;
   double bid   = SymbolInfoDouble(symbol, SYMBOL_BID);
   double slDist = PipsToPrice(slPips, symbol);
   double tpDist = PipsToPrice(tpPips, symbol);
   double sl    = bid + slDist;
   double tp    = bid - tpDist;
   trade.Sell(lots, symbol, bid, sl, tp, "AWB_Sell");
   return trade.ResultOrder();
}

//--- Place a buy order
ulong PlaceBuyOrder(CTrade &trade, double lots, int slPips, int tpPips, string symbol = NULL)
{
   if(symbol == NULL || symbol == "") symbol = _Symbol;
   double ask   = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double slDist = PipsToPrice(slPips, symbol);
   double tpDist = PipsToPrice(tpPips, symbol);
   double sl    = ask - slDist;
   double tp    = ask + tpDist;
   trade.Buy(lots, symbol, ask, sl, tp, "AWB_Buy");
   return trade.ResultOrder();
}

//--- Count open positions for this EA (by magic number)
int CountOpenPositions(long magic, string symbol = NULL)
{
   if(symbol == NULL || symbol == "") symbol = _Symbol;
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == symbol &&
         PositionGetInteger(POSITION_MAGIC) == magic)
         count++;
   }
   return count;
}
