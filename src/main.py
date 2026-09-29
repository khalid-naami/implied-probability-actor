"""Main entrypoint for the Implied Probability Intelligence Actor."""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Dict, List, Any
from apify import Actor

from src.data_engine import OptionsDataEngine
from src.probability_engine import ImpliedProbabilityEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("implied-probability-actor")


async def main() -> None:
    """Actor main workflow function."""
    async with Actor:
        actor_input = await Actor.get_input() or {}

        # Parse inputs
        raw_symbols = actor_input.get("symbols", ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"])
        if isinstance(raw_symbols, str):
            symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
        else:
            symbols = [str(s).strip().upper() for s in raw_symbols if str(s).strip()]

        if not symbols:
            symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]

        risk_free_rate = float(actor_input.get("riskFreeRate", 0.045))
        include_full_dist = bool(actor_input.get("includeFullDistribution", True))
        include_prob_matrix = bool(actor_input.get("includeProbabilityMatrix", True))
        max_expirations = int(actor_input.get("maxExpirationsPerSymbol", 4))

        logger.info(f"Starting Implied Probability Intelligence Actor...")
        logger.info(f"Target symbols: {symbols}")
        logger.info(f"Risk-free rate: {risk_free_rate * 100:.2f}%, Max Expirations: {max_expirations}")

        data_engine = OptionsDataEngine(risk_free_rate=risk_free_rate)
        prob_engine = ImpliedProbabilityEngine(risk_free_rate=risk_free_rate)

        all_results: List[Dict[str, Any]] = []
        dataset_records: List[Dict[str, Any]] = []

        for symbol in symbols:
            try:
                sym_data = data_engine.get_symbol_data(symbol, max_expirations=max_expirations)
                if not sym_data:
                    logger.warning(f"Could not retrieve options data for {symbol}. Skipping.")
                    continue

                spot_price = sym_data["spotPrice"]
                div_yield = sym_data["dividendYield"]
                chains = sym_data["chains"]

                symbol_expirations = []

                for chain_item in chains:
                    exp_date = chain_item["expirationDate"]
                    calls_df = chain_item["calls"]
                    puts_df = chain_item["puts"]

                    metrics = prob_engine.calculate_expiration_metrics(
                        symbol=symbol,
                        spot_price=spot_price,
                        dividend_yield=div_yield,
                        expiration_date_str=exp_date,
                        calls_df=calls_df,
                        puts_df=puts_df,
                        include_full_distribution=include_full_dist,
                        include_probability_matrix=include_prob_matrix
                    )

                    if metrics:
                        symbol_expirations.append(metrics)
                        
                        # Flat record for Dataset table view
                        dataset_records.append({
                            "symbol": symbol,
                            "spotPrice": spot_price,
                            "expirationDate": exp_date,
                            "daysToExpiration": metrics["daysToExpiration"],
                            "atmStrike": metrics["atmStrike"],
                            "atmImpliedVolatilityPct": metrics["atmImpliedVolatilityPct"],
                            "atmStraddlePrice": metrics["atmStraddlePrice"],
                            "impliedMoveUsd": metrics["impliedMoveUsd"],
                            "impliedMovePct": metrics["impliedMovePct"],
                            "lowerImpliedBound": metrics["lowerImpliedBound"],
                            "upperImpliedBound": metrics["upperImpliedBound"],
                            "probInsideMovePct": metrics["probInsideMovePct"],
                            "delta16LowerStrike": metrics["delta16LowerStrike"],
                            "delta16UpperStrike": metrics["delta16UpperStrike"],
                            "delta30LowerStrike": metrics["delta30LowerStrike"],
                            "delta30UpperStrike": metrics["delta30UpperStrike"],
                            "riskNeutralMedianStrike": metrics["riskNeutralMedianStrike"],
                            "updatedAt": datetime.now(timezone.utc).isoformat()
                        })

                if symbol_expirations:
                    all_results.append({
                        "symbol": symbol,
                        "spotPrice": spot_price,
                        "dividendYieldPct": round(div_yield * 100.0, 2),
                        "riskFreeRatePct": round(risk_free_rate * 100.0, 2),
                        "expirationsCount": len(symbol_expirations),
                        "expirations": symbol_expirations
                    })

            except Exception as e:
                logger.error(f"Error processing symbol {symbol}: {e}", exc_info=True)

        # Push to Apify Dataset
        if dataset_records:
            logger.info(f"Pushing {len(dataset_records)} records to Apify default dataset...")
            await Actor.push_data(dataset_records)
        else:
            logger.warning("No records generated to push to dataset.")

        # Save comprehensive summary to Key-Value Store OUTPUT
        output_payload = {
            "title": "Options Implied Probability & Expected Move Intelligence",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbolsAnalyzed": len(all_results),
            "totalExpirationsAnalyzed": len(dataset_records),
            "results": all_results
        }
        await Actor.set_value("OUTPUT", output_payload)

        logger.info(f"Implied Probability Actor completed successfully! Processed {len(all_results)} symbols across {len(dataset_records)} expiration cycles.")


if __name__ == "__main__":
    asyncio.run(main())
