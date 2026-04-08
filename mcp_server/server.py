"""
SourceGuard MCP Server

Exposes the SourceGuard Supplier Trust API as Model Context Protocol tools
so AI procurement agents (Claude Desktop, LangChain, etc.) can call them natively.

Tools:
  score_supplier          — Trust score + SHAP risk flags for a single supplier
  evaluate_procurement    — Ranked shortlist for a procurement category
  verify_grs_certificate  — Real-time GRS cert check via Textile Exchange
  list_suppliers          — Filtered list of scored suppliers

Setup:
  pip install mcp httpx
  export SOURCEGUARD_API_URL=http://localhost:8000/v1
  export SOURCEGUARD_API_KEY=your_key_here
  python -m mcp_server.server
"""

import asyncio
import json
import os

import httpx
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

# ── Config ──────────────────────────────────────────────────────────── #
API_BASE = os.getenv("SOURCEGUARD_API_URL", "http://localhost:8000/v1")
API_KEY  = os.getenv("SOURCEGUARD_API_KEY", "")

server = Server("sourceguard")


# ── Tool registry ────────────────────────────────────────────────────── #

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="score_supplier",
            description=(
                "Get the SourceGuard Trust Score (0–100) for a supplier, plus "
                "SHAP-driven risk flags explaining why. Use supplier_id for an "
                "exact lookup or supplier_name for fuzzy matching."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "supplier_id":   {"type": "string", "description": "Exact supplier slug ID"},
                    "supplier_name": {"type": "string", "description": "Company name (fuzzy matched)"},
                },
            },
        ),
        types.Tool(
            name="evaluate_procurement",
            description=(
                "Find and rank trusted suppliers for a procurement category. "
                "Applies hard filters (trust score, certifications, country, "
                "inactivity) and returns an approved shortlist with rationale. "
                "Use this when an agent needs to select a supplier to place an order."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Product category e.g. 'organic cotton tote bags'",
                    },
                    "min_trust_score": {
                        "type": "number",
                        "description": "Minimum trust score 0–100 (default 75)",
                    },
                    "required_certs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Required certs e.g. ['gots', 'oekotex']",
                    },
                    "country_prefer": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Preferred sourcing countries",
                    },
                    "country_exclude": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Excluded countries",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max suppliers to return (default 5, max 20)",
                    },
                },
                "required": ["category"],
            },
        ),
        types.Tool(
            name="verify_grs_certificate",
            description=(
                "Verify a Global Recycled Standard (GRS) certificate number in "
                "real-time by querying the Textile Exchange Integrity Database. "
                "Optionally links the result to an existing supplier record."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cert_number": {
                        "type": "string",
                        "description": "GRS certificate number e.g. 'CU123456GRS'",
                    },
                    "supplier_id": {
                        "type": "string",
                        "description": "Optional: supplier ID to link the result to",
                    },
                },
                "required": ["cert_number"],
            },
        ),
        types.Tool(
            name="list_suppliers",
            description=(
                "List scored suppliers from the SourceGuard database, optionally "
                "filtered by minimum trust score or country."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_score": {
                        "type": "number",
                        "description": "Minimum trust score filter 0–100 (default 0)",
                    },
                    "country": {
                        "type": "string",
                        "description": "Filter by country name (partial match)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20, max 100)",
                    },
                },
            },
        ),
        types.Tool(
            name="refresh_supplier",
            description=(
                "Trigger an on-demand re-scrape and re-score for a single supplier. "
                "Use this when you need the freshest data before making a procurement "
                "decision. Returns the updated trust score."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "supplier_id": {
                        "type": "string",
                        "description": "Supplier slug ID to refresh",
                    },
                },
                "required": ["supplier_id"],
            },
        ),
    ]


# ── Tool execution ───────────────────────────────────────────────────── #

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            if name == "score_supplier":
                resp = await client.post(
                    f"{API_BASE}/score", json=arguments, headers=headers
                )

            elif name == "evaluate_procurement":
                body = {
                    "category":          arguments["category"],
                    "min_trust_score":   arguments.get("min_trust_score", 75.0),
                    "required_certs":    arguments.get("required_certs", []),
                    "country_prefer":    arguments.get("country_prefer", []),
                    "country_exclude":   arguments.get("country_exclude", []),
                    "max_days_inactive": 365,
                    "max_results":       arguments.get("max_results", 5),
                }
                resp = await client.post(
                    f"{API_BASE}/procure/evaluate", json=body, headers=headers
                )

            elif name == "verify_grs_certificate":
                resp = await client.post(
                    f"{API_BASE}/verify/grs", json=arguments, headers=headers
                )

            elif name == "list_suppliers":
                params = {k: v for k, v in arguments.items() if v is not None}
                params.setdefault("limit", 20)
                resp = await client.get(
                    f"{API_BASE}/suppliers", params=params,
                    headers={"X-API-Key": API_KEY}
                )

            elif name == "refresh_supplier":
                supplier_id = arguments["supplier_id"]
                resp = await client.post(
                    f"{API_BASE}/suppliers/{supplier_id}/refresh",
                    headers=headers
                )

            else:
                return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

        except httpx.ConnectError:
            return [types.TextContent(
                type="text",
                text=(
                    f"Cannot connect to SourceGuard API at {API_BASE}. "
                    "Is the API running? Set SOURCEGUARD_API_URL if needed."
                ),
            )]

    if resp.status_code >= 400:
        return [types.TextContent(
            type="text",
            text=f"API error {resp.status_code}: {resp.text}",
        )]

    return [types.TextContent(type="text", text=json.dumps(resp.json(), indent=2))]


# ── Entrypoint ───────────────────────────────────────────────────────── #

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="sourceguard",
                server_version="2.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
