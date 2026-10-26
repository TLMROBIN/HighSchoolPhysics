import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from highschoolphysics.auth import AuthService
from highschoolphysics.db import connect
from highschoolphysics.repository import PhysicsRepository
from tests.http_support import LivePhysicsServer


class SsoPasswordHttpTests(unittest.TestCase):
    def test_sso_password_flow_and_legacy_requirement_do_not_touch_local_credential(self):
        with tempfile.TemporaryDirectory() as tmp, LivePhysicsServer(Path(tmp) / 'http.sqlite3') as server:
            conn = connect(server.db_path)
            try:
                PhysicsRepository(conn).save_oidc_provider_config(
                    actor_id='user-admin', provider_name='Test OIDC', issuer='https://idp.example.test',
                    client_id='physics-test', client_secret='test-only', authorization_endpoint='https://idp.example.test/auth',
                    token_endpoint='https://idp.example.test/token', userinfo_endpoint='https://idp.example.test/userinfo', enabled=True,
                )
                conn.execute("update users set must_change_password=1 where id='user-admin'")
                conn.commit()
                before = tuple(conn.execute("select password_hash,must_change_password from users where id='user-admin'").fetchone())
                session = AuthService(conn).session_for_user('user-admin', 'sso-password-test')
            finally:
                conn.close()
            cookie = 'hsp_session=' + session.token
            status, headers, _ = server.request('GET', '/change-password', headers={'Cookie': cookie})
            self.assertEqual(status, 303)
            query = parse_qs(urlparse(headers['Location']).query)
            self.assertEqual(query['kc_action'], ['UPDATE_PASSWORD'])
            self.assertEqual(query['code_challenge_method'], ['S256'])
            status, _, _ = server.post_json('/api/password/change', {'current_password':'anything','new_password':'NewPassword123'}, cookie)
            self.assertEqual(status, 409)
            status, _, _ = server.request('GET', '/', headers={'Cookie': cookie})
            self.assertEqual(status, 303)
            conn = connect(server.db_path)
            self.assertEqual(tuple(conn.execute("select password_hash,must_change_password from users where id='user-admin'").fetchone()), before)
            conn.close()
