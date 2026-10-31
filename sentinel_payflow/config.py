"""
Sentinel PayFlow — Institutional Configuration & MultiHopper Protocol Constants
Strictly environment-driven (Zero hardcoded secrets or private keys).
"""
import hashlib
import os
from decimal import Decimal

# ============================================================================
# MultiHopper Official Protocol & REST API v1 Endpoints
# ============================================================================
MULTIHOPPER_ENV = os.getenv("MULTIHOPPER_ENV", "devnet").strip().lower()
MULTIHOPPER_DEVNET_API = "https://devnet.multihopper.com/api/v1"
MULTIHOPPER_MAINNET_API = "https://multihopper.com/api/v1"

# Active API Base URL resolved by environment
MULTIHOPPER_BASE_URL = (
    MULTIHOPPER_MAINNET_API if MULTIHOPPER_ENV == "mainnet" else MULTIHOPPER_DEVNET_API
)

# API Key loaded strictly from environment (mh_test_... for devnet, mh_live_... for mainnet)
MULTIHOPPER_API_KEY = os.getenv("MULTIHOPPER_API_KEY", "").strip()

# Official On-Chain MultiHopper Core Program ID on Solana
MULTIHOPPER_CORE_PROGRAM_ID = "3jLoS2wbNgtKzieUUxwg6Xhdv6gbZkHDtPWA9ZAgspFh"

# ============================================================================
# Sentinel Fleet 16-Byte Integrator Identity (provider_id: Option<[u8; 16]>)
# Tracks lifetime 0.25% revenue share ($2,500 per $1M routed) via FeeSchedule PDA
# ============================================================================
# DEFAULT MUST BE EXACTLY 16 UTF-8 BYTES for on-chain RouteConfig Option<[u8; 16]>
SENTINEL_PROVIDER_ID_STR = os.getenv("SENTINEL_PROVIDER_ID", "SENTINELPAYFLOW1")
# Guarantee exact 16-byte width for on-chain RouteConfig [u8; 16] serialization
_raw_pid = SENTINEL_PROVIDER_ID_STR.encode("utf-8")
SENTINEL_PROVIDER_ID_BYTES: bytes = (
    _raw_pid[:16].ljust(16, b"\x00")
    if len(_raw_pid) != 16
    else _raw_pid
)
SENTINEL_PROVIDER_ID_U8_LIST = list(SENTINEL_PROVIDER_ID_BYTES)
SENTINEL_PROVIDER_ID_HEX = SENTINEL_PROVIDER_ID_BYTES.hex()

MULTIHOPPER_REVSHARE_BPS = 25  # 0.25% integrator revenue share on qualifying volume
CHALLENGE_QUALIFYING_VOLUME_USD = Decimal("100000.00")

# ============================================================================
# Solana Token Mints & Non-Custodial Treasury Configuration
# ============================================================================
SOL_SYSTEM_MINT = "11111111111111111111111111111111"
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_DEVNET_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
USDC_MAINNET_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

DEFAULT_USDC_MINT = (
    USDC_MAINNET_MINT if MULTIHOPPER_ENV == "mainnet" else USDC_DEVNET_MINT
)

# Valid 32-byte Base58 Solana Treasury Receiver for Gateway Structuring Fees (Configurable via .env)
SENTINEL_SOLANA_TREASURY = os.getenv(
    "SENTINEL_SOLANA_TREASURY",
    "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
)

# ============================================================================
# Institutional Guardrails & Protocol Limits
# ============================================================================
SENTINEL_GATEWAY_FEE_BPS = int(os.getenv("SENTINEL_GATEWAY_FEE_BPS", "50"))  # 0.50% default
MIN_QUALIFYING_AMOUNT_RAW = int(os.getenv("MIN_QUALIFYING_AMOUNT_RAW", "1000000"))  # 1.00 USDC (6 decimals)
MAX_SINGLE_ROUTE_USD = Decimal(os.getenv("MAX_SINGLE_ROUTE_USD", "500000.00"))  # Treasury velocity cap
MAX_MULTISEND_DESTINATIONS = 20  # MultiHopper REST v1 multisend cap
MIN_API_HOPS = 3
MAX_API_HOPS = 10
MAX_ONCHAIN_HOPS = 64

# Optional remote PayFi Shield endpoint (Houston VPS)
PAYFI_SHIELD_REMOTE_URL = os.getenv(
    "PAYFI_SHIELD_REMOTE_URL",
    "http://127.0.0.1:5060/api/v1/compliance/screen",
)

# Domain-separated public attestation tag (overridable via env for private enterprise deployments)
ATTESTATION_DOMAIN_TAG = os.getenv(
    "SENTINEL_ATTESTATION_DOMAIN",
    "SENTINEL-PAYFLOW-CLEANPASS-V1",
)

LEGAL_COMPLIANCE_NOTICE = (
    "Sentinel PayFlow operates as a strictly non-custodial pre-flight algorithmic triage "
    "and programmable routing interface ($0.00 custody of user funds; zero private keys stored). "
    "Risk screening outputs reflect deterministic sanctions-list matching and public on-chain "
    "heuristics at evaluation time and do not constitute legal advice or regulatory immunity."
)


def derive_reference_id_bytes(external_id: str) -> bytes:
    """Derives an exact 16-byte ([u8; 16]) reference_id for MultiHopper RouteConfig."""
    digest = hashlib.sha256(f"sentinel-ref:{external_id}".encode("utf-8")).digest()
    return digest[:16]
