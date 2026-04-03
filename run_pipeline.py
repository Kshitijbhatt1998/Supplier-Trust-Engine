"""
Full Pipeline Orchestrator

Runs the complete pipeline in sequence:
1. Scrape ImportYeti (supplier discovery + shipment data)
2. Verify certifications (OEKO-TEX + GOTS)
3. Engineer features
4. Score all suppliers (requires trained model)

Usage:
  python run_pipeline.py --scrape           # Step 1 only
  python run_pipeline.py --verify           # Step 2 only
  python run_pipeline.py --score            # Steps 3+4
  python run_pipeline.py --all              # Full pipeline
  python run_pipeline.py --train            # Train model (after labeling)
"""

import asyncio
import argparse
from dotenv import load_dotenv
from loguru import logger

load_dotenv()


async def run_scraper():
    from pipeline.spiders.importyeti_scraper import ImportYetiScraper
    scraper = ImportYetiScraper()
    await scraper.run(max_per_code=25)


async def run_verifier():
    from pipeline.verifiers.certification_verifier import verify_all_suppliers
    await verify_all_suppliers(limit=100)


def run_scoring():
    from model.scorer import score_all_and_store
    score_all_and_store()


def run_training():
    from model.scorer import train
    train()


async def main():
    parser = argparse.ArgumentParser(description="Textile Supplier Trust Engine Pipeline")
    parser.add_argument("--scrape", action="store_true", help="Run ImportYeti scraper")
    parser.add_argument("--verify", action="store_true", help="Run certification verifier")
    parser.add_argument("--score",  action="store_true", help="Score all suppliers")
    parser.add_argument("--train",  action="store_true", help="Train the model")
    parser.add_argument("--all",    action="store_true", help="Run full pipeline")
    args = parser.parse_args()

    if args.all or args.scrape:
        logger.info("=== Step 1: Scraping ImportYeti ===")
        await run_scraper()

    if args.all or args.verify:
        logger.info("=== Step 2: Verifying Certifications ===")
        await run_verifier()

    if args.train:
        logger.info("=== Training Model ===")
        run_training()

    if args.all or args.score:
        logger.info("=== Step 3: Scoring Suppliers ===")
        run_scoring()

    logger.success("Pipeline complete.")


if __name__ == "__main__":
    asyncio.run(main())
