import unittest

from highschoolphysics.providers import (
    ProviderSecretStore,
    budget_status,
    estimate_cost_cents,
)


class ProviderOperationsTests(unittest.TestCase):
    def test_fernet_secret_store_round_trips_without_plaintext(self):
        store = ProviderSecretStore(ProviderSecretStore.generate_key())
        ciphertext = store.encrypt("sk-live-secret")

        self.assertNotIn("sk-live-secret", ciphertext)
        self.assertEqual(store.decrypt(ciphertext), "sk-live-secret")

    def test_estimate_cost_uses_per_thousand_unit_rates(self):
        self.assertEqual(
            estimate_cost_cents(
                input_units=1500,
                output_units=500,
                input_cost_per_1k_cents=2,
                output_cost_per_1k_cents=8,
            ),
            7.0,
        )

    def test_budget_status_blocks_call_and_monthly_limits(self):
        self.assertEqual(
            budget_status(
                daily_call_limit=2,
                monthly_budget_cents=100,
                current_daily_calls=2,
                current_monthly_cost_cents=20,
                estimated_cost_cents=1,
            ),
            {
                "allowed": False,
                "reason": "daily_call_limit_exceeded",
            },
        )
        self.assertEqual(
            budget_status(
                daily_call_limit=10,
                monthly_budget_cents=100,
                current_daily_calls=1,
                current_monthly_cost_cents=99,
                estimated_cost_cents=2,
            ),
            {
                "allowed": False,
                "reason": "monthly_budget_exceeded",
            },
        )
        self.assertEqual(
            budget_status(
                daily_call_limit=10,
                monthly_budget_cents=0,
                current_daily_calls=1,
                current_monthly_cost_cents=999,
                estimated_cost_cents=10,
            ),
            {"allowed": True, "reason": "ok"},
        )


if __name__ == "__main__":
    unittest.main()
