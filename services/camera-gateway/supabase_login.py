"""Server-side Supabase Auth requests and access-token verification."""

import json
import re
from urllib.parse import urlencode
from uuid import UUID

import jwt


SOCIAL_PROVIDERS = {"google": "google", "microsoft": "azure"}


def user_id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Expected a Supabase user or factor ID.")
    return value


class SupabaseAuth:
    def __init__(self, origin, key, methods, read_json):
        if not isinstance(key, str) or not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{1,256}", key):
            raise ValueError("Use a Supabase publishable API key.")
        if (not isinstance(methods, list) or not methods
                or any(method not in ("email", *SOCIAL_PROVIDERS) for method in methods)):
            raise ValueError("Choose enabled Supabase sign-in methods.")
        self.issuer = origin + "/auth/v1"
        self.key = key
        self.methods = frozenset(methods)
        self.read_json = read_json

    def authorize(self, method, callback, challenge):
        provider = SOCIAL_PROVIDERS[method]
        params = {"provider": provider, "redirect_to": callback, "code_challenge": challenge,
                  "code_challenge_method": "s256", "prompt": "select_account"}
        if provider == "azure":
            params["scopes"] = "email"
        return self.issuer + "/authorize?" + urlencode(params)

    def request(self, path, body=None, token=None):
        headers = {"apikey": self.key}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        return self.read_json(self.issuer + path, data=json.dumps(body).encode() if body is not None else None,
                              headers=headers)

    def claims(self, token):
        header = jwt.get_unverified_header(token)
        algorithm, kid = header.get("alg"), header.get("kid")
        if algorithm not in ("ES256", "RS256") or not isinstance(kid, str) or not kid:
            raise ValueError("Use asymmetric Supabase Auth JWT signing keys.")
        jwks = self.read_json(self.issuer + "/.well-known/jwks.json")
        key_type = "EC" if algorithm == "ES256" else "RSA"
        keys = [key for key in jwks["keys"] if key.get("kid") == kid and key.get("kty") == key_type
                and key.get("alg", algorithm) == algorithm and key.get("use", "sig") == "sig"]
        if len(keys) != 1:
            raise ValueError("Supabase Auth signing key is unavailable.")
        claims = jwt.decode(token, jwt.PyJWK.from_dict(keys[0], algorithm=algorithm).key,
                            algorithms=[algorithm], audience="authenticated", issuer=self.issuer,
                            options={"require": ["sub", "exp", "iat", "session_id", "aal"], "strict_aud": True},
                            leeway=15)
        user_id(claims["sub"])
        user_id(claims["session_id"])
        if (claims.get("role") != "authenticated" or claims.get("is_anonymous") is not False
                or claims["aal"] not in ("aal1", "aal2")):
            raise ValueError("Expected an authenticated Supabase user session.")
        return claims

    def factors(self, token, subject):
        user = self.request("/user", token=token)
        if user.get("id") != subject or not user.get("email_confirmed_at"):
            raise ValueError("Sign-in requires a confirmed email address.")
        return [{"id": user_id(factor["id"]), "name": factor.get("friendly_name") or "Authenticator"}
                for factor in user.get("factors") or []
                if factor.get("factor_type") == "totp" and factor.get("status") == "verified"]

    def enroll(self, token):
        response = self.request("/factors", {"factor_type": "totp", "issuer": "Proxigo Global View"}, token)
        return {"id": user_id(response["id"]), "secret": response["totp"]["secret"],
                "qr": response["totp"]["qr_code"]}

    def verify(self, token, factor, code):
        route = "/factors/" + user_id(factor)
        challenge = self.request(route + "/challenge", {}, token)
        response = self.request(route + "/verify", {"challenge_id": challenge["id"], "code": code}, token)
        return self.claims(response["access_token"])
