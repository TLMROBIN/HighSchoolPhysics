import unittest
from unittest.mock import patch

from highschoolphysics.sso import (
    build_oidc_authorization_url,
    create_oidc_login_state,
    exchange_oidc_code_for_claims,
    normalize_oidc_claims,
)


class SSOTests(unittest.TestCase):
    def test_oidc_authorization_url_includes_state_nonce_and_pkce(self):
        login_state = create_oidc_login_state()
        url = build_oidc_authorization_url(
            {
                "authorization_endpoint": "https://idp.example.test/authorize",
                "client_id": "physics-client",
                "scope": "openid profile email",
            },
            redirect_uri="https://school.example.test/sso/callback",
            state=login_state,
        )

        self.assertIn("state=%s" % login_state["state"], url)
        self.assertIn("nonce=%s" % login_state["nonce"], url)
        self.assertIn("code_challenge=", url)
        self.assertNotIn(login_state["code_verifier"], url)

    def test_normalize_oidc_claims_extracts_binding_identity(self):
        claims = normalize_oidc_claims(
            {
                "iss": "https://idp.example.test",
                "sub": "teacher-001",
                "email": "teacher@example.test",
                "name": "李老师",
                "preferred_username": "teacher_li",
            }
        )

        self.assertEqual(claims["issuer"], "https://idp.example.test")
        self.assertEqual(claims["subject"], "teacher-001")
        self.assertEqual(claims["external_id"], "https://idp.example.test|teacher-001")
        self.assertEqual(claims["local_username"], "teacher_li")

    def test_code_exchange_can_return_the_id_token_for_front_channel_logout(self):
        config = {
            "client_id": "highschoolphysics",
            "token_endpoint": "https://sso.example.test/token",
            "userinfo_endpoint": "https://sso.example.test/userinfo",
        }
        with patch(
            "highschoolphysics.sso._post_form",
            return_value={"access_token": "access", "id_token": "header.payload.signature"},
        ), patch(
            "highschoolphysics.sso._get_json",
            return_value={"sub": "student-1", "preferred_username": "student001"},
        ):
            claims, id_token_hint = exchange_oidc_code_for_claims(
                config,
                "secret",
                "code",
                "verifier",
                "https://school.example.test/physics/sso/callback",
                include_id_token_hint=True,
            )

        self.assertEqual(claims["preferred_username"], "student001")
        self.assertEqual(id_token_hint, "header.payload.signature")


if __name__ == "__main__":
    unittest.main()
