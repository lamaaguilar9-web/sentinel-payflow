"""
Sentinel PayFlow — Comprehensive Institutional Audit & Unit Test Suite
Validates:
  1. Cryptographic 32-byte Base58 Solana public key verification
  2. All 175 sanctioned entities (155 Tornado Cash / OFAC SDN + 20 Solana Exploiters) O(1) blocking
  3. Cross-chain bridge origin Tornado Cash taint blocking
  4. Anti-wash / self-routing & Sybil duplicate split rejection
  5. Exact u64 fixed-point conservation across prime/odd decimal splits (zero 1-micro-USDC drift)
  6. Exact 16-byte ([u8; 16]) provider_id & reference_id MultiHopper RouteConfig invariants
  7. FastAPI REST endpoints (/health, /api/v1/compliance/screen, /api/v1/routes/preset/*, /api/v1/routes/create)
"""
import io
import os
import sys
import unittest
from decimal import Decimal

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from sentinel_payflow.api import app
from sentinel_payflow.config import (
    MULTIHOPPER_CORE_PROGRAM_ID,
    SENTINEL_PROVIDER_ID_BYTES,
    SENTINEL_PROVIDER_ID_U8_LIST,
    SENTINEL_SOLANA_TREASURY,
    derive_reference_id_bytes,
)
from sentinel_payflow.payflow_engine import PayFlowEngine, split_raw_by_bps
from sentinel_payflow.shield_validator import ShieldValidator, decode_base58_solana_pubkey


VALID_SRC = "7fUAJdStEuGbc3sM84cKRL6yYaaSstyLSU4ve5oovLS7"
VALID_DST_1 = "DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy"
VALID_DST_2 = "DYw8jCTfwHNRJhhmFcbXvVDTqWMEVFBX6ZKUmG5CNSKK"
VALID_DST_3 = "4fYNw3dojWmQ4dXtSGE9epjRGy9pFSx62YypT7avPYvA"


