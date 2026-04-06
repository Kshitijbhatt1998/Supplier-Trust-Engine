#!/bin/bash
set -e

echo "=== Supplier Trust Engine — startup ==="

python - <<'EOF'
from pipeline.storage.db import init_db
from loguru import logger

con = init_db()
count = con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
scored = con.execute("SELECT COUNT(*) FROM trust_scores").fetchone()[0]

if count == 0:
    logger.info("Empty DB — seeding synthetic suppliers...")
    from data.seed_suppliers import generate_and_seed
    generate_and_seed()

if scored == 0:
    logger.info("No scores — training model and scoring all suppliers...")
    from model.scorer import train, score_all_and_store
    train()
    score_all_and_store()

final = con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
logger.success(f"DB ready: {final} suppliers, {scored} scored")
EOF

exec uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
