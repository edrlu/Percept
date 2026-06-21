"""One-time Pika login. Run ONCE:

    pipeline/.venv/bin/python -m pipeline.pika_login

Opens your browser to log into Pika, captures the OAuth grant, and stores a
refresh token so the app generates videos forever without re-auth. End users
never do this — only you, once.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import os
import secrets
import time
import urllib.parse
import webbrowser

import httpx

from . import pika_auth

PORT = int(os.getenv("PIKA_LOGIN_PORT", "64999"))
REDIRECT = f"http://localhost:{PORT}/callback"


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _register_client() -> str:
    resp = httpx.post(
        pika_auth.REGISTRATION_ENDPOINT,
        json={
            "client_name": "Cerebra Studio",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["client_id"]


def main() -> None:
    client_id = _register_client()
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    authorize_url = pika_auth.AUTHORIZE_ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": pika_auth.RESOURCE,
        }
    )

    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                captured["code"] = qs["code"][0]
                captured["state"] = qs.get("state", [""])[0]
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Pika connected. You can close this tab and return to the terminal.</h2>")

        def log_message(self, *_):  # silence
            return

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print("\nOpening your browser to log into Pika…")
    print(f"If it doesn't open, paste this URL into your browser:\n\n{authorize_url}\n")
    try:
        webbrowser.open(authorize_url)
    except Exception:
        pass

    print("Waiting for you to authorize in the browser…")
    while "code" not in captured:
        server.handle_request()

    if captured.get("state") != state:
        raise SystemExit("State mismatch — aborting for safety. Re-run the login.")

    resp = httpx.post(
        pika_auth.TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "code": captured["code"],
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": pika_auth.RESOURCE,
        },
        headers={"accept": "application/json"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"Token exchange failed ({resp.status_code}): {resp.text}")
    tok = resp.json()
    if "refresh_token" not in tok:
        raise SystemExit(f"No refresh_token returned — cannot persist auth: {tok}")

    pika_auth.save_creds(
        {
            "client_id": client_id,
            "access_token": tok["access_token"],
            "refresh_token": tok["refresh_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60,
        }
    )
    print("\n✅ Pika connected permanently. The app auto-refreshes from now on —")
    print("   no need to log in again or set PIKA_MCP_TOKEN. You can generate videos.")


if __name__ == "__main__":
    main()
