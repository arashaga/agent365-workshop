"""Helper code for the Agent 365 workshop notebooks.

You do not need to read this file to follow the lessons. It contains:

* loading settings from ``Agent_365/.env``
* displaying simple result tables
* a local Microsoft Graph simulator with sample agents
* a real Microsoft Graph client for live mode
* local replacements for services this notebook cannot connect to
* Microsoft Graph calls used by the lessons
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from IPython.display import Markdown, display

HERE = Path(__file__).resolve().parent
GRAPH = "https://graph.microsoft.com"


# ── Settings ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Settings:
    live: bool
    allow_writes: bool
    tenant_id: str
    client_id: str
    client_secret: str
    owner_id: str
    aoai_endpoint: str
    aoai_deployment: str
    aoai_api_version: str
    env_file: Path | None

    @property
    def llm_configured(self) -> bool:
        return bool(self.aoai_endpoint and self.aoai_deployment)


def _load_env_file(path: Path) -> None:
    """KEY=VALUE loader. Never overrides variables that are already set; never prints values."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings() -> Settings:
    env_file = HERE / ".env"
    if env_file.is_file():
        _load_env_file(env_file)

    def flag(name: str) -> bool:
        return os.getenv(name, "false").strip().lower() == "true"

    return Settings(
        live=flag("A365_LIVE"),
        allow_writes=flag("A365_ALLOW_WRITES"),
        tenant_id=os.getenv("A365_TENANT_ID") or os.getenv("ENTRA_TENANT_ID", ""),
        client_id=os.getenv("A365_CLIENT_ID", ""),
        client_secret=os.getenv("A365_CLIENT_SECRET", ""),
        owner_id=os.getenv("A365_OWNER_OBJECT_ID", ""),
        aoai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        aoai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
        aoai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
        env_file=env_file if env_file.is_file() else None,
    )


