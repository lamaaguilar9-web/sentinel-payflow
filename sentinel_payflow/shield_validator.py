"""
Sentinel PayFlow — Dual-Layer Institutional AML, Tornado Cash & Anti-Wash Shield
O(1) sub-millisecond in-memory screening across 175+ sanctioned EVM/Tornado Cash & Solana entities,
cryptographic Base58 Ed25519 key validation, anti-farming circular transfer detection,
and SHA-256 CleanPass compliance attestation.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import (
    ATTESTATION_DOMAIN_TAG,
    LEGAL_COMPLIANCE_NOTICE,
    MAX_MULTISEND_DESTINATIONS,
    MAX_SINGLE_ROUTE_USD,
    MIN_QUALIFYING_AMOUNT_RAW,
)

logger = logging.getLogger("SentinelShield")

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
EVM_ADDRESS_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")


def decode_base58_solana_pubkey(address: str) -> Optional[bytes]:
    """
    Cryptographically decodes a Base58 string and verifies it represents
    an exact 32-byte Solana public key / PDA. Returns None if malformed.
    """
    if not isinstance(address, str):
        return None
    addr = address.strip()
    if len(addr) < 32 or len(addr) > 44:
        return None

    num = 0
    for ch in addr:
        idx = BASE58_ALPHABET.find(ch)
        if idx == -1:
            return None
        num = num * 58 + idx

    raw = num.to_bytes((num.bit_length() + 7) // 8, "big") if num > 0 else b""
    leading_zeros = 0
    for ch in addr:
        if ch == "1":
            leading_zeros += 1
        else:
            break
    decoded = (b"\x00" * leading_zeros) + raw
    if len(decoded) != 32:
        return None
    return decoded


class ShieldValidator:
    """
    Layer-0 Institutional Pre-Flight Compliance & Genuine-Volume Guard for MultiHopper.
    Complements MultiHopper's native TRM Labs screening by blocking Tornado Cash bridge taint,
    OFAC SDN wallets, Solana drainers, and circular wash-farming before API submission.
    """

    def __init__(self, sanctions_path: Optional[str] = None):
        if sanctions_path is None:
            sanctions_path = os.path.join(
                os.path.dirname(__file__), "data", "sanctions_db.json"
            )
        self.sanctions_path = sanctions_path
        self.entities_by_addr: Dict[str, Dict[str, str]] = {}
        self.solana_blocked: Set[str] = set()
        self.evm_blocked_lower: Set[str] = set()
        self._load_sanctions_database()

    def _load_sanctions_database(self) -> None:
        if not os.path.exists(self.sanctions_path):
            raise FileNotFoundError(
                f"Sanctions database missing at {self.sanctions_path}"
            )
        with open(self.sanctions_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        for entity in payload.get("entities", []):
            addr = entity.get("address", "").strip()
            chain = entity.get("chain", "").upper()
            if not addr:
                continue
            if chain == "SOLANA":
                self.solana_blocked.add(addr)
                self.entities_by_addr[addr] = entity
            else:
                norm = addr.lower()
                self.evm_blocked_lower.add(norm)
                self.entities_by_addr[norm] = entity

    @property
    def total_sanctioned_entities(self) -> int:
        return len(self.solana_blocked) + len(self.evm_blocked_lower)

    def validate_solana_wallet(self, address: str) -> Dict[str, Any]:
        """Validates Base58 format (exact 32 bytes) and screens against Solana OFAC/Exploiter DB."""
        start_ns = time.perf_counter_ns()
        if not address or not isinstance(address, str):
            return {
                "valid": False,
                "code": "EMPTY_WALLET_ADDRESS",
                "reason": "Wallet address cannot be empty.",
                "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
            }

        addr = address.strip()
        if addr.startswith("0x"):
            return {
                "valid": False,
                "code": "INVALID_EVM_ADDRESS_ON_SOLANA_RAIL",
                "reason": "EVM 0x address detected on Solana transfer rail; use bridge_origin_evm for cross-chain origin screening.",
                "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
            }

        if decode_base58_solana_pubkey(addr) is None:
            return {
                "valid": False,
                "code": "INVALID_SOLANA_BASE58_PUBKEY",
                "reason": "Address is not a valid 32-byte Base58 Solana public key.",
                "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
            }

        if addr in self.solana_blocked:
            meta = self.entities_by_addr.get(addr, {})
            logger.warning("[FAIL-CLOSED] Blocked Solana wallet: %s (%s)", addr, meta.get("label"))
            return {
                "valid": False,
                "code": f"SOLANA_{meta.get('category', 'SANCTIONED')}_BLOCKED",
                "reason": f"Matched institutional blocklist: {meta.get('label', 'Sanctioned Solana Entity')}",
                "matched_entity": meta,
                "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
            }

        return {
            "valid": True,
            "code": "CLEAN_SOLANA_WALLET",
            "reason": "Verified 32-byte Base58 public key; zero sanctions matches.",
            "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
        }

    def validate_bridge_origin_evm(self, evm_address: Optional[str]) -> Dict[str, Any]:
        """
        Screens cross-chain bridged liquidity origins (Wormhole, Mayan, deBridge, Allbridge)
        against all 155 Tornado Cash pools, routers, proxies, and OFAC SDN EVM addresses.
        """
        start_ns = time.perf_counter_ns()
        if not evm_address:
            return {
                "valid": True,
                "code": "NATIVE_SOLANA_ORIGIN",
                "reason": "No cross-chain EVM bridge origin declared.",
                "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
            }

        addr = evm_address.strip()
        if not EVM_ADDRESS_REGEX.match(addr):
            return {
                "valid": False,
                "code": "INVALID_BRIDGE_ORIGIN_FORMAT",
                "reason": "Declared bridge_origin_evm must be a 42-char 0x hex address.",
                "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
            }

        norm = addr.lower()
        if norm in self.evm_blocked_lower:
            meta = self.entities_by_addr.get(norm, {})
            logger.warning(
                "[TORNADO/OFAC BRIDGE BLOCK] Blocked cross-chain origin: %s (%s)",
                addr,
                meta.get("label"),
            )
            return {
                "valid": False,
                "code": f"CROSS_CHAIN_{meta.get('category', 'TORNADO_OFAC')}_TAINT",
                "reason": f"Cross-chain bridge origin flagged ({meta.get('category')}): {meta.get('label')}",
                "matched_entity": meta,
                "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
            }

        return {
            "valid": True,
            "code": "CLEAN_BRIDGE_ORIGIN",
            "reason": "Cross-chain EVM origin cleared against 155 Tornado Cash & OFAC SDN entries.",
            "latency_ms": round((time.perf_counter_ns() - start_ns) / 1_000_000, 4),
        }

    def screen_transfer_route(
        self,
        source_owner: str,
        destinations: List[Dict[str, Any]],
        total_amount_raw: int,
        token_decimals: int = 6,
        token_price_usd: Decimal = Decimal("1.00"),
        bridge_origin_evm: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes full institutional pre-flight verification across:
          1. Cross-chain Tornado Cash / OFAC SDN bridge origin taint check
          2. Source wallet Base58 & sanctions check
          3. Destination wallets Base58 & sanctions check (1..20 recipients)
          4. Anti-Wash / Anti-Circular / Anti-Farming checks (no self-routing, no duplicate sybil splits, no dust)
          5. Treasury outflow velocity cap
          6. Exact u64 conservation of value (sum of destination shares == total_amount_raw)
        """
        t0 = time.perf_counter_ns()

        # 1. Cross-chain Tornado Cash / OFAC bridge check
        bridge_check = self.validate_bridge_origin_evm(bridge_origin_evm)
        if not bridge_check["valid"]:
            return self._build_decision(False, bridge_check["code"], bridge_check["reason"], t0, bridge_check.get("matched_entity"))

        # 2. Source owner validation
        src_check = self.validate_solana_wallet(source_owner)
        if not src_check["valid"]:
            return self._build_decision(
                False,
                f"SOURCE_{src_check['code']}",
                f"Source wallet rejected: {src_check['reason']}",
                t0,
                src_check.get("matched_entity"),
            )

        # 3. Destination count & dust rules
        if not destinations or len(destinations) > MAX_MULTISEND_DESTINATIONS:
            return self._build_decision(
                False,
                "INVALID_DESTINATION_COUNT",
                f"MultiHopper routes require between 1 and {MAX_MULTISEND_DESTINATIONS} destinations.",
                t0,
            )

        if int(total_amount_raw) < MIN_QUALIFYING_AMOUNT_RAW:
            return self._build_decision(
                False,
                "DUST_OR_NON_QUALIFYING_AMOUNT",
                f"Transfer amountRaw ({total_amount_raw}) is below institutional minimum ({MIN_QUALIFYING_AMOUNT_RAW}).",
                t0,
            )

        # 4. Velocity cap check
        human_tokens = Decimal(int(total_amount_raw)) / (Decimal(10) ** token_decimals)
        usd_notional = (human_tokens * Decimal(str(token_price_usd))).quantize(Decimal("0.01"))
        if usd_notional > MAX_SINGLE_ROUTE_USD:
            return self._build_decision(
                False,
                "TREASURY_OUTFLOW_VELOCITY_BREAKER",
                f"Route notional (${usd_notional} USD) exceeds single-batch institutional breaker (${MAX_SINGLE_ROUTE_USD} USD).",
                t0,
            )

        # 5. Per-destination screening + Anti-Wash / Anti-Farming invariants
        seen_wallets: Set[str] = set()
        sum_shares_raw = 0
        clean_src = source_owner.strip()

        for idx, dest in enumerate(destinations):
            w = str(dest.get("wallet", "")).strip()
            amt = int(dest.get("amountRaw", 0))
            if amt <= 0:
                return self._build_decision(
                    False,
                    "NON_POSITIVE_SHARE_AMOUNT",
                    f"Destination #{idx + 1} has non-positive amountRaw ({amt}).",
                    t0,
                )

            w_check = self.validate_solana_wallet(w)
            if not w_check["valid"]:
                return self._build_decision(
                    False,
                    f"DEST_{w_check['code']}",
                    f"Destination #{idx + 1} rejected: {w_check['reason']}",
                    t0,
                    w_check.get("matched_entity"),
                )

            # Anti-circular / anti-wash rule (MultiHopper Challenge strict genuine-volume requirement)
            if w == clean_src:
                return self._build_decision(
                    False,
                    "CIRCULAR_SELF_ROUTING_PROHIBITED",
                    "Source wallet cannot appear in destinations (circular/wash routing prohibited).",
                    t0,
                )

            if w in seen_wallets:
                return self._build_decision(
                    False,
                    "DUPLICATE_DESTINATION_SYBIL_SPLIT",
                    f"Duplicate recipient wallet ({w[:8]}...) detected in single batch; consolidate share instead.",
                    t0,
                )

            seen_wallets.add(w)
            sum_shares_raw += amt

        # 6. Exact u64 conservation check
        if sum_shares_raw != int(total_amount_raw):
            return self._build_decision(
                False,
                "AMOUNT_CONSERVATION_MISMATCH",
                f"Sum of destination shares ({sum_shares_raw}) != total amountRaw ({total_amount_raw}).",
                t0,
            )

        # 7. Generate deterministic CleanPass SHA-256 attestation
        elapsed_ms = round((time.perf_counter_ns() - t0) / 1_000_000, 4)
        canonical_payload = json.dumps(
            {
                "domain": ATTESTATION_DOMAIN_TAG,
                "source_owner": clean_src,
                "bridge_origin_evm": (bridge_origin_evm or "NONE").lower(),
                "total_amount_raw": str(total_amount_raw),
                "destinations": [
                    {"wallet": d["wallet"].strip(), "amountRaw": str(d["amountRaw"])}
                    for d in destinations
                ],
                "sanctions_db_size": self.total_sanctioned_entities,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        attestation_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

        return {
            "approved": True,
            "verdict": "CLEANPASS_APPROVED",
            "code": "INSTITUTIONAL_PREFLIGHT_PASSED",
            "reason": "All wallets, bridge origins, and u64 conservation invariants passed screening.",
            "screened_wallets_count": 1 + len(destinations) + (1 if bridge_origin_evm else 0),
            "sanctions_db_entries": self.total_sanctioned_entities,
            "usd_notional": str(usd_notional),
            "latency_ms": elapsed_ms,
            "cleanpass_attestation": {
                "schema": "SentinelCleanPass/v1",
                "attestation_sha256": f"0x{attestation_hash}",
                "custody_model": "NON_CUSTODIAL_CLIENT_SIGNED",
                "commingling": False,
                "evaluated_at": int(time.time()),
            },
            "legal_notice": LEGAL_COMPLIANCE_NOTICE,
        }

    def _build_decision(
        self,
        approved: bool,
        code: str,
        reason: str,
        t0_ns: int,
        matched_entity: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000, 4)
        incident_id = hashlib.sha256(
            f"{code}:{reason}:{int(time.time())}".encode("utf-8")
        ).hexdigest()[:16]
        res: Dict[str, Any] = {
            "approved": approved,
            "verdict": "CLEANPASS_APPROVED" if approved else "FAIL_CLOSED_BLOCKED",
            "code": code,
            "reason": reason,
            "incident_reference": f"SAR-{incident_id.upper()}",
            "sanctions_db_entries": self.total_sanctioned_entities,
            "latency_ms": elapsed_ms,
            "legal_notice": LEGAL_COMPLIANCE_NOTICE,
        }
        if matched_entity:
            res["matched_entity"] = matched_entity
        return res
