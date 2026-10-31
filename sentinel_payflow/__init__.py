"""
Sentinel PayFlow — Institutional AML/OFAC Pre-Flight Shield & MultiHopper Programmable Router
Engineered by Sentinel Fleet Technologies for Solana Devnet & Mainnet.
"""

__version__ = "1.0.0"

from .config import (
    MULTIHOPPER_CORE_PROGRAM_ID,
    MULTIHOPPER_DEVNET_API,
    MULTIHOPPER_MAINNET_API,
    SENTINEL_PROVIDER_ID_HEX,
    SENTINEL_PROVIDER_ID_STR,
)
from .shield_validator import ShieldValidator
from .multihopper_client import MultiHopperClient
from .payflow_engine import PayFlowEngine

__all__ = [
    "__version__",
    "MULTIHOPPER_CORE_PROGRAM_ID",
    "MULTIHOPPER_DEVNET_API",
    "MULTIHOPPER_MAINNET_API",
    "SENTINEL_PROVIDER_ID_STR",
    "SENTINEL_PROVIDER_ID_HEX",
    "ShieldValidator",
    "MultiHopperClient",
    "PayFlowEngine",
]
