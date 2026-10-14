"""Registered blueprint-agent authentication for the workshop's Agent 365 exporter."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from uuid import UUID

import httpx
import jwt

OBSERVABILITY_RESOURCE = "9b975845-388f-4429-889e-eab1ef63949c"
OBSERVABILITY_SCOPE = f"api://{OBSERVABILITY_RESOURCE}/.default"
REFRESH_SECONDS = 120


class BlueprintTokenResolver:
    def __init__(
        self, *, tenant_id: str, agent_id: str, blueprint_id: str, blueprint_secret: str
    ) -> None:
        self.tenant_id = str(UUID(tenant_id))
        self.agent_id = str(UUID(agent_id))
        self.blueprint_id = str(UUID(blueprint_id))
        if self.agent_id == self.blueprint_id:
            raise ValueError("Use the registered agent instance's client ID, not the blueprint ID.")
        if not blueprint_secret.strip():
            raise ValueError("The blueprint needs a real local-development credential.")
        self._secret = blueprint_secret
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    async def sign_in(self) -> None:
        await asyncio.to_thread(self, self.agent_id, self.tenant_id)
        print("Authenticated as the agent instance. Telemetry ingestion is not yet verified.")

    def _request_token(self, http: httpx.Client, form: dict[str, str]) -> dict:
        response = http.post(
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token", data=form
        )
        if response.is_error:
            raise RuntimeError(
                f"Entra token exchange failed (HTTP {response.status_code}). "
                "Check the agent instance ID, parent blueprint, and blueprint credential. "
                "See LIVE_SETUP.md; no token or secret has been printed."
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise ValueError("Entra did not return an access token.")
        if not payload["access_token"]:
            raise ValueError("Entra returned an empty access token.")
        return payload

    def _check_token(self, payload: dict, requested_at: float) -> tuple[str, float]:
        token = payload["access_token"]
        # These are routing checks on a token fetched directly from Entra over TLS.
        # The Agent 365 service verifies its signature; this is not an inbound JWT validator.
        claims = jwt.decode(token, options={"verify_signature": False})
        roles = claims.get("roles", [])
        app_only = claims.get("idtyp") == "app" or (
            "idtyp" not in claims
            and (
                (isinstance(roles, list) and bool(roles))
                or (bool(claims.get("oid")) and claims.get("oid") == claims.get("sub"))
            )
        )
        if (
            not app_only
            or "scp" in claims
            or not isinstance(roles, list)
            or not all(isinstance(role, str) and role.strip() for role in roles)
        ):
            raise ValueError(
                "Agent 365 S2S requires an app-only token, not a delegated user token."
            )
        clients = [claims[key] for key in ("azp", "appid") if key in claims]
        if (
            claims.get("tid") != self.tenant_id
            or not clients
            or any(client != self.agent_id for client in clients)
            or claims.get("aud") not in (OBSERVABILITY_RESOURCE, f"api://{OBSERVABILITY_RESOURCE}")
        ):
            raise ValueError("The token does not match this agent, tenant, and Agent 365 audience.")
        expires_in, expires_at = payload.get("expires_in"), claims.get("exp")
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_in)
            or not math.isfinite(expires_at)
            or expires_in <= 0
        ):
            raise ValueError("Entra did not return valid token expiry information.")
        expires_at = min(requested_at + expires_in, expires_at)
        if expires_at <= time.time() + REFRESH_SECONDS:
            raise ValueError("The Agent 365 token is expired or too close to expiry.")
        return token, expires_at

    def exchange_for_resource(self, scope: str, *, user_assertion: str | None = None) -> dict:
        """Acquire a resource token through the real agent identity chain."""
        if not scope.endswith("/.default"):
            raise ValueError("Agent resource exchanges must request a /.default scope.")
        if user_assertion is not None and not user_assertion:
            raise ValueError("OBO needs a real user access token for the blueprint.")
        with httpx.Client(timeout=30, follow_redirects=False) as http:
            parent = self._request_token(
                http,
                {
                    "grant_type": "client_credentials",
                    "client_id": self.blueprint_id,
                    "client_secret": self._secret,
                    "fmi_path": self.agent_id,
                    "scope": "api://AzureADTokenExchange/.default",
                },
            )
            form = {
                "grant_type": "client_credentials",
                "client_id": self.agent_id,
                "client_assertion": parent["access_token"],
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "scope": scope,
            }
            if user_assertion is not None:
                form.update(
                    grant_type="urn:ietf:params:oauth:grant-type:jwt-bearer",
                    assertion=user_assertion,
                    requested_token_use="on_behalf_of",
                )
            return self._request_token(http, form)

    def __call__(self, agent_id: str, tenant_id: str) -> str:
        if agent_id != self.agent_id or tenant_id != self.tenant_id:
            raise ValueError("Refusing to use this credential for a different agent or tenant.")
        with self._lock:
            if self._token and time.time() < self._expires_at - REFRESH_SECONDS:
                return self._token
            self._token = None
            self._expires_at = 0
            requested_at = time.time()
            payload = self.exchange_for_resource(OBSERVABILITY_SCOPE)
            self._token, self._expires_at = self._check_token(payload, requested_at)
            return self._token
