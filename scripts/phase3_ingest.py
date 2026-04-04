from pipeline.ingest.comtrade_client import ComtradeClient
from pipeline.ingest.manifest_scraper import ManifestFetcher
from model.scorer import train, score_all_and_store
from pipeline.storage.db import init_db
from loguru import logger

def run_phase3_ingest():
    logger.info("--- Starting Phase 3 Ingestion ---")
    
    # 1. Comtrade National Stats
    logger.info("Step 1: Fetching Comtrade National Stats...")
    com_client = ComtradeClient()
    countries = ["India", "China", "Bangladesh", "Turkey", "Vietnam", "Pakistan", "Portugal", "Italy"]
    hs_codes = ["6109", "6204", "6302"] # Basic textile chapters
    com_client.ingest_to_db(countries[0], hs_codes, [2022, 2023])
    # For many countries, the preview API might hit limits, so we'll just do a few for demo
    for c in countries[1:3]:
        com_client.ingest_to_db(c, hs_codes, [2023])

    # 2. Manifest Verification (Real-World Proof)
    logger.info("Step 2: verifying manifests for all suppliers...")
    fetcher = ManifestFetcher()
    con = init_db()
    supplier_ids = [r[0] for r in con.execute("SELECT id FROM suppliers").fetchall()]
    for sid in supplier_ids:
        fetcher.verify_supplier_manifests(sid)
    con.close()

    # 3. Retrain Model with new features
    logger.info("Step 3: Retraining model with new Verified Shipping features...")
    train(labeled_csv="data/labeled_suppliers.csv")

    # 4. Rescore everything
    logger.info("Step 4: Rescoring all suppliers...")
    score_all_and_store()
    
    logger.success("Phase 3 Ingestion & Scoring Complete!")

if __name__ == "__main__":
    run_phase3_ingest()