class TestSentinelPayFlowInstitutional(unittest.TestCase):
    def setUp(self):
        self.validator = ShieldValidator()
        self.engine = PayFlowEngine(validator=self.validator)
        self.client = TestClient(app)

    # ------------------------------------------------------------------
    # 1. Cryptographic & Protocol Constants Invariants
    # ------------------------------------------------------------------
    def test_01_provider_id_exact_16_bytes(self):
        self.assertEqual(len(SENTINEL_PROVIDER_ID_BYTES), 16)
        self.assertEqual(len(SENTINEL_PROVIDER_ID_U8_LIST), 16)
        for byte_val in SENTINEL_PROVIDER_ID_U8_LIST:
            self.assertGreaterEqual(byte_val, 0)
            self.assertLessEqual(byte_val, 255)

    def test_02_reference_id_exact_16_bytes(self):
        ref = derive_reference_id_bytes("invoice-2026-9981")
        self.assertEqual(len(ref), 16)

    def test_03_treasury_and_program_id_are_valid_32_byte_base58(self):
        self.assertIsNotNone(decode_base58_solana_pubkey(SENTINEL_SOLANA_TREASURY))
        self.assertIsNotNone(decode_base58_solana_pubkey(MULTIHOPPER_CORE_PROGRAM_ID))

    def test_04_rejects_evm_0x_address_on_solana_rail(self):
        res = self.validator.validate_solana_wallet("0x15C42d6E839182045f1248030fEF310b3cF3d74e")
        self.assertFalse(res["valid"])
        self.assertEqual(res["code"], "INVALID_EVM_ADDRESS_ON_SOLANA_RAIL")

    def test_05_rejects_malformed_base58_addresses(self):
        for bad in ["", "shortkey", "0OIlInvalidBase58Characters111111111111", "111111"]:
            res = self.validator.validate_solana_wallet(bad)
            self.assertFalse(res["valid"], f"Should reject malformed key: {bad}")

    # ------------------------------------------------------------------
    # 2. Tornado Cash, OFAC SDN & Solana Exploiter Screening (175 Entities)
    # ------------------------------------------------------------------
    def test_06_sanctions_db_has_175_verified_entities(self):
        self.assertEqual(self.validator.total_sanctioned_entities, 175)
        self.assertEqual(len(self.validator.evm_blocked_lower), 155)
        self.assertEqual(len(self.validator.solana_blocked), 20)

    def test_07_blocks_all_155_tornado_cash_and_ofac_bridge_origins(self):
        for evm_addr in list(self.validator.evm_blocked_lower):
            # Test mixed-case checksum resilience
            mixed_case = "0x" + evm_addr[2:].upper()
            res = self.validator.validate_bridge_origin_evm(mixed_case)
            self.assertFalse(res["valid"], f"Failed to block Tornado/OFAC address {mixed_case}")
            self.assertIn("CROSS_CHAIN_", res["code"])

    def test_08_blocks_all_20_solana_ofac_and_exploiter_wallets(self):
        for sol_addr in list(self.validator.solana_blocked):
            res = self.validator.validate_solana_wallet(sol_addr)
            self.assertFalse(res["valid"], f"Failed to block Solana sanctioned wallet {sol_addr}")

    def test_09_clean_bridge_origin_passes(self):
        res = self.validator.validate_bridge_origin_evm("0x15C42d6E839182045f1248030fEF310b3cF3d74e")
        self.assertTrue(res["valid"])
        self.assertEqual(res["code"], "CLEAN_BRIDGE_ORIGIN")

    # ------------------------------------------------------------------
    # 3. Anti-Wash, Anti-Farming & Conservation Invariants
    # ------------------------------------------------------------------
    def test_10_blocks_circular_self_routing(self):
        res = self.validator.screen_transfer_route(
            source_owner=VALID_SRC,
            destinations=[{"wallet": VALID_SRC, "amountRaw": "10000000"}],
            total_amount_raw=10_000_000,
        )
        self.assertFalse(res["approved"])
        self.assertEqual(res["code"], "CIRCULAR_SELF_ROUTING_PROHIBITED")

    def test_11_blocks_duplicate_sybil_destinations(self):
        res = self.validator.screen_transfer_route(
            source_owner=VALID_SRC,
            destinations=[
                {"wallet": VALID_DST_1, "amountRaw": "5000000"},
                {"wallet": VALID_DST_1, "amountRaw": "5000000"},
            ],
            total_amount_raw=10_000_000,
        )
        self.assertFalse(res["approved"])
        self.assertEqual(res["code"], "DUPLICATE_DESTINATION_SYBIL_SPLIT")

    def test_12_blocks_dust_farming_transfers(self):
        res = self.validator.screen_transfer_route(
            source_owner=VALID_SRC,
            destinations=[{"wallet": VALID_DST_1, "amountRaw": "500"}],
            total_amount_raw=500,
        )
        self.assertFalse(res["approved"])
        self.assertEqual(res["code"], "DUST_OR_NON_QUALIFYING_AMOUNT")

    def test_13_blocks_outflow_velocity_cap_breach(self):
        # Default cap is $500,000 USD; test $750,000 USD
        res = self.validator.screen_transfer_route(
            source_owner=VALID_SRC,
            destinations=[{"wallet": VALID_DST_1, "amountRaw": str(750_000 * 1_000_000)}],
            total_amount_raw=750_000 * 1_000_000,
        )
        self.assertFalse(res["approved"])
        self.assertEqual(res["code"], "TREASURY_OUTFLOW_VELOCITY_BREAKER")

    def test_14_exact_u64_bps_split_zero_rounding_drift(self):
        # Test awkward prime micro-USDC totals where naive float math loses 1 unit
        for tricky_raw in [1_000_001, 33_333_333, 99_999_999_999, 100_000_007]:
            shares = split_raw_by_bps(tricky_raw, [8500, 1000, 500])
            self.assertEqual(sum(shares), tricky_raw)
            shares_agro = split_raw_by_bps(tricky_raw, [9000, 800, 200])
            self.assertEqual(sum(shares_agro), tricky_raw)

    # ------------------------------------------------------------------
    # 4. Verticals & MultiHopper Lifecycle Orchestration
    # ------------------------------------------------------------------
    def test_15_confidential_payroll_route_creates_valid_multisend(self):
        out = self.engine.create_payroll_route(
            employer_wallet=VALID_SRC,
            worker_payouts=[
                {"recipient": VALID_DST_1, "amount_usdc": "4500.25"},
                {"recipient": VALID_DST_2, "amount_usdc": "3200.75"},
            ],
            hops=5,
        )
        self.assertTrue(out["compliance_attestation"]["approved"])
        req = out["multihopper_transfer"]["multihopper_api_request"]
        self.assertIn("destinations", req)
        self.assertNotIn("recipientWallet", req)
        sum_dest = sum(int(d["amountRaw"]) for d in req["destinations"])
        self.assertEqual(sum_dest, int(req["amountRaw"]))

    def test_16_single_recipient_uses_recipientWallet_field(self):
        out = self.engine.execute_compliant_route(
            source_owner=VALID_SRC,
            destinations=[{"wallet": VALID_DST_1, "amountRaw": "25000000"}],
            hops=4,
        )
        req = out["multihopper_transfer"]["multihopper_api_request"]
        self.assertEqual(req["recipientWallet"], VALID_DST_1)
        self.assertNotIn("destinations", req)

    def test_17_agro_export_route_tracks_025_revshare(self):
        out = self.engine.create_b2b_commodity_route(
            buyer_wallet=VALID_SRC,
            contract_amount_usdc="100000.00",
            farmer_wallet=VALID_DST_1,
            mill_logistics_wallet=VALID_DST_2,
            hops=6,
        )
        # 0.25% of $100,000.00 == $250.00
        self.assertEqual(out["sentinel_025_revshare_usd"], "250.00")
        self.assertEqual(out["non_custodial_bundle"]["custody_model"], "NON_CUSTODIAL_CLIENT_SIGNED")

    # ------------------------------------------------------------------
    # 5. FastAPI Gateway & Judge Presets End-to-End Verification
    # ------------------------------------------------------------------
    def test_18_health_endpoint(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(body["sanctions_db"]["total_entities"], 175)
        self.assertEqual(body["custody_model"], "NON_CUSTODIAL_CLIENT_SIGNED")

    def test_19_all_clean_judge_presets_pass_200(self):
        for preset in ["agro_coffee_export", "confidential_payroll", "cpa_vendor_batch"]:
            r = self.client.post(f"/api/v1/routes/preset/{preset}")
            self.assertEqual(r.status_code, 200, f"Preset {preset} failed: {r.text}")
            self.assertIn("transfer_id", r.json())

    def test_20_all_attack_judge_presets_fail_closed_403(self):
        for attack in ["attack_tornado_bridge", "attack_solana_exploiter", "attack_circular_wash"]:
            r = self.client.post(f"/api/v1/routes/preset/{attack}")
            self.assertEqual(r.status_code, 403, f"Attack preset {attack} was not blocked!")

    def test_21_dashboard_html_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Sentinel PayFlow", r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
