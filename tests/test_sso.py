import unittest

from highschoolphysics.sso import (
    build_oidc_authorization_url,
    create_oidc_login_state,
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


if __name__ == "__main__":
    unittest.main()
