"""
Sentinel PayFlow — Exact Fixed-Point u64 Programmable Settlement & Routing Engine
Eliminates floating-point drift (sum of shares == total amountRaw invariant guaranteed)
and orchestrates 4 high-volume institutional B2B verticals over MultiHopper on Solana.
"""
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional

from .config import (
    CHALLENGE_QUALIFYING_VOLUME_USD,
    DEFAULT_USDC_MINT,
    MAX_API_HOPS,
    MIN_API_HOPS,
    MULTIHOPPER_REVSHARE_BPS,
    SENTINEL_GATEWAY_FEE_BPS,
    SENTINEL_PROVIDER_ID_HEX,
    SENTINEL_PROVIDER_ID_STR,
    SENTINEL_SOLANA_TREASURY,
)
from .multihopper_client import MultiHopperClient
from .shield_validator import ShieldValidator


def decimal_to_raw(amount: Decimal, decimals: int = 6) -> int:
    """Converts human token Decimal to exact integer base units (u64)."""
    scale = Decimal(10) ** decimals
    return int((Decimal(str(amount)) * scale).to_integral_value(rounding=ROUND_DOWN))


def raw_to_token_str(amount_raw: int, decimals: int = 6) -> str:
    """Formats raw u64 integer back to fixed-point decimal string without float precision loss."""
    scale = Decimal(10) ** decimals
    val = Decimal(int(amount_raw)) / scale
    return f"{val:.{decimals}f}"


