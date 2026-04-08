"""
Shopify Connector Plugin — Supplier Trust Engine V2

Reads product vendor names from a Shopify store, matches them to scored
suppliers in DuckDB, and returns a trust-annotated vendor manifest.

In a production integration this would:
  1. Call the Shopify Admin REST or GraphQL API to list products/vendors.
  2. For each vendor, call EntityResolver to find the canonical supplier.
  3. Write the trust score back to Shopify product metafields via a PATCH.

For now, the sync_vendors() method is a functional mockup that demonstrates
the data flow and the schema of the response — no live Shopify credentials
are required.
"""

from __future__ import annotations

import json
from typing import Optional
from loguru import logger


class ShopifyConnector:
    """
    Wraps the Supplier Trust Engine ↔ Shopify integration.

    Parameters
    ----------
    shop_url     : Shopify myshopify domain, e.g. "acme.myshopify.com"
    access_token : Shopify Admin API access token (private app or OAuth)
    db           : Live DuckDB connection from app.state.db
    """

    def __init__(self, shop_url: str, access_token: str, db) -> None:
        self.shop_url = shop_url
        self.access_token = access_token
        self.db = db

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def sync_vendors(self) -> dict:
        """
        Main entry point called by POST /v1/integrations/shopify/sync.

        Returns a summary dict with per-vendor trust scores.
        """
        logger.info(f"Shopify sync starting for {self.shop_url}")

        vendors = self._fetch_vendors()
        results = []

        for vendor in vendors:
            score_row = self._lookup_trust_score(vendor)
            results.append({
                "vendor":      vendor,
                "supplier_id": score_row.get("supplier_id"),
                "trust_score": score_row.get("trust_score"),
                "risk_flags":  score_row.get("risk_flags", []),
                "matched":     score_row.get("supplier_id") is not None,
            })
            if score_row.get("supplier_id"):
                self._write_metafield(vendor, score_row["trust_score"])

        matched   = sum(1 for r in results if r["matched"])
        unmatched = len(results) - matched

        logger.info(f"Shopify sync complete: {matched} matched, {unmatched} unmatched")
        return {
            "status":          "success",
            "shop":            self.shop_url,
            "vendors_found":   len(results),
            "vendors_matched": matched,
            "vendors":         results,
        }

    # ------------------------------------------------------------------ #
    # Private helpers                                                       #
    # ------------------------------------------------------------------ #

    def _fetch_vendors(self) -> list[str]:
        """
        Return a deduplicated list of product vendor names from Shopify.

        Production: GET /admin/api/2024-01/products.json?fields=vendor
        Mockup: returns a realistic fixed list so the integration can be
        demonstrated without live credentials.
        """
        # TODO: Replace with actual Shopify API call once credentials are wired:
        #   import httpx
        #   resp = httpx.get(
        #       f"https://{self.shop_url}/admin/api/2024-01/products.json",
        #       headers={"X-Shopify-Access-Token": self.access_token},
        #       params={"fields": "vendor", "limit": 250},
        #   )
        #   products = resp.json().get("products", [])
        #   return list({p["vendor"] for p in products if p.get("vendor")})
        return [
            "Shahi Exports",
            "Orient Craft Ltd",
            "Welspun India",
            "Unknown Vendor Co",
        ]

    def _lookup_trust_score(self, vendor_name: str) -> dict:
        """
        Fuzzy-match a vendor name to a canonical supplier and return its score.
        Uses a simple ILIKE query for the mockup; EntityResolver would be used
        in production for full fuzzy matching.
        """
        row = self.db.execute("""
            SELECT s.id, s.name, t.trust_score, t.shap_flags_json
            FROM suppliers s
            JOIN trust_scores t ON t.supplier_id = s.id
            WHERE s.name ILIKE ?
            LIMIT 1
        """, [f"%{vendor_name}%"]).fetchone()

        if not row:
            return {}

        return {
            "supplier_id": row[0],
            "supplier_name": row[1],
            "trust_score": row[2],
            "risk_flags": json.loads(row[3]) if row[3] else [],
        }

    def _write_metafield(self, vendor: str, trust_score: Optional[float]) -> None:
        """
        Write the trust score back to Shopify as a product metafield.

        Production: PUT /admin/api/2024-01/products/{id}/metafields.json
        Mockup: logs the action only.
        """
        logger.debug(
            f"[mock] Writing metafield trust_score={trust_score} "
            f"for vendor '{vendor}' on {self.shop_url}"
        )
        # TODO: Real implementation:
        #   httpx.post(
        #       f"https://{self.shop_url}/admin/api/2024-01/products/{product_id}/metafields.json",
        #       headers={"X-Shopify-Access-Token": self.access_token},
        #       json={"metafield": {
        #           "namespace": "datavibe",
        #           "key":       "trust_score",
        #           "value":     str(trust_score),
        #           "type":      "number_decimal",
        #       }},
        #   )
