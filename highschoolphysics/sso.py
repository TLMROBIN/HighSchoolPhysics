"""OIDC SSO helpers."""

import base64
import hashlib
import json
import secrets
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class OidcExchangeError(ValueError):
    pass


def _base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def create_oidc_login_state():
    code_verifier = secrets.token_urlsafe(48)
    return {
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(24),
        "code_verifier": code_verifier,
        "code_challenge": _base64url(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        ),
    }


def build_oidc_authorization_url(config, redirect_uri, state):
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "scope": config.get("scope", "openid profile email"),
            "state": state["state"],
            "nonce": state["nonce"],
            "code_challenge": state["code_challenge"],
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in config["authorization_endpoint"] else "?"
    return "%s%s%s" % (config["authorization_endpoint"], separator, query)


def normalize_oidc_claims(claims):
    issuer = claims.get("iss") or claims.get("issuer") or ""
    subject = claims.get("sub") or claims.get("subject") or ""
    email = claims.get("email") or ""
    preferred_username = claims.get("preferred_username") or ""
    local_username = preferred_username or email.split("@", 1)[0]
    return {
        "issuer": issuer,
        "subject": subject,
        "email": email,
        "display_name": claims.get("name") or claims.get("display_name") or local_username,
        "preferred_username": preferred_username,
        "local_username": local_username,
        "external_id": "%s|%s" % (issuer, subject),
    }


def _post_form(url, data):
    encoded = urlencode(data).encode("utf-8")
    request = Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise OidcExchangeError("OIDC token exchange failed") from exc


def _get_json(url, access_token):
    request = Request(url, headers={"Authorization": "Bearer %s" % access_token})
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise OidcExchangeError("OIDC userinfo request failed") from exc


def _decode_jwt_payload_without_verification(token):
    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode((payload + padding).encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise OidcExchangeError("OIDC id_token payload is invalid") from exc


def exchange_oidc_code_for_claims(config, client_secret, code, code_verifier, redirect_uri):
    token_endpoint = config.get("token_endpoint")
    if not token_endpoint:
        raise OidcExchangeError("OIDC token endpoint is not configured")
    data = {
        "grant_type": "authorization_code",
        "client_id": config["client_id"],
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    token_payload = _post_form(token_endpoint, data)
    access_token = token_payload.get("access_token")
    if config.get("userinfo_endpoint") and access_token:
        return _get_json(config["userinfo_endpoint"], access_token)
    if token_payload.get("id_token"):
        return _decode_jwt_payload_without_verification(token_payload["id_token"])
    raise OidcExchangeError("OIDC token response did not include usable claims")
