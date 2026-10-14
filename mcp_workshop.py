"""Small live MCP identity/OBO lesson helpers; no directory provisioning or fake tokens."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
from azure.identity import InteractiveBrowserCredential
from dotenv import load_dotenv
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from .agent365_auth import BlueprintTokenResolver
from .mcp_identity_server import APPLICATION_READ_PERMISSION, READ_PERMISSION


@dataclass(frozen=True)
class MCPSettings:
    tenant_id: str
    agent_id: str
    blueprint_id: str
    api_id: str
    blueprint_secret: str = field(repr=False)
    browser_client_id: str | None = None

    @classmethod
    def from_env(cls) -> MCPSettings:
        load_dotenv(Path(__file__).with_name(".env"), override=False)

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value or "<" in value:
                raise RuntimeError(f"Set {name} in Agent_365/.env; see the MCP setup guide.")
            return value

        browser_id = os.getenv("MCP_WORKSHOP_BROWSER_CLIENT_ID", "").strip()
        return cls(
            tenant_id=str(UUID(required("A365_TENANT_ID"))),
            agent_id=str(UUID(required("A365_OBSERVABILITY_AGENT_ID"))),
            blueprint_id=str(UUID(required("A365_OBSERVABILITY_BLUEPRINT_ID"))),
            api_id=str(UUID(required("MCP_WORKSHOP_API_ID"))),
            blueprint_secret=required("A365_OBSERVABILITY_BLUEPRINT_SECRET"),
            browser_client_id=str(UUID(browser_id)) if browser_id else None,
        )


@dataclass(frozen=True)
class MCPAccess:
    token: str = field(repr=False)
    mode: str

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def sign_in_agent(settings: MCPSettings, *, on_behalf_of_user: bool = False) -> MCPAccess:
    resolver = BlueprintTokenResolver(
        tenant_id=settings.tenant_id,
        agent_id=settings.agent_id,
        blueprint_id=settings.blueprint_id,
        blueprint_secret=settings.blueprint_secret,
    )
    assertion = None
    user_id = None
    if on_behalf_of_user:
        if settings.browser_client_id is None:
            raise RuntimeError("Set MCP_WORKSHOP_BROWSER_CLIENT_ID and complete OBO setup first.")
        with InteractiveBrowserCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.browser_client_id,
            redirect_uri="http://localhost:8400",
        ) as credential:
            user_token = await asyncio.to_thread(
                credential.get_token, f"api://{settings.blueprint_id}/access_as_user"
            )
        assertion = user_token.token
        # Routing checks on tokens acquired directly from Entra; the MCP server verifies signatures.
        user_claims = jwt.decode(assertion, options={"verify_signature": False})
        if (
            user_claims.get("tid") != settings.tenant_id
            or user_claims.get("aud") != settings.blueprint_id
            or user_claims.get("azp") != settings.browser_client_id
            or "access_as_user" not in str(user_claims.get("scp", "")).split()
            or not user_claims.get("oid")
        ):
            raise ValueError("The user assertion must target this blueprint, not Graph or MCP.")
        user_id = user_claims["oid"]
    payload = await asyncio.to_thread(
        resolver.exchange_for_resource,
        f"api://{settings.api_id}/.default",
        user_assertion=assertion,
    )
    token = payload["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    if claims.get("ver") != "2.0":
        raise ValueError(
            "The MCP API did not issue a v2 access token. In the MCP API app registration "
            "(MCP_WORKSHOP_API_ID), set Manifest > api.requestedAccessTokenVersion to 2 "
            "and save. Restart the kernel and request a fresh token. "
            "Using the v2 token endpoint alone does not select the access-token version."
        )
    for claim, expected, setting in (
        ("tid", settings.tenant_id, "A365_TENANT_ID"),
        ("aud", settings.api_id, "MCP_WORKSHOP_API_ID"),
        ("azp", settings.agent_id, "A365_OBSERVABILITY_AGENT_ID"),
    ):
        if claims.get(claim) != expected:
            raise ValueError(
                f"The MCP token's {claim} claim does not match {setting}. "
                "Verify the configured tenant, MCP API client ID, and child agent client ID; "
                "do not substitute object IDs. Restart the kernel after updating .env."
            )
    permissions = (
        str(claims.get("scp", "")).split() if on_behalf_of_user else claims.get("roles", [])
    )
    permission = READ_PERMISSION if on_behalf_of_user else APPLICATION_READ_PERMISSION
    if permission not in permissions:
        raise PermissionError(
            f"Grant {permission} to this agent for the MCP API, then retry."
        )
    if on_behalf_of_user and claims.get("oid") != user_id:
        raise ValueError("The OBO token does not belong to the signed-in user.")
    if not on_behalf_of_user and claims.get("scp"):
        raise ValueError("Expected an agent-only token, not a delegated token.")
    mode = "OBO (agent + user)" if on_behalf_of_user else "app-only (agent)"
    print(f"Acquired {mode} token for the MCP API. The token stays private.")
    return MCPAccess(token=token, mode=mode)


@asynccontextmanager
async def local_mcp_server(settings: MCPSettings) -> AsyncGenerator[str]:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    run_id = str(uuid4())
    environment = os.environ.copy()
    environment.update(
        A365_TENANT_ID=settings.tenant_id,
        A365_OBSERVABILITY_AGENT_ID=settings.agent_id,
        MCP_WORKSHOP_API_ID=settings.api_id,
        MCP_WORKSHOP_PORT=str(port),
        MCP_WORKSHOP_RUN_ID=run_id,
    )
    for name in ("A365_OBSERVABILITY_BLUEPRINT_SECRET", "A365_CLIENT_SECRET"):
        environment.pop(name, None)
    # Windows notebook selector loops do not implement asyncio subprocess transports.
    process = subprocess.Popen(
        [sys.executable, "-m", "Agent_365.mcp_identity_server"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
    )
    origin = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1) as http:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("The local MCP server failed to start; see terminal errors.")
                try:
                    response = await http.get(f"{origin}/healthz")
                except (httpx.ConnectError, httpx.TimeoutException):
                    await asyncio.sleep(0.1)
                    continue
                response.raise_for_status()
                if response.json() != {"run_id": run_id}:
                    raise RuntimeError("Another process answered the workshop readiness check.")
                break
            else:
                raise RuntimeError("The local MCP server did not become ready within 10 seconds.")
        print("Started authenticated MCP server on loopback; it stops when this cell finishes.")
        yield f"{origin}/mcp"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)


@asynccontextmanager
async def mcp_session(url: str, access: MCPAccess) -> AsyncGenerator[ClientSession]:
    endpoint = urlsplit(url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname != "127.0.0.1"
        or endpoint.path != "/mcp"
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("The workshop only sends MCP tokens to its loopback /mcp endpoint.")
    async with (
        httpx.AsyncClient(
            headers=access.headers(), trust_env=False, follow_redirects=False, timeout=30
        ) as http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def result_text(result: types.CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, types.TextContent))


async def explore_mcp_identity(settings: MCPSettings, access: MCPAccess) -> None:
    async with local_mcp_server(settings) as url:
        async with httpx.AsyncClient(trust_env=False) as anonymous:
            response = await anonymous.post(
                url, headers={"Accept": "application/json, text/event-stream"}, json={}
            )
            if response.status_code != 401:
                raise RuntimeError("Expected anonymous MCP access to be rejected with HTTP 401.")
            print("No token -> HTTP 401: authentication required.")
        async with mcp_session(url, access) as session:
            tools = await session.list_tools()
            print("Tools authorized before any model runs:", ", ".join(t.name for t in tools.tools))
            identity = await session.call_tool("who_am_i", {})
            if identity.isError:
                raise RuntimeError(result_text(identity))
            details = json.loads(result_text(identity))
            print("Agent:", details["agent_client_id"])
            print("Using:", details["mode"])
            print("Allowed permission:", ", ".join(details["permissions"]))
            user = await session.call_tool("user_context", {})
            if access.mode == "app-only (agent)":
                if not user.isError or "needs OBO" not in result_text(user):
                    raise RuntimeError("Expected the server to deny user_context without OBO.")
                print(
                    "Direct call to hidden user_context -> denied: an agent-only token has no user."
                )
            else:
                if user.isError:
                    raise RuntimeError(result_text(user))
                details = json.loads(result_text(user))
                print("Acting for user:", details["user_object_id"])
                print("The agent still has its own ID:", details["agent_client_id"])


async def run_mcp_assistant(settings: MCPSettings, access: MCPAccess) -> None:
    from agent_framework import Agent, MCPStreamableHTTPTool
    from agent_framework.openai import OpenAIChatCompletionClient

    from .workshop_helpers import connect_model, prepare_workshop

    model_name = prepare_workshop()
    async with (
        local_mcp_server(settings) as url,
        httpx.AsyncClient(
            headers=access.headers(), trust_env=False, follow_redirects=False
        ) as http,
        MCPStreamableHTTPTool(
            name="identity-workshop",
            url=url,
            http_client=http,
            load_prompts=False,
        ) as mcp_tools,
        connect_model() as model,
    ):
        assistant = Agent(
            id=settings.agent_id,
            name="Identity learning assistant",
            client=OpenAIChatCompletionClient(model=model_name, async_client=model),
            instructions=(
                "Call who_am_i once. If user_context is available, call it once too. "
                "Explain app-only versus OBO in three short sentences. "
                "Never invent a user or claim a denied tool succeeded."
            ),
            tools=[mcp_tools],
        )
        answer = await assistant.run(
            "Explain whose identity and permission you are using.", options={"max_tokens": 512}
        )
    print(answer.text)
