//+------------------------------------------------------------------+
//| AWB RiskManager.mqh  v2                                          |
//| Lot sizing, order placement, breakeven management                |
//+------------------------------------------------------------------+
#pragma once
#include <Trade\Trade.mqh>

double AWB_PipSizeRM(string sym = NULL)
{
   if(sym == NULL || sym == "") sym = _Symbol;
   int d = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   return ((d == 3 || d == 5) ? SymbolInfoDouble(sym, SYMBOL_POINT) * 10.0
                               : SymbolInfoDouble(sym, SYMBOL_POINT));
}

//--- Lot size: risk riskPct% of balance on slPips stop
double CalcLotSize(double riskPct, int slPips, string symbol = NULL)
{
   if(symbol == NULL || symbol == "") symbol = _Symbol;

   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * (riskPct / 100.0);
   double tickSz     = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickVal    = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double pip        = AWB_PipSizeRM(symbol);
   double pipVal     = (tickSz > 0) ? (pip / tickSz) * tickVal : 0;

   if(pipVal <= 0) return SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);

   double lots = riskAmount / (slPips * pipVal);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   lots = MathFloor(lots / step) * step;
   lots = MathMax(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN),
                  MathMin(lots, SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX)));
   return lots;
}

double PipsToPrice(int pips, string sym = NULL)
{
   if(sym == NULL || sym == "") sym = _Symbol;
   return pips * AWB_PipSizeRM(sym);
}

//--- Place sell at market
ulong PlaceSellOrder(CTrade &trade, double lots, int slPips, int tpPips, string sym = NULL)
{
   if(sym == NULL || sym == "") sym = _Symbol;
   double bid   = SymbolInfoDouble(sym, SYMBOL_BID);
   double slDst = PipsToPrice(slPips, sym);
   double tpDst = PipsToPrice(tpPips, sym);
   trade.Sell(lots, sym, bid, bid + slDst, bid - tpDst, "AWB_Sell");
   return trade.ResultOrder();
}

//--- Place buy at market
ulong PlaceBuyOrder(CTrade &trade, double lots, int slPips, int tpPips, string sym = NULL)
{
   if(sym == NULL || sym == "") sym = _Symbol;
   double ask   = SymbolInfoDouble(sym, SYMBOL_ASK);
   double slDst = PipsToPrice(slPips, sym);
   double tpDst = PipsToPrice(tpPips, sym);
   trade.Buy(lots, sym, ask, ask - slDst, ask + tpDst, "AWB_Buy");
   return trade.ResultOrder();
}

//--- Move SL to breakeven (entry ± 1 pip) once price has moved slPips in our favour
//    Call on every tick while a position is open.
void ManageBreakeven(CTrade &trade, long magic, int slPips, string sym = NULL)
{
   if(sym == NULL || sym == "") sym = _Symbol;
   double pip = AWB_PipSizeRM(sym);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym)  continue;
      if(PositionGetInteger(POSITION_MAGIC)  != magic) continue;

      double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl     = PositionGetDouble(POSITION_SL);
      double tp     = PositionGetDouble(POSITION_TP);
      long   ptype  = PositionGetInteger(POSITION_TYPE);
      double beDist = slPips * pip;

      if(ptype == POSITION_TYPE_BUY)
      {
         double beLevel = entry + beDist;   // 1R above entry
         double newSL   = entry + pip;      // just above entry
         if(SymbolInfoDouble(sym, SYMBOL_BID) >= beLevel && sl < newSL - pip)
            trade.PositionModify(ticket, newSL, tp);
      }
      else // SELL
      {
         double beLevel = entry - beDist;
         double newSL   = entry - pip;
         if(SymbolInfoDouble(sym, SYMBOL_ASK) <= beLevel && sl > newSL + pip)
            trade.PositionModify(ticket, newSL, tp);
      }
   }
}

//--- Count open positions for this EA
int CountOpenPositions(long magic, string sym = NULL)
{
   if(sym == NULL || sym == "") sym = _Symbol;
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == sym &&
         PositionGetInteger(POSITION_MAGIC) == magic)
         count++;
   }
   return count;
}
