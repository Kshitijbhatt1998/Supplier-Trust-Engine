"""
Full Pipeline Orchestrator

Runs the complete pipeline in sequence:
1. Seed database with synthetic suppliers (dev/testing)
2. Scrape ImportYeti (supplier discovery + shipment data)
3. Verify certifications (OEKO-TEX + GOTS)
4. Train model (after labeling)
5. Score all suppliers

Usage:
  python run_pipeline.py --seed             # Generate 50 synthetic suppliers (no scraping)
  python run_pipeline.py --scrape           # Run ImportYeti scraper
  python run_pipeline.py --verify           # Run certification verifier
  python run_pipeline.py --train            # Train model (after seeding or labeling)
  python run_pipeline.py --score            # Score all suppliers
  python run_pipeline.py --all              # Full pipeline (scrape + verify + score)
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


def run_seed():
    from data.seed_suppliers import generate_and_seed
    generate_and_seed()


async def main():
    parser = argparse.ArgumentParser(description="SourceGuard Pipeline")
    parser.add_argument("--seed",   action="store_true", help="Seed DB with 50 synthetic suppliers (no scraping needed)")
    parser.add_argument("--scrape", action="store_true", help="Run ImportYeti scraper")
    parser.add_argument("--verify", action="store_true", help="Run certification verifier")
    parser.add_argument("--score",  action="store_true", help="Score all suppliers")
    parser.add_argument("--train",  action="store_true", help="Train the scoring model")
    parser.add_argument("--all",    action="store_true", help="Run full pipeline (scrape + verify + score)")
    args = parser.parse_args()

    if args.seed:
        logger.info("=== Seeding Synthetic Suppliers ===")
        run_seed()

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
