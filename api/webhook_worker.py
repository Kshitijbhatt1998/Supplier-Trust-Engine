import json
import hmac
import hashlib
import httpx
import pandas as pd
from loguru import logger
from typing import Dict, List

async def deliver_alerts(tenant_id: str, event_type: str, payload: Dict, con):
    """
    Fetch active webhooks for a tenant and deliver signed event payloads.
    """
    webhooks = con.execute("""
        SELECT url, secret, event_types 
        FROM webhooks 
        WHERE tenant_id = ? AND is_active = TRUE
    """, [tenant_id]).fetchall()
    
    if not webhooks:
        return

    logger.info(f"📡 Dispatching {event_type} webhook for tenant {tenant_id}...")

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url, secret, event_types_raw in webhooks:
            event_types = json.loads(event_types_raw)
            if event_type not in event_types:
                continue
                
            # Prepare payload with signature
            body = json.dumps({
                "event": event_type,
                "timestamp": pd.Timestamp.now().isoformat(),
                "data": payload
            })
            
            # HMAC-SHA256 signature
            signature = hmac.new(
                secret.encode(),
                body.encode(),
                hashlib.sha256
            ).hexdigest()
            
            headers = {
                "Content-Type": "application/json",
                "X-Vibe-Signature": signature,
                "X-Vibe-Event": event_type
            }
            
            try:
                resp = await client.post(url, content=body, headers=headers)
                if resp.status_code >= 400:
                    logger.warning(f"  Webhook delivery failed to {url}: {resp.status_code}")
                else:
                    logger.success(f"  Webhook delivered: {url}")
            except Exception as e:
                logger.error(f"  Webhook connection error: {url} -> {e}")

# Note: In a larger app, this would be a separate process consuming from a queue.
