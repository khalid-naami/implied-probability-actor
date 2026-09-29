"""Data fetcher module for options chains and underlying asset quotes using yfinance."""

import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple, Any
import yfinance as yf
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class OptionsDataEngine:
    """Fetches real-time equity & options chain data."""

    def __init__(self, risk_free_rate: float = 0.045):
        self.risk_free_rate = risk_free_rate

    def get_symbol_data(self, symbol: str, max_expirations: int = 4) -> Optional[Dict[str, Any]]:
        """Fetch spot price, dividend yield, and active option chains for symbol."""
        clean_symbol = symbol.strip().upper()
        logger.info(f"Fetching options data for {clean_symbol}...")

        try:
            ticker = yf.Ticker(clean_symbol)
            
            # Fetch spot price
            spot_price = None
            try:
                fast_info = getattr(ticker, 'fast_info', None)
                if fast_info and hasattr(fast_info, 'last_price') and fast_info.last_price is not None:
                    spot_price = float(fast_info.last_price)
                elif hasattr(fast_info, 'previous_close') and fast_info.previous_close is not None:
                    spot_price = float(fast_info.previous_close)
            except Exception as e:
                logger.debug(f"fast_info failed for {clean_symbol}: {e}")

            if spot_price is None or spot_price <= 0:
                hist = ticker.history(period="5d")
                if not hist.empty:
                    spot_price = float(hist['Close'].iloc[-1])

            if spot_price is None or spot_price <= 0:
                info = ticker.info or {}
                spot_price = float(info.get('regularMarketPrice') or info.get('currentPrice') or info.get('previousClose') or 0.0)

            if spot_price <= 0:
                logger.error(f"Could not determine valid spot price for {clean_symbol}")
                return None

            # Dividend yield
            div_yield = 0.0
            try:
                info = ticker.info or {}
                raw_yield = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
                if raw_yield is not None:
                    div_yield = float(raw_yield)
                    if div_yield > 1.0:  # If expressed as percentage e.g. 1.5%
                        div_yield = div_yield / 100.0
            except Exception:
                div_yield = 0.0

            # Expiration dates
            expirations = list(ticker.options or [])
            if not expirations:
                logger.warning(f"No option expirations found for {clean_symbol}")
                return None

            # Filter valid future expirations
            today = date.today()
            valid_expirations = []
            for exp_str in expirations:
                try:
                    exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    if exp_d >= today:
                        valid_expirations.append(exp_str)
                except ValueError:
                    continue

            if not valid_expirations:
                logger.warning(f"No future option expirations for {clean_symbol}")
                return None

            selected_expirations = valid_expirations[:max_expirations]
            logger.info(f"Processing {len(selected_expirations)} expirations for {clean_symbol}: {selected_expirations}")

            chains = []
            for exp_date in selected_expirations:
                try:
                    chain = ticker.option_chain(exp_date)
                    calls_df = chain.calls.copy() if hasattr(chain, 'calls') else pd.DataFrame()
                    puts_df = chain.puts.copy() if hasattr(chain, 'puts') else pd.DataFrame()

                    if calls_df.empty and puts_df.empty:
                        continue

                    # Clean calls
                    calls_df = self._clean_option_df(calls_df, 'call')
                    puts_df = self._clean_option_df(puts_df, 'put')

                    chains.append({
                        "expirationDate": exp_date,
                        "calls": calls_df,
                        "puts": puts_df
                    })
                except Exception as ex:
                    logger.warning(f"Failed to fetch option chain for {clean_symbol} on {exp_date}: {ex}")

            if not chains:
                logger.warning(f"No valid option chains parsed for {clean_symbol}")
                return None

            return {
                "symbol": clean_symbol,
                "spotPrice": round(spot_price, 4),
                "dividendYield": round(div_yield, 5),
                "riskFreeRate": self.risk_free_rate,
                "chains": chains
            }

        except Exception as e:
            logger.error(f"Error fetching data for {clean_symbol}: {e}", exc_info=True)
            return None

    def _clean_option_df(self, df: pd.DataFrame, opt_type: str) -> pd.DataFrame:
        """Format and clean options dataframe."""
        if df.empty:
            return df

        cols = ['strike', 'lastPrice', 'bid', 'ask', 'impliedVolatility', 'volume', 'openInterest']
        for col in cols:
            if col not in df.columns:
                df[col] = 0.0

        df['strike'] = pd.to_numeric(df['strike'], errors='coerce')
        df['lastPrice'] = pd.to_numeric(df['lastPrice'], errors='coerce').fillna(0.0)
        df['bid'] = pd.to_numeric(df['bid'], errors='coerce').fillna(0.0)
        df['ask'] = pd.to_numeric(df['ask'], errors='coerce').fillna(0.0)
        df['impliedVolatility'] = pd.to_numeric(df['impliedVolatility'], errors='coerce').fillna(0.0)
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int)
        df['openInterest'] = pd.to_numeric(df['openInterest'], errors='coerce').fillna(0).astype(int)

        # Mid price calculation
        df['midPrice'] = np.where(
            (df['bid'] > 0) & (df['ask'] > 0),
            (df['bid'] + df['ask']) / 2.0,
            df['lastPrice']
        )
        df['optionType'] = opt_type
        return df.dropna(subset=['strike']).sort_values('strike').reset_index(drop=True)