# ── Display ─────────────────────────────────────────────────────────────────────────────────
def show_table(rows: list[dict], title: str | None = None) -> None:
    """Render a list of dicts as a Markdown table (column order = first row's keys)."""
    if not rows:
        display(Markdown(f"**{title}**\n\n_(no rows)_" if title else "_(no rows)_"))
        return
    cols = list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in rows:
        cells = [str(row.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    display(Markdown((f"**{title}**\n\n" if title else "") + "\n".join(lines)))


# ── LLM client ──────────────────────────────────────────────────────────────────────────────
def make_openai_client(settings: Settings):
    """Azure OpenAI client that signs in with your `az login` (no API keys). None if not set up."""
    if not settings.llm_configured:
        return None
    from azure.identity import (
        AzureCliCredential,
        ChainedTokenCredential,
        DefaultAzureCredential,
        get_bearer_token_provider,
    )
    from openai import AzureOpenAI

    scope = "https://cognitiveservices.azure.com/.default"
    # If your `az login` / VS Code default tenant differs from the tenant that owns the
    # Azure OpenAI resource, DefaultAzureCredential still hands back a token for your
    # default tenant, and Azure OpenAI rejects it with: "Token tenant ... does not match
    # resource tenant." Pinning AzureCliCredential to the workshop tenant (when known)
    # avoids that mismatch; DefaultAzureCredential remains a fallback for other sign-in methods.
    credential = (
        ChainedTokenCredential(AzureCliCredential(tenant_id=settings.tenant_id), DefaultAzureCredential())
        if settings.tenant_id
        else DefaultAzureCredential()
    )
    return AzureOpenAI(
        azure_endpoint=settings.aoai_endpoint,
        api_version=settings.aoai_api_version,
        azure_ad_token_provider=get_bearer_token_provider(credential, scope),
    )


# ── Tokens (JWT) ────────────────────────────────────────────────────────────────────────────
def decode_jwt_claims(token: str) -> dict:
    """Read the fields inside an access token. No signature check: for display only."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def make_unsigned_jwt(claims: dict) -> str:
    """A FAKE token with the given fields (OFFLINE mode only; no service would accept it)."""

    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}.fake-signature"


# Turn recorded agent activities into simple tables.
def _ms(span) -> int:
    return round((span.end_time - span.start_time) / 1_000_000)


def summarize_spans(spans: list) -> None:
    """Show totals, model calls, and the order of recorded activities."""
    attrs = [dict(s.attributes or {}) for s in spans]
    llm = [
        (s, a)
        for s, a in zip(spans, attrs, strict=True)
        if a.get("gen_ai.operation.name") == "Chat"
    ]
    tools = [a for a in attrs if a.get("gen_ai.operation.name") == "execute_tool"]
    turns = [a for a in attrs if a.get("gen_ai.operation.name") == "invoke_agent"]
    handoffs = [
        f"{a['microsoft.a365.caller.agent.name']} → {a.get('gen_ai.agent.name')}"
        for a in turns
        if a.get("microsoft.a365.caller.agent.name")
    ]
    root = max(spans, key=_ms)

    def total(key: str) -> int:
        return sum(int(a.get(key) or 0) for _, a in llm)

    show_table(
        [
            {"what": "AI model calls", "value": len(llm)},
            {
                "what": "input tokens (sent to the model)",
                "value": total("gen_ai.usage.input_tokens"),
            },
            {
                "what": "input tokens reused by the service",
                "value": total("contoso.usage.cached_input_tokens"),
            },
            {
                "what": "output tokens (written by the model)",
                "value": total("gen_ai.usage.output_tokens"),
            },
            {
                "what": "extra reasoning tokens used by the model",
                "value": total("contoso.usage.reasoning_tokens"),
            },
            {
                "what": "tool calls",
                "value": ", ".join(a.get("gen_ai.tool.name", "?") for a in tools),
            },
            {
                "what": "agents involved",
                "value": ", ".join(a.get("gen_ai.agent.name", "?") for a in turns),
            },
            {"what": "agents that asked another agent for help", "value": ", ".join(handoffs) or "none"},
            {"what": "total time", "value": f"{_ms(root)} ms"},
        ],
        title="Summary of recorded agent activities",
    )

    show_table(
        [
            {
                "#": i,
                "agent": a.get("gen_ai.agent.name"),
                "model": a.get("gen_ai.request.model"),
                "input tokens": a.get("gen_ai.usage.input_tokens"),
                "cached": a.get("contoso.usage.cached_input_tokens", 0),
                "output tokens": a.get("gen_ai.usage.output_tokens"),
                "reasoning": a.get("contoso.usage.reasoning_tokens", 0),
                "time": f"{_ms(s)} ms",
            }
            for i, (s, a) in enumerate(sorted(llm, key=lambda x: x[0].start_time), start=1)
        ],
        title="One row for each AI model call",
    )

    ids = {s.context.span_id for s in spans}
    children: dict = {}
    roots = []
    for s in spans:
        parent = s.parent.span_id if s.parent else None
        (children.setdefault(parent, []).append(s) if parent in ids else roots.append(s))
    lines: list[str] = []

    def walk(s, depth: int) -> None:
        a = s.attributes or {}
        label = f"{'    ' * depth}└─ {s.name}  [{_ms(s)} ms]"
        if a.get("contoso.business_domain"):
            label += f"  (labels: org={a.get('contoso.org')}, domain={a.get('contoso.business_domain')})"
        lines.append(label)
        for c in sorted(children.get(s.context.span_id, []), key=lambda x: x.start_time):
            walk(c, depth + 1)

    for r in sorted(roots, key=lambda x: x.start_time):
        walk(r, 0)
    display(
        Markdown(
            "**Order of agent activities (each line is one recorded action)**\n\n"
            "```text\n" + "\n".join(lines) + "\n```"
        )
    )


# Local helpers for the notification example.
class MiniRouter:
    """Stand-in for `AgentApplication`: AgentNotification only needs `add_route()`."""

    def __init__(self) -> None:
        self.routes: list = []

    def add_route(self, selector, handler, **kwargs) -> None:
        self.routes.append((selector, handler))

    async def deliver(self, activity) -> list:
        """Deliver one incoming activity to the first matching handler; return its replies."""
        context = FakeTurnContext(activity)
        for selector, handler in self.routes:
            if selector(context):
                await handler(context, None)
                return context.replies
        print("   (no handler matched)")
        return []


class FakeTurnContext:
    def __init__(self, activity) -> None:
        self.activity, self.replies = activity, []

    async def send_activity(self, activity) -> None:
        self.replies.append(activity)


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


# ── Microsoft Graph: response object, LIVE client, OFFLINE simulator ─────────────────────────
class GraphResponse:
    def __init__(self, status: int, body) -> None:
        self.status, self.body = status, body

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def value(self) -> list:
        return self.body.get("value", []) if isinstance(self.body, dict) else []

    def short(self) -> str:
        """One-line outcome for tables, e.g. '✅ 2 found' or '❌ 400 BadRequest'."""
        if self.status == 0:
            return "📝 dry-run"
        if self.ok:
            return (
                f"✅ {len(self.value())} found"
                if "value" in (self.body or {})
                else f"✅ {self.status}"
            )
        err = (self.body or {}).get("error", {}) if isinstance(self.body, dict) else {}
        return f"❌ {self.status} {err.get('code', '')}".strip()


def _error(status: int, code: str, message: str) -> GraphResponse:
    return GraphResponse(status, {"error": {"code": code, "message": message}})


class LiveGraph:
    """Real Microsoft Graph, signed in with the notebook's app registration (see LIVE_SETUP.md)."""

    READ_SCOPES = [
        "CopilotPackages.Read.All",
        "AgentRegistration.Read.All",
        "AgentIdentity.Read.All",
        "AgentIdentityBlueprint.Read.All",
        "CustomSecAttributeAssignment.Read.All",
    ]
    WRITE_SCOPES = [
        "AgentRegistration.ReadWrite.All",
        "AgentIdentity.ReadWrite.All",
        "AgentIdentityBlueprint.ReadWrite.All",
        "CustomSecAttributeAssignment.ReadWrite.All",
        "CustomSecAttributeDefinition.ReadWrite.All",
    ]

    def __init__(self, settings: Settings, scopes: list[str] | None = None) -> None:
        from azure.identity import ClientSecretCredential, InteractiveBrowserCredential

        if not (settings.tenant_id and settings.client_id):
            raise ValueError(
                "LIVE mode needs A365_TENANT_ID and A365_CLIENT_ID (see LIVE_SETUP.md)."
            )
        if settings.client_secret:
            self._credential = ClientSecretCredential(
                settings.tenant_id, settings.client_id, settings.client_secret
            )
            self._scopes = [f"{GRAPH}/.default"]
        else:
            wanted = scopes or self.READ_SCOPES + (
                self.WRITE_SCOPES if settings.allow_writes else []
            )
            self._credential = InteractiveBrowserCredential(
                tenant_id=settings.tenant_id, client_id=settings.client_id
            )
            self._scopes = [f"{GRAPH}/{s}" for s in wanted]

    def request(
        self, method: str, path: str, *, params=None, headers=None, json_body=None, retries: int = 0
    ) -> GraphResponse:
        import requests

        token = self._credential.get_token(*self._scopes).token
        hdrs = {"Authorization": f"Bearer {token}", "OData-Version": "4.0", **(headers or {})}
        for attempt in range(retries + 1):
            r = requests.request(
                method, GRAPH + path, params=params, headers=hdrs, json=json_body, timeout=30
            )
            if r.ok or r.status_code not in (400, 404) or attempt == retries:
                break
            time.sleep(5 * (attempt + 1))  # new Entra objects take a few seconds to replicate
        is_json = r.content and "json" in r.headers.get("content-type", "")
        return GraphResponse(r.status_code, r.json() if is_json else (r.text or None))


_ANY = re.compile(r"^(\w+)/any\(\s*(\w+)\s*:\s*\2\s+eq\s+'([^']*)'\s*\)$", re.I)
_CSA = re.compile(r"^customSecurityAttributes/(\w+)/(\w+)\s+(eq|ne)\s+'([^']*)'$", re.I)
_SW = re.compile(r"^startswith\(\s*(\w+)\s*,\s*'([^']*)'\s*\)$", re.I)
_CMP = re.compile(r"^(\w+)\s+(eq|ne|ge|le)\s+'?([^']*?)'?$", re.I)


def _split_and(expr: str) -> list[str]:
    return [c.strip() for c in re.split(r"\s+and\s+", expr.strip(), flags=re.I) if c.strip()]


def _clause_property(clause: str) -> str:
    m = re.match(r"^(?:startswith\(\s*)?(\w+)", clause, re.I)
    return m.group(1) if m else clause


def _match(clause: str, obj: dict) -> bool:
    if m := _ANY.match(clause):
        return m.group(3).lower() in [str(v).lower() for v in obj.get(m.group(1)) or []]
    if m := _CSA.match(clause):
        value = ((obj.get("customSecurityAttributes") or {}).get(m.group(1)) or {}).get(m.group(2))
        return (value == m.group(4)) == (m.group(3).lower() == "eq")
    if m := _SW.match(clause):
        return str(obj.get(m.group(1)) or "").lower().startswith(m.group(2).lower())
    if m := _CMP.match(clause):
        left, op, right = str(obj.get(m.group(1)) or ""), m.group(2).lower(), m.group(3)
        return {
            "eq": left.lower() == right.lower(),
            "ne": left.lower() != right.lower(),
            "ge": left >= right,
            "le": left <= right,
        }[op]
    raise ValueError(f"simulator cannot parse filter clause: {clause}")


class SimulatedGraph:
    """OFFLINE stand-in for Microsoft Graph.

    Holds FAKE sample agents and answers the way Microsoft's documentation says the real
    service does, including its limits (which filters are allowed, which headers are required,
    which operations don't exist). Error messages are illustrative, not the real wording.
    """

    PACKAGE_FILTERABLE = {"supportedHosts", "elementTypes", "platform", "lastModifiedDateTime"}

    def __init__(
        self, packages: list, package_details: dict, blueprints: dict, identities: dict
    ) -> None:
        self.packages = {p["id"]: p for p in packages}
        self.package_details = package_details
        self.blueprints = blueprints
        self.identities = identities
        self.registrations: dict = {}

    def request(
        self, method: str, path: str, *, params=None, headers=None, json_body=None, retries: int = 0
    ) -> GraphResponse:
        params, m, p = params or {}, method.upper(), path.rstrip("/")
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        body = json_body or {}
        if m == "GET" and re.fullmatch(r"/(v1\.0|beta)/copilot/admin/catalog/packages", p):
            return self._list_packages(params)
        if m == "GET" and (
            mt := re.fullmatch(r"/(?:v1\.0|beta)/copilot/admin/catalog/packages/(.+)", p)
        ):
            detail = self.package_details.get(mt.group(1))
            return GraphResponse(200, detail) if detail else _error(404, "NotFound", "No package.")
        if p == "/beta/copilot/agentRegistrations":
            if m == "POST":
                return self._create_registration(body)
            return _error(
                405, "MethodNotAllowed", "Docs list create/get/update/delete only (no list)."
            )
        if mt := re.fullmatch(r"/beta/copilot/agentRegistrations/([^/]+)", p):
            rid = mt.group(1)
            if rid not in self.registrations:
                return _error(404, "NotFound", "Registration not found.")
            if m == "GET":
                return GraphResponse(200, self.registrations[rid])
            if m == "DELETE":
                del self.registrations[rid]
                return GraphResponse(204, None)
        if m == "GET" and p == "/v1.0/servicePrincipals/microsoft.graph.agentIdentity":
            return self._list_identities(params, headers)
        if m == "PATCH" and (
            mt := re.fullmatch(
                r"/v1\.0/servicePrincipals/([^/]+)/microsoft\.graph\.agentIdentity", p
            )
        ):
            return self._patch_identity(mt.group(1), body)
        if m == "PATCH" and (
            mt := re.fullmatch(
                r"/v1\.0/applications/([^/]+)/microsoft\.graph\.agentIdentityBlueprint", p
            )
        ):
            bp = self.blueprints.get(mt.group(1))
            if not bp:
                return _error(404, "Request_ResourceNotFound", "Blueprint not found.")
            bp.update({k: v for k, v in body.items() if k == "tags"})
            return GraphResponse(204, None)
        if m == "POST" and p in (
            "/v1.0/directory/attributeSets",
            "/v1.0/directory/customSecurityAttributeDefinitions",
        ):
            return GraphResponse(201, body)
        return _error(400, "BadRequest", f"simulator has no route for {m} {p}")

    def _list_packages(self, params: dict) -> GraphResponse:
        items = list(self.packages.values())
        for clause in _split_and(params.get("$filter", "")):
            prop = _clause_property(clause)
            if prop not in self.PACKAGE_FILTERABLE:
                allowed = ", ".join(sorted(self.PACKAGE_FILTERABLE))
                return _error(400, "BadRequest", f"Can't filter on '{prop}'. Allowed: {allowed}.")
            items = [i for i in items if _match(clause, i)]
        return GraphResponse(200, {"value": items})

    def _with_blueprint_tags(self, ident: dict) -> dict:
        # Documented behavior: an identity's tags = its own tags + its blueprint's tags.
        bp_tags = next(
            (
                b.get("tags", [])
                for b in self.blueprints.values()
                if b["appId"] == ident["agentIdentityBlueprintId"]
            ),
            [],
        )
        return {**ident, "tags": sorted(set(ident.get("tags", [])) | set(bp_tags))}

    def _list_identities(self, params: dict, headers: dict) -> GraphResponse:
        flt = params.get("$filter", "")
        if params.get("$search"):
            # Verified against a real tenant (Sept 2026): $search is rejected on this endpoint.
            # Use $filter=startswith(displayName,'…') here, or $search on /servicePrincipals.
            return _error(
                400,
                "Request_UnsupportedQuery",
                "Property 'displayName' does not exist as a declared property.",
            )
        advanced = (
            headers.get("consistencylevel") == "eventual"
            and str(params.get("$count", "")).lower() == "true"
        )
        if "customSecurityAttributes/" in flt and not advanced:
            return _error(
                400,
                "Request_UnsupportedQuery",
                "Needs header 'ConsistencyLevel: eventual' and $count=true.",
            )
        items = [self._with_blueprint_tags(i) for i in self.identities.values()]
        for clause in _split_and(flt):
            items = [i for i in items if _match(clause, i)]
        select = {s.strip() for s in params.get("$select", "").split(",") if s.strip()}
        if "customSecurityAttributes" not in select:  # documented: only returned when selected
            items = [{k: v for k, v in i.items() if k != "customSecurityAttributes"} for i in items]
        return GraphResponse(200, {"value": items})

    def _patch_identity(self, sp_id: str, body: dict) -> GraphResponse:
        ident = self.identities.get(sp_id)
        if not ident:
            return _error(404, "Request_ResourceNotFound", "Agent identity not found.")
        if "tags" in body:
            ident["tags"] = list(body["tags"])
        for attr_set, values in (body.get("customSecurityAttributes") or {}).items():
            clean = {k: v for k, v in values.items() if "@odata.type" not in k}
            ident.setdefault("customSecurityAttributes", {}).setdefault(attr_set, {}).update(clean)
        return GraphResponse(204, None)

    def _create_registration(self, body: dict) -> GraphResponse:
        required = (
            "displayName",
            "createdBy",
            "sourceCreatedDateTime",
            "sourceLastModifiedDateTime",
        )
        missing = [k for k in required if not body.get(k)]
        if missing:
            return _error(400, "BadRequest", f"Missing required properties: {missing}")
        rid = str(uuid.uuid4())
        self.registrations[rid] = {"id": rid, **body}
        return GraphResponse(201, {"id": rid})


def build_simulated_tenant(agents: list[dict]) -> SimulatedGraph:
    """FAKE tenant: catalog packages, blueprints and agent identities for the sample agents."""
    # Per agent: (type, publisher, platform, hosts, elementTypes, blocked, availableTo,
    #             categories, groups)
    catalog = {
        "risk-intel": (
            "custom",
            "contoso Risk Office",
            "web",
            ["Copilot", "Teams"],
            ["CustomEngineAgent"],
            False,
            "some",
            ["Risk", "Supply Chain"],
            ["grp-risk-analysts"],
        ),
        "supplier-360": (
            "external",
            "SAP",
            "web",
            ["Copilot", "Teams"],
            ["Bots"],
            False,
            "all",
            ["Procurement"],
            [],
        ),
        "policy-finder": (
            "external",
            "Glean",
            "web",
            ["Copilot"],
            ["Bots"],
            True,
            "all",
            ["Compliance", "Search"],
            [],
        ),
        "contract-summarizer": (
            "custom",
            "contoso Legal",
            "Copilot Studio",
            ["Copilot", "Teams"],
            ["DeclarativeAgent"],
            False,
            "some",
            ["Legal"],
            ["grp-legal"],
        ),
    }  # order-tracker (an on-prem Java app) is intentionally NOT in the Microsoft 365 catalog
    packages, details = [], {}
    for a in agents:
        if a["key"] not in catalog:
            continue
        ptype, publisher, platform, hosts, elements, blocked, available, cats, groups = catalog[
            a["key"]
        ]
        base = {
            "id": f"P_{a['key']}",
            "displayName": a["name"],
            "type": ptype,
            "shortDescription": a["description"],
            "isBlocked": blocked,
            "availableTo": available,
            "supportedHosts": hosts,
            "elementTypes": elements,
            "platform": platform,
            "publisher": publisher,
            "appId": a["blueprint_app_id"],
            "lastModifiedDateTime": "2026-09-01T10:00:00Z",
        }
        packages.append(base)
        details[base["id"]] = {
            **base,
            "categories": cats,
            "sensitivity": a["classification"],
            "allowedUsersAndGroups": [{"resourceType": "group", "resourceId": g} for g in groups],
        }
    blueprints = {
        a["blueprint_object_id"]: {"appId": a["blueprint_app_id"], "tags": []} for a in agents
    }
    identities = {
        a["identity_id"]: {
            "@odata.type": "#microsoft.graph.agentIdentity",
            "id": a["identity_id"],
            "displayName": a["name"],
            "agentIdentityBlueprintId": a["blueprint_app_id"],
            "tags": [],
        }
        for a in agents
    }
    blueprints["bp-obj-hr"] = {"appId": "a0000009-0000-4000-8000-000000000009", "tags": []}
    identities["5e100009-hr"] = {
        "@odata.type": "#microsoft.graph.agentIdentity",
        "id": "5e100009-hr",
        "displayName": "HR Onboarding Bot",
        "agentIdentityBlueprintId": "a0000009-0000-4000-8000-000000000009",
        "tags": [],
    }
    return SimulatedGraph(packages, details, blueprints, identities)


# ── Discovery facade helpers (lab 2) ─────────────────────────────────────────────────────────
# FIVE MADE-UP contoso AGENTS used in OFFLINE mode. The IDs are fake; nothing here exists in Entra.
SAMPLE_AGENTS: list[dict] = [
    {
        "key": "risk-intel",
        "name": "Risk Intelligence Agent",
        "platform": "Azure AI Foundry",
        "description": "Reads news, finds company names and flags supplier risk (e.g. sanctions).",
        "org": "contoso-risk-office",
        "domain": "supplier-risk",
        "classification": "Confidential",
        "capabilities": ["article-classification", "summarization", "ner", "risk-detection"],
        "url": "https://riskintel.contoso.contoso.com/a2a",
        "protocol": "HTTP+JSON",
        "blueprint_object_id": "bp-obj-risk",
        "blueprint_app_id": "a0000001-0000-4000-8000-000000000001",
        "identity_id": "5e100001-0000-4000-8000-000000000001",
    },
    {
        "key": "supplier-360",
        "name": "Supplier 360 (SAP Joule)",
        "platform": "SAP Joule",
        "description": "Answers supplier master-data and purchase-order questions from SAP.",
        "org": "contoso-procurement",
        "domain": "supplier-master-data",
        "classification": "Internal",
        "capabilities": ["supplier-lookup", "po-status"],
        "url": "https://joule.sap.contoso.com/agents/supplier360",
        "protocol": "JSONRPC",
        "blueprint_object_id": "bp-obj-sap",
        "blueprint_app_id": "a0000002-0000-4000-8000-000000000002",
        "identity_id": "5e100002-0000-4000-8000-000000000002",
    },
    {
        "key": "policy-finder",
        "name": "Policy Finder (Glean)",
        "platform": "Glean",
        "description": "Searches contoso policies, procedures and compliance guidance.",
        "org": "contoso-compliance",
        "domain": "policy-compliance",
        "classification": "Internal",
        "capabilities": ["enterprise-search", "policy-qa", "summarization"],
        "url": "https://glean.contoso.com/agents/policy-finder",
        "protocol": "HTTP+JSON",
        "blueprint_object_id": "bp-obj-glean",
        "blueprint_app_id": "a0000003-0000-4000-8000-000000000003",
        "identity_id": "5e100003-0000-4000-8000-000000000003",
    },
    {
        "key": "order-tracker",
        "name": "Order Tracker (on-prem Java app)",
        "platform": "On-prem Java",
        "description": "An existing Java order system, exposed to agents through an MCP adapter.",
        "org": "contoso-logistics",
        "domain": "order-management",
        "classification": "Internal",
        "capabilities": ["order-status", "shipment-tracking"],
        "url": "https://orders.contoso.contoso.com/mcp",
        "protocol": "MCP",
        "blueprint_object_id": "bp-obj-orders",
        "blueprint_app_id": "a0000004-0000-4000-8000-000000000004",
        "identity_id": "5e100004-0000-4000-8000-000000000004",
    },
    {
        "key": "contract-summarizer",
        "name": "Contract Summarizer",
        "platform": "Copilot Studio",
        "description": "Summarizes supplier contracts and pulls out risky clauses.",
        "org": "contoso-legal",
        "domain": "contract-management",
        "classification": "Confidential",
        "capabilities": ["summarization", "clause-extraction", "risk-detection"],
        "url": "https://copilotstudio.contoso.com/agents/contract-summarizer",
        "protocol": "HTTP+JSON",
        "blueprint_object_id": "bp-obj-legal",
        "blueprint_app_id": "a0000005-0000-4000-8000-000000000005",
        "identity_id": "5e100005-0000-4000-8000-000000000005",
    },
]


def live_demo_agent() -> dict:
    """LIVE mode: the first sample agent, pointed at YOUR test identity (A365_DEMO_* settings)."""
    return {
        **SAMPLE_AGENTS[0],
        "identity_id": os.getenv("A365_DEMO_AGENT_IDENTITY_ID", ""),
        "blueprint_object_id": os.getenv("A365_DEMO_BLUEPRINT_OBJECT_ID", ""),
        "blueprint_app_id": os.getenv("A365_DEMO_BLUEPRINT_APP_ID", ""),
    }


@dataclass
class AgentRecord:
    """Everything the directory knows about one agent, merged from the three sources."""

    identity_id: str
    name: str
    org: str | None
    domain: str | None
    capabilities: list[str]
    description: str = ""
    endpoint: str | None = None
    protocol: str | None = None
    data_classification: str = "Internal"
    package: dict | None = None
    sources: list[str] = field(default_factory=list)


class AgentDirectory:
    """Reads Entra Agent ID (labels), Agent 365 registrations (business cards) and the catalog."""

    PKG = "/v1.0/copilot/admin/catalog/packages"
    IDENTITIES = "/v1.0/servicePrincipals/microsoft.graph.agentIdentity"

    def __init__(
        self, graph, registration_by_identity: dict[str, str], card_extension_uri: str
    ) -> None:
        self.graph = graph
        self.registration_by_identity = registration_by_identity
        self.card_extension_uri = card_extension_uri
        self.package_by_app: dict = {}
        for p in graph.request("GET", self.PKG).value():
            detail = graph.request("GET", f"{self.PKG}/{p['id']}")
            self.package_by_app[p.get("appId")] = detail.body if detail.ok else p

    @staticmethod
    def label_filter(org: str | None, domain: str | None, capabilities: list[str]) -> str:
        clauses = [f"tags/any(t:t eq 'contoso:org={org}')"] if org else []
        clauses += [f"tags/any(t:t eq 'contoso:domain={domain}')"] if domain else []
        clauses += [f"tags/any(t:t eq 'contoso:cap={c}')" for c in capabilities]
        return " and ".join(clauses)

    def search_by_labels(self, org=None, domain=None, capabilities=()) -> list[AgentRecord]:
        params = {"$select": "id,displayName,tags,agentIdentityBlueprintId"}
        if flt := self.label_filter(org, domain, list(capabilities)):
            params["$filter"] = flt
        r = self.graph.request("GET", self.IDENTITIES, params=params)
        return [self._merge(i) for i in r.value()] if r.ok else []

    def _merge(self, ident: dict) -> AgentRecord:
        def label(prefix: str) -> list[str]:
            return [
                t.split("=", 1)[1] for t in ident.get("tags", []) if t.startswith(f"contoso:{prefix}=")
            ]

        rec = AgentRecord(
            identity_id=ident["id"],
            name=ident.get("displayName", ""),
            org=next(iter(label("org")), None),
            domain=next(iter(label("domain")), None),
            capabilities=label("cap"),
            sources=["Entra Agent ID"],
        )
        if rid := self.registration_by_identity.get(ident["id"]):
            r = self.graph.request("GET", f"/beta/copilot/agentRegistrations/{rid}")
            if r.ok:
                card = r.body.get("agentCard") or {}
                ext = next(
                    (
                        e.get("params", {})
                        for e in card.get("capabilities", {}).get("extensions", [])
                        if e.get("uri") == self.card_extension_uri
                    ),
                    {},
                )
                rec.description, rec.endpoint = card.get("description", ""), card.get("url")
                rec.protocol = card.get("preferredTransport")
                rec.data_classification = ext.get("dataClassification", rec.data_classification)
                rec.sources.append("Agent 365 registration")
        if pkg := self.package_by_app.get(ident.get("agentIdentityBlueprintId")):
            rec.package = pkg
            rec.sources.append("Agent 365 catalog")
        return rec


def rank_by_keywords(
    text: str | None, records: list[AgentRecord]
) -> list[tuple[AgentRecord, float]]:
    """Stand-in for an LLM/embedding ranker: share of query words found in the agent's text."""
    if not text:
        return [(r, 0.0) for r in records]
    words = {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}
    scored = []
    for r in records:
        hay = set(
            re.findall(
                r"[a-z0-9]+",
                f"{r.name} {r.description} {r.domain} {' '.join(r.capabilities)}".lower(),
            )
        )
        scored.append((r, round(len(words & hay) / max(len(words), 1), 2)))
    return sorted(scored, key=lambda x: x[1], reverse=True)


# ── Entra Agent ID provisioning (lab 3) ──────────────────────────────────────────────────────
def provision_agent_team(
    graph: LiveGraph,
    blueprint_name: str,
    workers: dict[str, str],
    grants: dict[str, list[str]],
    owner_id: str,
) -> tuple[dict, dict]:
    """Create (or reuse) a blueprint, its principal, one agent identity per worker, and grants.

    Returns (blueprint, agents). Every call is a plain Microsoft Graph v1.0 request.
    """

    def call(method: str, path: str, body: dict | None = None) -> dict:
        r = graph.request(method, path, json_body=body, retries=5)
        if not r.ok:
            raise RuntimeError(f"{method} {path} → {r.status}: {str(r.body)[:300]}")
        return r.body or {}

    def first(path: str) -> dict | None:
        return next(iter(call("GET", path).get("value", [])), None)

    sponsor = {"sponsors@odata.bind": [f"{GRAPH}/v1.0/users/{owner_id}"]}
    bp_path = "/v1.0/applications/microsoft.graph.agentIdentityBlueprint"
    bp = first(f"{bp_path}?$filter=displayName eq '{blueprint_name}'")
    bp = bp or call("POST", bp_path, {"displayName": blueprint_name, **sponsor})
    principal = first(f"/v1.0/servicePrincipals?$filter=appId eq '{bp['appId']}'&$select=id")
    principal = principal or call(
        "POST",
        "/v1.0/servicePrincipals/microsoft.graph.agentIdentityBlueprintPrincipal",
        {"appId": bp["appId"]},
    )
    expires = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    secret = call(
        "POST",
        f"/v1.0/applications/{bp['id']}/microsoft.graph.agentIdentityBlueprint/addPassword",
        {"passwordCredential": {"displayName": "workshop-notebook", "endDateTime": expires}},
    )
    blueprint = {
        "id": bp["id"],
        "appId": bp["appId"],
        "principalId": principal["id"],
        "secret": secret["secretText"],
    }

    graph_sp = call(
        "GET",
        "/v1.0/servicePrincipals(appId='00000003-0000-0000-c000-000000000000')?$select=id,appRoles",
    )
    agents: dict = {}
    for role, name in workers.items():
        ident = first(
            f"/v1.0/servicePrincipals/microsoft.graph.agentIdentity?$filter=displayName eq '{name}'"
            "&$select=id,appId"
        )
        ident = ident or call(
            "POST",
            "/v1.0/servicePrincipals/microsoft.graph.agentIdentity",
            {"displayName": name, "agentIdentityBlueprintId": bp["appId"], **sponsor},
        )
        existing = {
            a["appRoleId"]
            for a in call("GET", f"/v1.0/servicePrincipals/{ident['id']}/appRoleAssignments")[
                "value"
            ]
        }
        for perm in grants.get(role, []):
            app_role = next(r for r in graph_sp["appRoles"] if r["value"] == perm)
            if app_role["id"] not in existing:
                call(
                    "POST",
                    f"/v1.0/servicePrincipals/{ident['id']}/appRoleAssignments",
                    {
                        "principalId": ident["id"],
                        "resourceId": graph_sp["id"],
                        "appRoleId": app_role["id"],
                    },
                )
        agents[role] = {"id": ident["id"], "appId": ident["appId"], "name": name}
    return blueprint, agents


def delete_agent_team(graph: LiveGraph, blueprint: dict, agents: dict) -> None:
    for a in agents.values():
        graph.request("DELETE", f"/v1.0/servicePrincipals/{a['id']}")
    graph.request("DELETE", f"/v1.0/servicePrincipals/{blueprint['principalId']}")
    graph.request("DELETE", f"/v1.0/applications/{blueprint['id']}")


class FakeAgentTokens:
    """OFFLINE: hands out FAKE tokens with the same fields a real agent token has."""

    def __init__(self, blueprint_app_id: str, roles_by_app_id: dict[str, list[str]]) -> None:
        self._bp, self._roles = blueprint_app_id, roles_by_app_id

    def token_for(self, agent_app_id: str, resource: str = GRAPH) -> str:
        return make_unsigned_jwt(
            {
                "aud": resource,
                "appid": agent_app_id,
                "idtyp": "app",
                "roles": self._roles.get(agent_app_id, []),
                "xms_par_app_azp": self._bp,
            }
        )

    def clear(self) -> None:
        pass


def fake_graph_reply(claims: dict, path: str) -> tuple[int, dict]:
    """OFFLINE: what Graph would answer for this token (3 sample users, or 403 if not allowed)."""
    if path.startswith("/v1.0/users") and "User.Read.All" in claims.get("roles", []):
        return 200, {
            "value": [
                {"displayName": "Procurement Lead", "jobTitle": "Procurement Lead"},
                {"displayName": "Risk Analyst", "jobTitle": "Risk Analyst"},
                {"displayName": "Supply Chain Manager", "jobTitle": "Supply Chain Manager"},
            ]
        }
    return 403, {"error": {"code": "Authorization_RequestDenied"}}
