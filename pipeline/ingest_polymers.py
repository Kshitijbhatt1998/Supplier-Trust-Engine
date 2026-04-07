import duckdb
import uuid
import json
from loguru import logger
from api.chemical_normalizer import ChemicalNormalizer, cas_to_canonical_id

# Database path
DB_PATH = "data/trust_engine.duckdb"

# ------------------------------------------------------------------ #
# SEED DATA: PHASES 1-4                                               #
# ------------------------------------------------------------------ #

# Phase 1 & 2: The Giants and CAS Anchors
POLYMERS = [
    {"id": "sabic-global",   "name": "SABIC Innovative Plastics", "country": "Saudi Arabia", "category": "chemical", "trust": 98},
    {"id": "reliance-ind",   "name": "Reliance Industries Ltd",   "country": "India",        "category": "chemical", "trust": 95},
    {"id": "exxon-chem",     "name": "ExxonMobil Chemical",       "country": "USA",          "category": "chemical", "trust": 97},
    {"id": "formosa-plastics","name": "Formosa Plastics Corp",    "country": "Taiwan",       "category": "chemical", "trust": 92},
    
    # Generic CAS Anchors (Chemical Entities)
    {"id": cas_to_canonical_id("9002-88-4"), "name": "Polyethylene (PE/HDPE)", "category": "chemical", "trust": 85},
    {"id": cas_to_canonical_id("9002-86-2"), "name": "Polyvinyl Chloride (PVC)", "category": "chemical", "trust": 85},
    {"id": cas_to_canonical_id("9003-07-0"), "name": "Polypropylene (PP)", "category": "chemical", "trust": 85},
]

# Phase 3: Noise Priming (Common Misspellings to be "Strictly" Thresholded)
NOISE_PRIMING = [
    {"alias": "HDPE GRANULS", "canonical": cas_to_canonical_id("9002-88-4")},
    {"alias": "PVC RESIN K67", "canonical": cas_to_canonical_id("9002-86-2")},
    {"alias": "POLYETHYLENE HIGH DENSITY", "canonical": cas_to_canonical_id("9002-88-4")},
]

# Phase 4: Role Shield (Known Trader-Manufacturer Clusters to PRE-REJECT)
# This prevents XYZ Logistics from ever being aliased to SABIC.
ROLE_SHIELD = [
    {"alias": "XYZ LOGISTICS", "canonical": "sabic-global", "reason": "role_pollution_carrier"},
    {"alias": "MITSUBISHI CORP", "canonical": "sabic-global", "reason": "role_pollution_trader"},
    {"alias": "ABC TRADING", "canonical": "reliance-ind", "reason": "role_pollution_trader"},
]

def run_ingestion():
    logger.info(f"Starting Polymer Ingestion at {DB_PATH}")
    con = duckdb.connect(DB_PATH)
    norm = ChemicalNormalizer()

    try:
        # --- Phase 1 & 2: Upsert Giants & Anchors ---
        for p in POLYMERS:
            con.execute("""
                INSERT INTO suppliers (id, name, country, category, shipment_count)
                VALUES (?, ?, ?, ?, 1000)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, category = EXCLUDED.category
            """, [p['id'], p['name'], p['country'], p['category']])
            
            # Upsert Trust Score
            con.execute("""
                INSERT INTO trust_scores (supplier_id, trust_score, risk_probability, updated_at)
                VALUES (?, ?, ?, NOW())
                ON CONFLICT (supplier_id) DO UPDATE SET trust_score = EXCLUDED.trust_score
            """, [p['id'], p.get('trust', 80), 0.02])
            
            logger.debug(f"Seeded canonical: {p['name']} ({p['id']})")

        # --- Phase 3: Noise Priming (Intentional Rejection Buffering) ---
        # We seed the entity_rejections table to force a Strict threshold from day 1
        for noise in NOISE_PRIMING:
            normalized = norm.normalize(noise['alias'])
            # Create 10 dummy rejections to trigger Laplace penalty
            for i in range(10):
                con.execute("""
                    INSERT INTO entity_rejections (alias_normalized, canonical_id, reason_code)
                    VALUES (?, ?, 'noise_priming_warmup')
                    ON CONFLICT DO NOTHING
                """, [normalized, noise['canonical']])
            
            # Also register the alias as unverified to show it in the queue
            con.execute("""
                INSERT INTO entity_aliases (id, alias_name, alias_normalized, canonical_id, match_score, is_verified, category)
                VALUES (?, ?, ?, ?, 88.0, FALSE, 'chemical')
                ON CONFLICT DO NOTHING
            """, [uuid.uuid4().hex, noise['alias'], normalized, noise['canonical']])

        # --- Phase 4: Role Shield (Trader-Manufacturer Rejection) ---
        for shield in ROLE_SHIELD:
            normalized = norm.normalize(shield['alias'])
            con.execute("""
                INSERT INTO entity_rejections (alias_normalized, canonical_id, reason_code)
                VALUES (?, ?, ?)
                ON CONFLICT DO NOTHING
            """, [normalized, shield['canonical'], shield['reason']])
            logger.debug(f"Primed Role Shield: {shield['alias']} NOT {shield['canonical']}")

        logger.success("Polymer Ingestion Complete. Chemical Industry nodes and Role Shields are active.")

    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    run_ingestion()
