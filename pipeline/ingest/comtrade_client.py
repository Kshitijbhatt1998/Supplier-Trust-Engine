import requests
import json
from loguru import logger
from typing import List, Dict, Optional
from datetime import datetime
from pipeline.storage.db import init_db, upsert_trade_stat

# UN M49 Country Codes
M49_MAP = {
    "Bangladesh": "050",
    "China": "156",
    "Germany": "276",
    "India": "356",
    "Italy": "380",
    "Pakistan": "586",
    "Portugal": "620",
    "Turkey": "792",
    "Vietnam": "704",
    "USA": "842",
    "World": "0",
}

PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

class ComtradeClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        # For public preview, no key is needed but limits apply (500 records)

    def fetch_annual_trade(
        self, 
        reporter_name: str, 
        partner_name: str = "World", 
        year: int = None, 
        hs_code: str = "TOTAL"
    ) -> List[Dict]:
        """Fetch annual trade data from UN Comtrade."""
        if not year:
            year = datetime.now().year - 1
        
        reporter_code = M49_MAP.get(reporter_name)
        partner_code = M49_MAP.get(partner_name)
        
        if not reporter_code:
            logger.error(f"Unknown reporter country: {reporter_name}")
            return []

        params = {
            "reporterCode": reporter_code,
            "partnerCode": partner_code,
            "period": str(year),
            "cmdCode": hs_code,
            "flowCode": "X", # Exports
        }

        try:
            logger.info(f"Fetching Comtrade data for {reporter_name} -> {partner_name} ({year}, HS: {hs_code})")
            response = requests.get(PREVIEW_URL, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            results = data.get("data", [])
            logger.info(f"Retrieved {len(results)} records from Comtrade")
            return results
        except Exception as e:
            logger.error(f"Comtrade API error: {e}")
            return []

    def ingest_to_db(self, reporter_name: str, hs_codes: List[str], years: List[int]):
        """Fetch and store trade stats in DuckDB."""
        con = init_db()
        for hs in hs_codes:
            for yr in years:
                records = self.fetch_annual_trade(reporter_name, year=yr, hs_code=hs)
                for rec in records:
                    # Map Comtrade response fields to our schema
                    stat = {
                        "reporter_code": str(rec.get("reporterCode")),
                        "partner_code": str(rec.get("partnerCode")),
                        "year": int(yr),
                        "hs_code": hs,
                        "trade_value_usd": float(rec.get("primaryValue", 0)),
                        "net_weight_kg": float(rec.get("netWeight", 0)),
                    }
                    upsert_trade_stat(con, stat)
        con.close()

if __name__ == "__main__":
    client = ComtradeClient()
    # Test ingestion for India textile codes
    client.ingest_to_db("India", ["6109", "6204", "6302"], [2022, 2023])