def split_raw_by_bps(total_raw: int, bps_shares: List[int]) -> List[int]:
    """
    Splits `total_raw` across `bps_shares` (where sum(bps_shares) == 10_000)
    using exact integer math and assigns any integer-division remainder (0..N-1 units)
    deterministically to index 0 (primary beneficiary) so sum(result) == total_raw ALWAYS.
    """
    if sum(bps_shares) != 10_000:
        raise ValueError(f"Basis point shares must sum to 10000, got {sum(bps_shares)}")
    if total_raw <= 0:
        raise ValueError("total_raw must be positive")

    allocations = [(total_raw * bps) // 10_000 for bps in bps_shares]
    remainder = total_raw - sum(allocations)
    allocations[0] += remainder
    assert sum(allocations) == total_raw
    return allocations


class PayFlowEngine:
    """
    Core orchestrator combining Sentinel Shield pre-flight compliance (Tornado Cash + OFAC + Anti-Wash)
    with MultiHopper's non-commingled programmable multi-hop routing on Solana.
    """

    def __init__(
        self,
        validator: Optional[ShieldValidator] = None,
        mh_client: Optional[MultiHopperClient] = None,
        treasury_wallet: Optional[str] = None,
    ):
        self.validator = validator or ShieldValidator()
        self.mh_client = mh_client or MultiHopperClient()
        self.treasury_wallet = treasury_wallet or SENTINEL_SOLANA_TREASURY

        # In-memory telemetry counters for institutional volume & revenue share tracking
        self.metrics = {
            "routes_created": 0,
            "routes_blocked_aml": 0,
            "total_routed_usd": Decimal("0.00"),
            "estimated_revshare_usd": Decimal("0.00"),
            "recent_routes": [],
            "recent_blocks": [],
        }

    def execute_compliant_route(
        self,
        source_owner: str,
        destinations: List[Dict[str, Any]],
        token_mint: str = DEFAULT_USDC_MINT,
        token_decimals: int = 6,
        token_symbol: str = "USDC",
        token_price_usd: str = "1.00",
        hops: int = 4,
        arrival_seconds: int = 30,
        bridge_origin_evm: Optional[str] = None,
        vertical: str = "CUSTOM_ROUTE",
        reference_note: str = "",
    ) -> Dict[str, Any]:
        """
        End-to-end pre-flight compliance screening + MultiHopper quote & route initialization.
        Raises ValueError on fail-closed compliance block while recording SAR telemetry.
        """
        if hops < MIN_API_HOPS or hops > MAX_API_HOPS:
            raise ValueError(f"MultiHopper API hops must be between {MIN_API_HOPS} and {MAX_API_HOPS}.")

        normalized_dests = [
            {"wallet": str(d["wallet"]).strip(), "amountRaw": str(int(d["amountRaw"]))}
            for d in destinations
        ]
        total_raw = sum(int(d["amountRaw"]) for d in normalized_dests)

        # Step 1: Layer-0 Sentinel Shield Pre-Flight (Tornado Cash + OFAC + Anti-Wash)
        compliance = self.validator.screen_transfer_route(
            source_owner=source_owner,
            destinations=normalized_dests,
            total_amount_raw=total_raw,
            token_decimals=token_decimals,
            token_price_usd=Decimal(str(token_price_usd)),
            bridge_origin_evm=bridge_origin_evm,
        )

        if not compliance["approved"]:
            self.metrics["routes_blocked_aml"] += 1
            self.metrics["recent_blocks"].insert(
                0,
                {
                    "timestamp": int(time.time()),
                    "vertical": vertical,
                    "code": compliance["code"],
                    "reason": compliance["reason"],
                    "incident_reference": compliance["incident_reference"],
                },
            )
            self.metrics["recent_blocks"] = self.metrics["recent_blocks"][:25]
            raise PermissionError(
                f"[{compliance['code']}] {compliance['reason']} (Ref: {compliance['incident_reference']})"
            )

        # Step 2: MultiHopper Fee & Revenue Share Estimate
        estimate = self.mh_client.estimate_transfer(
            token_mint=token_mint,
            amount_raw=total_raw,
            token_decimals=token_decimals,
            hops=hops,
            destination_count=len(normalized_dests),
        )

        # Step 3: Initialize MultiHopper Transfer & On-Chain RouteConfig Metadata
        amount_tokens_str = raw_to_token_str(total_raw, token_decimals)
        transfer = self.mh_client.create_transfer(
            token_mint=token_mint,
            amount_raw=total_raw,
            amount_tokens=amount_tokens_str,
            source_owner=source_owner.strip(),
            destinations=normalized_dests,
            hops=hops,
            arrival_seconds=arrival_seconds,
            token_decimals=token_decimals,
            token_symbol=token_symbol,
            token_price_usd=str(token_price_usd),
        )

        # Step 4: Prepare Non-Custodial Unsigned Transaction Bundle
        unsigned_bundle = self.mh_client.prepare_transactions(transfer["transfer_id"])

        usd_val = Decimal(compliance["usd_notional"])
        revshare_usd = (
            usd_val * Decimal(MULTIHOPPER_REVSHARE_BPS) / Decimal(10_000)
        ).quantize(Decimal("0.01"))

        self.metrics["routes_created"] += 1
        self.metrics["total_routed_usd"] += usd_val
        self.metrics["estimated_revshare_usd"] += revshare_usd

        envelope = {
            "transfer_id": transfer["transfer_id"],
            "vertical": vertical,
            "reference_note": reference_note,
            "execution_mode": transfer["execution_mode"],
            "provider_id": SENTINEL_PROVIDER_ID_STR,
            "provider_id_hex": SENTINEL_PROVIDER_ID_HEX,
            "total_amount_raw": str(total_raw),
            "total_amount_tokens": amount_tokens_str,
            "token_symbol": token_symbol,
            "usd_notional": str(usd_val),
            "sentinel_025_revshare_usd": str(revshare_usd),
            "compliance_attestation": compliance,
            "fee_estimate": estimate,
            "multihopper_transfer": transfer,
            "non_custodial_bundle": unsigned_bundle,
        }

        self.metrics["recent_routes"].insert(
            0,
            {
                "transfer_id": transfer["transfer_id"],
                "vertical": vertical,
                "usd_notional": str(usd_val),
                "revshare_usd": str(revshare_usd),
                "destinations_count": len(normalized_dests),
                "hops": hops,
                "attestation_sha256": compliance["cleanpass_attestation"]["attestation_sha256"],
                "created_at": int(time.time()),
            },
        )
        self.metrics["recent_routes"] = self.metrics["recent_routes"][:25]
        return envelope

    # ========================================================================
    # VERTICAL 1: Confidential Enterprise Payroll (Multisend up to 19 workers + Gateway Fee)
    # Solves public salary exposure on Solana without commingled Tornado-style pools
    # ========================================================================
    def create_payroll_route(
        self,
        employer_wallet: str,
        worker_payouts: List[Dict[str, Any]],
        hops: int = 5,
        bridge_origin_evm: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not worker_payouts or len(worker_payouts) > 19:
            raise ValueError("Payroll batch supports 1 to 19 workers (+ 1 gateway treasury share = max 20).")

        destinations: List[Dict[str, Any]] = []
        subtotal_raw = 0
        for item in worker_payouts:
            amt_raw = decimal_to_raw(Decimal(str(item["amount_usdc"])), 6)
            subtotal_raw += amt_raw
            destinations.append({"wallet": item["recipient"], "amountRaw": str(amt_raw)})

        fee_raw = (subtotal_raw * SENTINEL_GATEWAY_FEE_BPS) // 10_000
        if fee_raw > 0 and self.treasury_wallet != employer_wallet:
            destinations.append({"wallet": self.treasury_wallet, "amountRaw": str(fee_raw)})

        return self.execute_compliant_route(
            source_owner=employer_wallet,
            destinations=destinations,
            hops=hops,
            arrival_seconds=45,
            bridge_origin_evm=bridge_origin_evm,
            vertical="CONFIDENTIAL_WEB3_PAYROLL",
            reference_note=f"Confidential payroll batch ({len(worker_payouts)} contractors)",
        )

    # ========================================================================
    # VERTICAL 2: RWA Specialty Coffee & Agricultural Export Forward Settlement
    # 90% Producer Cooperative / 8% Wet Mill & Port Logistics / 2% Structuring Fee
    # ========================================================================
    def create_b2b_commodity_route(
        self,
        buyer_wallet: str,
        contract_amount_usdc: Any,
        farmer_wallet: str,
        mill_logistics_wallet: str,
        hops: int = 6,
        bridge_origin_evm: Optional[str] = None,
    ) -> Dict[str, Any]:
        total_raw = decimal_to_raw(Decimal(str(contract_amount_usdc)), 6)
        farmer_raw, mill_raw, sentinel_raw = split_raw_by_bps(total_raw, [9000, 800, 200])

        destinations = [
            {"wallet": farmer_wallet, "amountRaw": str(farmer_raw)},
            {"wallet": mill_logistics_wallet, "amountRaw": str(mill_raw)},
            {"wallet": self.treasury_wallet, "amountRaw": str(sentinel_raw)},
        ]

        return self.execute_compliant_route(
            source_owner=buyer_wallet,
            destinations=destinations,
            hops=hops,
            arrival_seconds=60,
            bridge_origin_evm=bridge_origin_evm,
            vertical="RWA_AGRO_EXPORT_SETTLEMENT",
            reference_note=f"RWA Agro Forward Contract Settlement (${contract_amount_usdc} USDC)",
        )

    # ========================================================================
    # VERTICAL 3: US CPA / Corporate Treasury Multi-Vendor Batch Settlement
    # Integrates with Sentinel 'The Night Shift' SHA-256 ledger reconciliation
    # ========================================================================
    def create_cpa_vendor_batch_route(
        self,
        corporate_treasury_wallet: str,
        vendor_invoices: List[Dict[str, Any]],
        hops: int = 4,
        bridge_origin_evm: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not vendor_invoices or len(vendor_invoices) > 19:
            raise ValueError("Vendor invoice batch supports 1 to 19 vendors (+ 1 reconciliation fee share).")

        destinations: List[Dict[str, Any]] = []
        subtotal_raw = 0
        for inv in vendor_invoices:
            amt_raw = decimal_to_raw(Decimal(str(inv["amount_usdc"])), 6)
            subtotal_raw += amt_raw
            destinations.append({"wallet": inv["vendor_wallet"], "amountRaw": str(amt_raw)})

        fee_raw = (subtotal_raw * SENTINEL_GATEWAY_FEE_BPS) // 10_000
        if fee_raw > 0 and self.treasury_wallet != corporate_treasury_wallet:
            destinations.append({"wallet": self.treasury_wallet, "amountRaw": str(fee_raw)})

        return self.execute_compliant_route(
            source_owner=corporate_treasury_wallet,
            destinations=destinations,
            hops=hops,
            arrival_seconds=30,
            bridge_origin_evm=bridge_origin_evm,
            vertical="CORPORATE_CPA_VENDOR_BATCH",
            reference_note=f"Corporate AP ledger settlement ({len(vendor_invoices)} vendor invoices)",
        )

    # ========================================================================
    # VERTICAL 4: Marketplace & Delivery Atomic Split (85% Merchant / 10% Courier / 5% Platform)
    # ========================================================================
    def create_merchant_split_route(
        self,
        customer_wallet: str,
        total_bill_usdc: Any,
        merchant_wallet: str,
        driver_wallet: str,
        hops: int = 3,
        bridge_origin_evm: Optional[str] = None,
    ) -> Dict[str, Any]:
        total_raw = decimal_to_raw(Decimal(str(total_bill_usdc)), 6)
        merch_raw, driver_raw, sentinel_raw = split_raw_by_bps(total_raw, [8500, 1000, 500])

        destinations = [
            {"wallet": merchant_wallet, "amountRaw": str(merch_raw)},
            {"wallet": driver_wallet, "amountRaw": str(driver_raw)},
            {"wallet": self.treasury_wallet, "amountRaw": str(sentinel_raw)},
        ]

        return self.execute_compliant_route(
            source_owner=customer_wallet,
            destinations=destinations,
            hops=hops,
            arrival_seconds=15,
            bridge_origin_evm=bridge_origin_evm,
            vertical="COMMERCE_DELIVERY_SPLIT",
            reference_note=f"Marketplace atomic split (${total_bill_usdc} USDC)",
        )

    def get_traction_summary(self) -> Dict[str, Any]:
        routed = self.metrics["total_routed_usd"]
        pct = min(
            Decimal("100.00"),
            (routed / CHALLENGE_QUALIFYING_VOLUME_USD * Decimal("100")).quantize(Decimal("0.01")),
        )
        return {
            "provider_id": SENTINEL_PROVIDER_ID_STR,
            "provider_id_hex": SENTINEL_PROVIDER_ID_HEX,
            "multihopper_core_program": self.mh_client.base_url,
            "live_api_key_active": self.mh_client.is_live_api_configured,
            "sanctions_db_entries": self.validator.total_sanctioned_entities,
            "routes_created": self.metrics["routes_created"],
            "routes_blocked_aml": self.metrics["routes_blocked_aml"],
            "total_routed_usd": str(routed),
            "challenge_target_usd": str(CHALLENGE_QUALIFYING_VOLUME_USD),
            "challenge_progress_pct": str(pct),
            "estimated_025_revshare_usd": str(self.metrics["estimated_revshare_usd"]),
            "recent_routes": self.metrics["recent_routes"],
            "recent_blocks": self.metrics["recent_blocks"],
        }
