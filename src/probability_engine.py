"""Quantitative engine for Black-Scholes risk-neutral probabilities, ATM straddle implied moves, delta levels, and probability distribution curves."""

import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)

class ImpliedProbabilityEngine:
    """Computes risk-neutral probability metrics from options chains."""

    def __init__(self, risk_free_rate: float = 0.045):
        self.risk_free_rate = risk_free_rate

    def calculate_expiration_metrics(
        self,
        symbol: str,
        spot_price: float,
        dividend_yield: float,
        expiration_date_str: str,
        calls_df: pd.DataFrame,
        puts_df: pd.DataFrame,
        include_full_distribution: bool = True,
        include_probability_matrix: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Calculate complete implied probability profile for a single expiration."""
        try:
            today = date.today()
            exp_date = datetime.strptime(expiration_date_str, "%Y-%m-%d").date()
            days_to_expiration = max((exp_date - today).days, 0.5)
            t_years = days_to_expiration / 365.0

            if spot_price <= 0:
                return None

            # Merge calls and puts on strike
            calls = calls_df.copy() if not calls_df.empty else pd.DataFrame(columns=['strike', 'midPrice', 'impliedVolatility'])
            puts = puts_df.copy() if not puts_df.empty else pd.DataFrame(columns=['strike', 'midPrice', 'impliedVolatility'])

            all_strikes = sorted(list(set(calls['strike'].tolist() + puts['strike'].tolist())))
            if not all_strikes:
                return None

            # Find ATM Strike
            atm_strike = min(all_strikes, key=lambda k: abs(k - spot_price))

            # Fetch ATM call and put
            atm_call = calls[calls['strike'] == atm_strike]
            atm_put = puts[puts['strike'] == atm_strike]

            atm_call_price = float(atm_call['midPrice'].iloc[0]) if not atm_call.empty else 0.0
            atm_put_price = float(atm_put['midPrice'].iloc[0]) if not atm_put.empty else 0.0
            straddle_price = atm_call_price + atm_put_price

            # ATM Implied Volatility
            atm_iv = 0.0
            if not atm_call.empty and float(atm_call['impliedVolatility'].iloc[0]) > 0:
                atm_iv = float(atm_call['impliedVolatility'].iloc[0])
            elif not atm_put.empty and float(atm_put['impliedVolatility'].iloc[0]) > 0:
                atm_iv = float(atm_put['impliedVolatility'].iloc[0])
            
            # If still 0, estimate from straddle formula: Straddle ≈ 0.8 * S * sigma * sqrt(T)
            if atm_iv <= 0.001 and straddle_price > 0 and t_years > 0:
                atm_iv = straddle_price / (0.8 * spot_price * np.sqrt(t_years))
            
            if atm_iv <= 0.001:
                atm_iv = 0.25  # Fallback default 25%

            # 1. Implied Move from Straddle
            implied_move_usd = straddle_price
            implied_move_pct = (implied_move_usd / spot_price) * 100.0 if spot_price > 0 else 0.0
            upper_implied_bound = spot_price + implied_move_usd
            lower_implied_bound = max(spot_price - implied_move_usd, 0.01)

            # 2. Probability of Staying Inside Straddle Move
            prob_inside_move = self._prob_between_strikes(
                spot_price, lower_implied_bound, upper_implied_bound,
                t_years, atm_iv, self.risk_free_rate, dividend_yield
            )

            # 3. 16-Delta (1-Sigma, 68% CI) & 30-Delta (0.5-Sigma, 40% CI) & 50-Delta Strike Bounds
            # Analytical strike formula: K = S * exp( (r - q - 0.5*sigma^2)*T - sigma*sqrt(T)*Phi_inv(p) )
            sigma_sqrt_t = atm_iv * np.sqrt(t_years)
            drift = (self.risk_free_rate - dividend_yield - 0.5 * (atm_iv ** 2)) * t_years

            # 16% Above (1-Sigma Upper): Phi_inv(0.16) ≈ -0.99445788
            z_16 = norm.ppf(0.16)
            strike_upper_16d = spot_price * np.exp(drift - sigma_sqrt_t * z_16)
            # 84% Above / 16% Below (1-Sigma Lower): Phi_inv(0.84) ≈ +0.99445788
            z_84 = norm.ppf(0.84)
            strike_lower_16d = spot_price * np.exp(drift - sigma_sqrt_t * z_84)

            # 30% Above (0.5-Sigma Upper): Phi_inv(0.30) ≈ -0.52440051
            z_30 = norm.ppf(0.30)
            strike_upper_30d = spot_price * np.exp(drift - sigma_sqrt_t * z_30)
            # 70% Above / 30% Below (0.5-Sigma Lower): Phi_inv(0.70) ≈ +0.52440051
            z_70 = norm.ppf(0.70)
            strike_lower_30d = spot_price * np.exp(drift - sigma_sqrt_t * z_70)

            # 50% Above (Risk-Neutral Median / 50-Delta): Phi_inv(0.50) = 0
            strike_50d = spot_price * np.exp(drift)

            # 4. Target Probability Matrix (13 tiers)
            target_probabilities = [0.10, 0.16, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70, 0.75, 0.80, 0.84, 0.90]
            prob_matrix = []
            if include_probability_matrix:
                for target_p in target_probabilities:
                    z_p = norm.ppf(target_p)
                    calc_strike = spot_price * np.exp(drift - sigma_sqrt_t * z_p)
                    nearest_mkt_strike = min(all_strikes, key=lambda k: abs(k - calc_strike))
                    dist_usd = calc_strike - spot_price
                    dist_pct = (dist_usd / spot_price) * 100.0

                    prob_matrix.append({
                        "targetProbAbovePct": round(target_p * 100.0, 1),
                        "targetProbBelowPct": round((1.0 - target_p) * 100.0, 1),
                        "theoreticalStrike": round(float(calc_strike), 2),
                        "nearestMarketStrike": round(float(nearest_mkt_strike), 2),
                        "distanceFromSpotUsd": round(float(dist_usd), 2),
                        "distanceFromSpotPct": round(float(dist_pct), 2),
                        "marketBias": "OTM Call / Bullish" if calc_strike > spot_price else ("OTM Put / Bearish" if calc_strike < spot_price else "ATM")
                    })

            # 5. Full Strike Distribution Table
            strike_distribution = []
            if include_full_distribution:
                for strike in all_strikes:
                    c_row = calls[calls['strike'] == strike]
                    p_row = puts[puts['strike'] == strike]

                    c_mid = float(c_row['midPrice'].iloc[0]) if not c_row.empty else 0.0
                    p_mid = float(p_row['midPrice'].iloc[0]) if not p_row.empty else 0.0

                    # Strike IV
                    s_iv = atm_iv
                    if not c_row.empty and float(c_row['impliedVolatility'].iloc[0]) > 0:
                        s_iv = float(c_row['impliedVolatility'].iloc[0])
                    elif not p_row.empty and float(p_row['impliedVolatility'].iloc[0]) > 0:
                        s_iv = float(p_row['impliedVolatility'].iloc[0])

                    s_sigma_sqrt_t = s_iv * np.sqrt(t_years)
                    if s_sigma_sqrt_t > 0:
                        d1 = (np.log(spot_price / strike) + (self.risk_free_rate - dividend_yield + 0.5 * (s_iv ** 2)) * t_years) / s_sigma_sqrt_t
                        d2 = d1 - s_sigma_sqrt_t
                    else:
                        d1 = 10.0 if spot_price > strike else -10.0
                        d2 = d1

                    prob_above = float(norm.cdf(d2))
                    prob_below = float(1.0 - prob_above)
                    call_delta = float(np.exp(-dividend_yield * t_years) * norm.cdf(d1))
                    put_delta = float(-np.exp(-dividend_yield * t_years) * norm.cdf(-d1))

                    # Touch Probability (reflection principle approximation)
                    if strike >= spot_price:
                        prob_touch = min(2.0 * prob_above, 1.0)
                    else:
                        prob_touch = min(2.0 * prob_below, 1.0)

                    # Risk-neutral PDF density: f(K) = phi(d2) / (K * sigma * sqrt(T))
                    if s_sigma_sqrt_t > 0 and strike > 0:
                        pdf_density = float(norm.pdf(d2) / (strike * s_sigma_sqrt_t))
                    else:
                        pdf_density = 0.0

                    strike_distribution.append({
                        "strike": round(float(strike), 2),
                        "distanceFromSpotUsd": round(float(strike - spot_price), 2),
                        "distanceFromSpotPct": round(float(((strike - spot_price) / spot_price) * 100.0), 2),
                        "callMidPrice": round(c_mid, 2),
                        "putMidPrice": round(p_mid, 2),
                        "impliedVolatilityPct": round(s_iv * 100.0, 2),
                        "probAboveStrikePct": round(prob_above * 100.0, 2),
                        "probBelowStrikePct": round(prob_below * 100.0, 2),
                        "probTouchStrikePct": round(prob_touch * 100.0, 2),
                        "callDelta": round(call_delta, 4),
                        "putDelta": round(put_delta, 4),
                        "riskNeutralDensity": round(pdf_density, 6)
                    })

            result = {
                "symbol": symbol,
                "spotPrice": spot_price,
                "expirationDate": expiration_date_str,
                "daysToExpiration": days_to_expiration,
                "timeToExpirationYears": round(t_years, 4),
                "atmStrike": round(float(atm_strike), 2),
                "atmCallPrice": round(atm_call_price, 2),
                "atmPutPrice": round(atm_put_price, 2),
                "atmStraddlePrice": round(straddle_price, 2),
                "atmImpliedVolatilityPct": round(atm_iv * 100.0, 2),
                "impliedMoveUsd": round(implied_move_usd, 2),
                "impliedMovePct": round(implied_move_pct, 2),
                "upperImpliedBound": round(upper_implied_bound, 2),
                "lowerImpliedBound": round(lower_implied_bound, 2),
                "probInsideMovePct": round(prob_inside_move * 100.0, 2),
                "probOutsideMovePct": round((1.0 - prob_inside_move) * 100.0, 2),
                "delta16UpperStrike": round(float(strike_upper_16d), 2),
                "delta16LowerStrike": round(float(strike_lower_16d), 2),
                "confidenceInterval68Pct": [round(float(strike_lower_16d), 2), round(float(strike_upper_16d), 2)],
                "delta30UpperStrike": round(float(strike_upper_30d), 2),
                "delta30LowerStrike": round(float(strike_lower_30d), 2),
                "confidenceInterval40Pct": [round(float(strike_lower_30d), 2), round(float(strike_upper_30d), 2)],
                "riskNeutralMedianStrike": round(float(strike_50d), 2),
                "targetProbabilityMatrix": prob_matrix,
                "strikeDistribution": strike_distribution
            }

            return result

        except Exception as e:
            logger.error(f"Error calculating expiration metrics for {symbol} on {expiration_date_str}: {e}", exc_info=True)
            return None

    def _prob_between_strikes(
        self,
        spot: float,
        k_lower: float,
        k_upper: float,
        t_years: float,
        sigma: float,
        r: float,
        q: float
    ) -> float:
        """Calculate probability that price stays between k_lower and k_upper at expiration."""
        if t_years <= 0 or sigma <= 0 or spot <= 0:
            return 0.682  # default 1-sigma

        sigma_sqrt_t = sigma * np.sqrt(t_years)
        drift = (r - q - 0.5 * (sigma ** 2)) * t_years

        d2_lower = (np.log(spot / k_lower) + drift) / sigma_sqrt_t
        d2_upper = (np.log(spot / k_upper) + drift) / sigma_sqrt_t

        prob_above_lower = norm.cdf(d2_lower)
        prob_above_upper = norm.cdf(d2_upper)

        prob_inside = prob_above_lower - prob_above_upper
        return float(np.clip(prob_inside, 0.0, 1.0))
