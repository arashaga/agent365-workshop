"""Loopback-only MCP resource server with real Entra JWT validation."""

from __future__ import annotations

import asyncio
import logging
import os
from uuid import UUID

import jwt
from jwt import PyJWKClient
from mcp import types
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse

LOGGER = logging.getLogger(__name__)
READ_PERMISSION = "Workshop.Read"
APPLICATION_READ_PERMISSION = "Workshop.Read.Application"
TRANSPORT_PERMISSION = "workshop:authorized"


class EntraMCPVerifier:
    def __init__(self, tenant_id: str, api_id: str, agent_id: str) -> None:
        self.tenant_id = str(UUID(tenant_id))
        self.api_id = str(UUID(api_id))
        self.agent_id = str(UUID(agent_id))
        self.issuer = f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        self.jwks = PyJWKClient(
            f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys",
            timeout=15,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.api_id,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "tid", "azp", "sub", "oid"]},
            )
            if (
                claims["tid"] != self.tenant_id
                or claims["azp"] != self.agent_id
                or claims.get("appid", self.agent_id) != self.agent_id
                or claims.get("ver") != "2.0"
                or not isinstance(claims["oid"], str)
                or not claims["oid"]
                or not isinstance(claims["sub"], str)
                or not claims["sub"]
            ):
                raise jwt.InvalidTokenError("Unexpected tenant, caller, or token version.")
            if "scp" in claims:
                if (
                    not isinstance(claims["scp"], str)
                    or not claims["scp"].strip()
                    or claims.get("idtyp") == "app"
                ):
                    raise jwt.InvalidTokenError("Invalid delegated permission claims.")
                permissions = claims["scp"].split()
            else:
                roles = claims.get("roles", [])
                if (
                    not isinstance(roles, list)
                    or not all(isinstance(role, str) and role for role in roles)
                    or not (
                        claims.get("idtyp") == "app"
                        or ("idtyp" not in claims and claims["oid"] == claims["sub"])
                    )
                ):
                    raise jwt.InvalidTokenError("Invalid application permission claims.")
                permissions = roles
        except jwt.PyJWKClientConnectionError as error:
            LOGGER.error("Cannot download Entra signing keys; MCP authentication unavailable.")
            raise RuntimeError("Cannot download Entra signing keys.") from error
        except jwt.PyJWTError as error:
            LOGGER.warning("Rejected MCP token: %s", type(error).__name__)
            return None
        required = READ_PERMISSION if claims.get("scp") else APPLICATION_READ_PERMISSION
        # The HTTP middleware has one gate; tools still enforce the mode-specific grant.
        if required in permissions:
            permissions = [*permissions, TRANSPORT_PERMISSION]
        return AccessToken(
            token=token,
            client_id=self.agent_id,
            scopes=permissions,
            expires_at=claims["exp"],
            resource=self.api_id,
            subject=claims["sub"],
            claims=claims,
        )


def authorized_identity(*, needs_user: bool = False) -> AccessToken:
    identity = get_access_token()
    permission = (
        READ_PERMISSION
        if identity is not None and (identity.claims or {}).get("scp")
        else APPLICATION_READ_PERMISSION
    )
    if identity is None or permission not in identity.scopes:
        LOGGER.warning("Denied MCP tool: %s is required.", permission)
        raise PermissionError(f"Access denied: {permission} is required.")
    if needs_user and not (identity.claims or {}).get("scp"):
        LOGGER.warning("Denied user_context: this token has no delegated user context.")
        raise PermissionError("Access denied: user_context needs OBO, not an agent-only token.")
    return identity


class AuthorizedMCP(FastMCP):
    async def list_tools(self) -> list[types.Tool]:
        identity = authorized_identity()
        tools = await super().list_tools()
        if not (identity.claims or {}).get("scp"):
            tools = [tool for tool in tools if tool.name != "user_context"]
        return tools


def create_server(tenant_id: str, api_id: str, agent_id: str, *, port: int, run_id: str) -> FastMCP:
    verifier = EntraMCPVerifier(tenant_id, api_id, agent_id)
    server = AuthorizedMCP(
        "Entra identity workshop",
        host="127.0.0.1",
        port=port,
        stateless_http=True,
        json_response=True,
        log_level="WARNING",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(verifier.issuer),
            resource_server_url=AnyHttpUrl(f"http://127.0.0.1:{port}/mcp"),
            required_scopes=[TRANSPORT_PERMISSION],
        ),
    )

    @server.custom_route("/healthz", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"run_id": run_id})

    @server.tool()
    def who_am_i() -> dict[str, str | list[str]]:
        """Show the authenticated agent ID and permission type, never the bearer token."""
        identity = authorized_identity()
        claims = identity.claims or {}
        return {
            "tenant_id": str(claims["tid"]),
            "agent_client_id": identity.client_id,
            "mode": "on behalf of a user" if claims.get("scp") else "agent only",
            "permissions": [p for p in identity.scopes if p != TRANSPORT_PERMISSION],
        }

    @server.tool()
    def user_context() -> dict[str, str]:
        """Show the signed-in user's verified object ID; requires delegated OBO permission."""
        identity = authorized_identity(needs_user=True)
        claims = identity.claims or {}
        return {
            "user_object_id": str(claims["oid"]),
            "agent_client_id": identity.client_id,
            "explanation": "Entra issued this token to the agent on behalf of this user.",
        }

    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    create_server(
        os.environ["A365_TENANT_ID"],
        os.environ["MCP_WORKSHOP_API_ID"],
        os.environ["A365_OBSERVABILITY_AGENT_ID"],
        port=int(os.environ["MCP_WORKSHOP_PORT"]),
        run_id=os.environ["MCP_WORKSHOP_RUN_ID"],
    ).run(transport="streamable-http")
