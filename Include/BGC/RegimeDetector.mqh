//+------------------------------------------------------------------+
//| RegimeDetector.mqh — CUSUM-based Market Regime Classifier        |
//| Detects RANGING vs TRENDING states via cumulative-sum statistics  |
//| and measures price deviation from a rolling EMA+StdDev envelope. |
//+------------------------------------------------------------------+
#pragma once

enum ENUM_MARKET_REGIME
{
   REGIME_RANGING       =  0,
   REGIME_TRENDING_UP   =  1,
   REGIME_TRENDING_DOWN = -1
};

class CRegimeDetector
{
private:
   //--- indicator handles
   int    m_ema_handle;
   int    m_atr_handle;

   //--- configuration
   string          m_symbol;
   ENUM_TIMEFRAMES m_tf;
   int             m_ema_period;
   int             m_atr_period;
   double          m_cusum_threshold;   // h — detection threshold (sigma multiples)
   double          m_cusum_allowance;   // k — slack allowance (typically 0.5)
   double          m_dev_sigma;         // signal trigger in std-dev units

   //--- CUSUM state
   double m_cusum_pos;
   double m_cusum_neg;

   //--- rolling std-dev window (same length as EMA)
   double m_price_buf[20];
   int    m_buf_pos;
   int    m_buf_count;

   //--- cached outputs
   double              m_atr;
   double              m_ema;
   double              m_std;
   ENUM_MARKET_REGIME  m_regime;

   //--------------------------------------------------------------------
   double RollingStd(double new_price)
   {
      m_price_buf[m_buf_pos % m_ema_period] = new_price;
      m_buf_pos++;
      if(m_buf_count < m_ema_period) m_buf_count++;

      if(m_buf_count < 2) return 1e-10;

      double sum = 0.0, sumsq = 0.0;
      int n = m_buf_count;
      for(int i = 0; i < n; i++) { sum += m_price_buf[i]; sumsq += m_price_buf[i] * m_price_buf[i]; }
      double mean = sum / n;
      double var  = (sumsq / n) - mean * mean;
      return (var > 0.0) ? MathSqrt(var) : 1e-10;
   }

   //--------------------------------------------------------------------
   void UpdateCUSUM(double z_score)
   {
      // Page's CUSUM: accumulates standardised innovation above/below k
      m_cusum_pos = MathMax(0.0, m_cusum_pos + z_score - m_cusum_allowance);
      m_cusum_neg = MathMax(0.0, m_cusum_neg - z_score - m_cusum_allowance);
   }

public:
   CRegimeDetector()
      : m_ema_handle(INVALID_HANDLE), m_atr_handle(INVALID_HANDLE),
        m_cusum_pos(0.0), m_cusum_neg(0.0),
        m_buf_pos(0), m_buf_count(0),
        m_atr(0.0), m_ema(0.0), m_std(1e-10),
        m_regime(REGIME_RANGING) {}

   ~CRegimeDetector() { Deinit(); }

   //--------------------------------------------------------------------
   bool Init(string symbol, ENUM_TIMEFRAMES tf,
             int    ema_period      = 20,
             int    atr_period      = 14,
             double cusum_threshold = 4.0,
             double cusum_allowance = 0.5,
             double dev_sigma       = 2.0)
   {
      m_symbol           = symbol;
      m_tf               = tf;
      m_ema_period       = MathMax(2, ema_period);
      m_atr_period       = MathMax(1, atr_period);
      m_cusum_threshold  = cusum_threshold;
      m_cusum_allowance  = cusum_allowance;
      m_dev_sigma        = dev_sigma;

      ArrayInitialize(m_price_buf, 0.0);

      m_ema_handle = iMA(symbol, tf, m_ema_period, 0, MODE_EMA, PRICE_CLOSE);
      m_atr_handle = iATR(symbol, tf, m_atr_period);

      if(m_ema_handle == INVALID_HANDLE || m_atr_handle == INVALID_HANDLE)
      {
         Print("RegimeDetector: indicator handle creation failed");
         return false;
      }
      return true;
   }

   //--------------------------------------------------------------------
   void Deinit()
   {
      if(m_ema_handle != INVALID_HANDLE) { IndicatorRelease(m_ema_handle); m_ema_handle = INVALID_HANDLE; }
      if(m_atr_handle != INVALID_HANDLE) { IndicatorRelease(m_atr_handle); m_atr_handle = INVALID_HANDLE; }
   }

   //--------------------------------------------------------------------
   bool Update()
   {
      double ema_buf[2], atr_buf[2];

      if(CopyBuffer(m_ema_handle, 0, 0, 2, ema_buf) < 2) return false;
      if(CopyBuffer(m_atr_handle, 0, 0, 2, atr_buf) < 2) return false;

      m_ema = ema_buf[0];
      m_atr = atr_buf[0];

      double price = iClose(m_symbol, m_tf, 0);
      m_std = RollingStd(price);

      double z = (m_std > 1e-10) ? (price - m_ema) / m_std : 0.0;
      UpdateCUSUM(z);

      // Classify regime
      if(m_cusum_pos >= m_cusum_threshold)
      {
         m_regime = REGIME_TRENDING_UP;
      }
      else if(m_cusum_neg >= m_cusum_threshold)
      {
         m_regime = REGIME_TRENDING_DOWN;
      }
      else
      {
         m_regime = REGIME_RANGING;
      }

      return true;
   }

   //--------------------------------------------------------------------
   // Returns true when price is 'sigma' std-devs from EMA.
   // direction: +1 = overextended above (sell signal), -1 = below (buy signal)
   bool IsPriceExtended(double price, int &direction)
   {
      if(m_std < 1e-10) return false;
      double z = (price - m_ema) / m_std;
      if(z >=  m_dev_sigma) { direction =  1; return true; }
      if(z <= -m_dev_sigma) { direction = -1; return true; }
      direction = 0;
      return false;
   }

   //--------------------------------------------------------------------
   // Hard reset of CUSUM accumulators — call after a cycle close
   void Reset()
   {
      m_cusum_pos = 0.0;
      m_cusum_neg = 0.0;
      m_regime    = REGIME_RANGING;
   }

   //--- accessors
   ENUM_MARKET_REGIME GetRegime()   const { return m_regime; }
   double             GetATR()      const { return m_atr; }
   double             GetEMA()      const { return m_ema; }
   double             GetStdDev()   const { return m_std; }
   double             GetCUSUMPos() const { return m_cusum_pos; }
   double             GetCUSUMNeg() const { return m_cusum_neg; }
};
