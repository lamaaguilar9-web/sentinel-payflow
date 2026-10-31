"""
Sentinel PayFlow — FastAPI Institutional Gateway & Interactive Console
Exposes REST endpoints for pre-flight Tornado Cash / OFAC screening,
MultiHopper programmable multi-hop route orchestration, and live traction metrics.
"""
import os
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import (
    DEFAULT_USDC_MINT,
    LEGAL_COMPLIANCE_NOTICE,
    MULTIHOPPER_BASE_URL,
    MULTIHOPPER_CORE_PROGRAM_ID,
    MULTIHOPPER_ENV,
    SENTINEL_PROVIDER_ID_HEX,
    SENTINEL_PROVIDER_ID_STR,
    SENTINEL_SOLANA_TREASURY,
)
from .payflow_engine import PayFlowEngine

app = FastAPI(
    title="Sentinel PayFlow — Institutional MultiHopper Routing & AML Shield",
    description=(
        "Non-custodial Solana programmable settlement gateway combining sub-millisecond "
        "Tornado Cash / OFAC / Anti-Wash pre-flight screening with MultiHopper non-commingled routing."
    ),
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

engine = PayFlowEngine()
STATIC_INDEX = os.path.join(os.path.dirname(__file__), "static", "index.html")


# ============================================================================
# Request Schemas
# ============================================================================
class DestinationItem(BaseModel):
    wallet: str = Field(..., description="32-byte Base58 Solana recipient wallet")
    amountRaw: str = Field(..., description="Exact integer base units (e.g., micro-USDC)")


class ComplianceScreenRequest(BaseModel):
    sourceOwner: str
    destinations: List[DestinationItem]
    tokenDecimals: int = Field(default=6, ge=0, le=18)
    tokenPriceUsd: str = Field(default="1.00")
    bridgeOriginEvm: Optional[str] = Field(
        default=None,
        description="Optional 0x EVM source wallet if funds arrived via cross-chain bridge",
    )


class CreateRouteRequest(BaseModel):
    sourceOwner: str
    destinations: List[DestinationItem]
    tokenMint: str = Field(default=DEFAULT_USDC_MINT)
    tokenDecimals: int = Field(default=6, ge=0, le=18)
    tokenSymbol: str = Field(default="USDC")
    tokenPriceUsd: str = Field(default="1.00")
    hops: int = Field(default=4, ge=3, le=10)
    arrivalSeconds: int = Field(default=30, ge=5, le=3600)
    bridgeOriginEvm: Optional[str] = None
    vertical: str = Field(default="CUSTOM_INSTITUTIONAL_ROUTE")
    referenceNote: str = Field(default="")


class ConfirmBroadcastRequest(BaseModel):
    signatures: List[str] = Field(..., min_length=1)


# ============================================================================
# Routes
# ============================================================================
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_dashboard():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return HTMLResponse("<h1>Sentinel PayFlow API Live</h1><p>Visit <a href='/docs'>/docs</a></p>")


@app.get("/health")
def health_probe() -> Dict[str, Any]:
    t0 = time.perf_counter_ns()
    sanctioned_count = engine.validator.total_sanctioned_entities
    latency_ms = round((time.perf_counter_ns() - t0) / 1_000_000, 4)
    return {
        "status": "healthy",
        "service": "sentinel-payflow",
        "version": __version__,
        "cluster": MULTIHOPPER_ENV,
        "multihopper_api_base": MULTIHOPPER_BASE_URL,
        "multihopper_core_program": MULTIHOPPER_CORE_PROGRAM_ID,
        "live_api_key_configured": engine.mh_client.is_live_api_configured,
        "provider_id": SENTINEL_PROVIDER_ID_STR,
        "provider_id_hex": SENTINEL_PROVIDER_ID_HEX,
        "custody_model": "NON_CUSTODIAL_CLIENT_SIGNED",
        "sanctions_db": {
            "total_entities": sanctioned_count,
            "evm_tornado_ofac_count": len(engine.validator.evm_blocked_lower),
            "solana_ofac_exploiter_count": len(engine.validator.solana_blocked),
            "lookup_complexity": "O(1)",
            "probe_latency_ms": latency_ms,
        },
        "treasury_receiver": SENTINEL_SOLANA_TREASURY,
        "legal_notice": LEGAL_COMPLIANCE_NOTICE,
    }


@app.post("/api/v1/compliance/screen")
def screen_route(req: ComplianceScreenRequest) -> Dict[str, Any]:
    dests = [{"wallet": d.wallet, "amountRaw": d.amountRaw} for d in req.destinations]
    total_raw = sum(int(d["amountRaw"]) for d in dests)
    return engine.validator.screen_transfer_route(
        source_owner=req.sourceOwner,
        destinations=dests,
        total_amount_raw=total_raw,
        token_decimals=req.tokenDecimals,
        token_price_usd=Decimal(req.tokenPriceUsd),
        bridge_origin_evm=req.bridgeOriginEvm,
    )


@app.post("/api/v1/routes/create")
def create_compliant_route(req: CreateRouteRequest) -> Dict[str, Any]:
    dests = [{"wallet": d.wallet, "amountRaw": d.amountRaw} for d in req.destinations]
    try:
        return engine.execute_compliant_route(
            source_owner=req.sourceOwner,
            destinations=dests,
            token_mint=req.tokenMint,
            token_decimals=req.tokenDecimals,
            token_symbol=req.tokenSymbol,
            token_price_usd=req.tokenPriceUsd,
            hops=req.hops,
            arrival_seconds=req.arrivalSeconds,
            bridge_origin_evm=req.bridgeOriginEvm,
            vertical=req.vertical,
            reference_note=req.referenceNote,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/routes/preset/{preset_id}")
def run_institutional_preset(preset_id: str) -> Dict[str, Any]:
    """
    One-click institutional demo scenarios for judges and auditors:
      - agro_coffee_export: $65,000 USDC Specialty Coffee Container Forward Settlement (90/8/2 split)
      - confidential_payroll: $38,400 USDC 5-Engineer Confidential Solana Payroll
      - cpa_vendor_batch: $24,750 USDC US CPA Overnight Multi-Vendor Settlement
      - attack_tornado_bridge: Simulated Tornado Cash Router (0x722122df...) Cross-Chain Taint -> FAIL-CLOSED 403
      - attack_solana_exploiter: Simulated Wormhole Exploiter Solana Wallet -> FAIL-CLOSED 403
      - attack_circular_wash: Simulated Self-Routing Wash Volume -> FAIL-CLOSED 403
    """
    emp_src = "7fUAJdStEuGbc3sM84cKRL6yYaaSstyLSU4ve5oovLS7"
    w1 = "DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy"
    w2 = "DYw8jCTfwHNRJhhmFcbXvVDTqWMEVFBX6ZKUmG5CNSKK"
    w3 = "4fYNw3dojWmQ4dXtSGE9epjRGy9pFSx62YypT7avPYvA"
    w4 = "83astBRguLMdt2h5U1Tpdq5tjFoJ6noeGwaY3mDLVcri"
    w5 = "6dM9A2k8tN3pW7vX9yZ2bC4eF6gH8jK1mN3pQ5rS7tV9"

    try:
        if preset_id == "agro_coffee_export":
            return engine.create_b2b_commodity_route(
                buyer_wallet=emp_src,
                contract_amount_usdc="65000.00",
                farmer_wallet=w1,
                mill_logistics_wallet=w2,
                hops=6,
            )
        if preset_id == "confidential_payroll":
            return engine.create_payroll_route(
                employer_wallet=emp_src,
                worker_payouts=[
                    {"recipient": w1, "amount_usdc": "9500.00"},
                    {"recipient": w2, "amount_usdc": "8200.00"},
                    {"recipient": w3, "amount_usdc": "7600.00"},
                    {"recipient": w4, "amount_usdc": "6800.00"},
                    {"recipient": w5, "amount_usdc": "6300.00"},
                ],
                hops=5,
            )
        if preset_id == "cpa_vendor_batch":
            return engine.create_cpa_vendor_batch_route(
                corporate_treasury_wallet=emp_src,
                vendor_invoices=[
                    {"vendor_wallet": w1, "amount_usdc": "11250.00"},
                    {"vendor_wallet": w2, "amount_usdc": "8400.00"},
                    {"vendor_wallet": w3, "amount_usdc": "5100.00"},
                ],
                hops=4,
            )
        if preset_id == "attack_tornado_bridge":
            return engine.create_b2b_commodity_route(
                buyer_wallet=emp_src,
                contract_amount_usdc="15000.00",
                farmer_wallet=w1,
                mill_logistics_wallet=w2,
                hops=4,
                bridge_origin_evm="0x722122df12d4e14e13ac3b6895a86e84145b6967",  # Tornado Cash Proxy V2
            )
        if preset_id == "attack_solana_exploiter":
            return engine.create_merchant_split_route(
                customer_wallet=emp_src,
                total_bill_usdc="2500.00",
                merchant_wallet="G2YxRa6wt1qePMwfJzdXZG62ej4qaTC7YURzuh2Lwd3t",  # Wormhole Exploiter
                driver_wallet=w2,
                hops=3,
            )
        if preset_id == "attack_circular_wash":
            return engine.execute_compliant_route(
                source_owner=emp_src,
                destinations=[{"wallet": emp_src, "amountRaw": "5000000000"}],
                hops=3,
                vertical="WASH_TRADING_TEST",
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    raise HTTPException(status_code=404, detail=f"Unknown preset '{preset_id}'")


@app.post("/api/v1/routes/{transfer_id}/confirm")
def confirm_route_broadcast(transfer_id: str, req: ConfirmBroadcastRequest) -> Dict[str, Any]:
    try:
        return engine.mh_client.confirm_broadcast(transfer_id, req.signatures)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/stats")
@app.get("/api/v1/traction")
def get_stats() -> Dict[str, Any]:
    return engine.get_traction_summary()
