# Agent 365 workshop: start here

Use these short notebooks in order. **Every notebook explains its own setup,
who must do it, what to run, and what output proves it worked.**
You do not need to read the long setup guide first.

| Notebook | Learn | Additional setup |
|---|---|---|
| [00 - Identity setup](./00_Identity_Setup.ipynb) | Create/find the blueprint and child identity in Entra | Authorized Entra administrator, project Python kernel |
| [01 - Agent permissions](./01_Agent_Permissions.ipynb) | Prove allowed and denied MCP calls with real tokens | Custom MCP API and a grant to the child; steps included |
| [02 - Agent and Azure traces](./02_Agent_and_Azure_Traces.ipynb) | Run the real agent; find its execution in Azure | Existing model, CLI user access, Application Insights; steps included |
| [03 - User delegation](./03_User_Delegation.ipynb) | Compare agent-only and user-delegated access | Browser client and two separate permission links; steps included |
| [04 - Agent 365 observability](./04_Agent365_Observability.ipynb) | Add Agent 365 export without losing the Azure viewer | Assigned license, registration or telemetry grant, tenant viewer |

**Fresh kernel per notebook.** Settings and directory grants persist; Python
variables and tracing configuration should not be carried between notebooks.
Start the kernel from the project root or `Agent_365`. Notebooks 00-01 do not need
a model, Azure trace resource, or Agent 365 observability license. Notebook 04
does not require notebook 03.

## Keep these four ideas separate

- **Agent code:** the worker, running locally in the notebook.
- **Entra identity:** its badge. IDs are not credentials or permission grants.
- **Authorization:** the protected API checks the signed badge and its permission.
- **Observability:** traces describe work; Azure and Agent 365 are different destinations.

| Connection | Authenticated as |
|---|---|
| Azure model | Your Azure CLI user |
| Protected MCP tools | Child agent identity; optionally on behalf of a user |
| Azure trace ingestion | Your Azure CLI user with Monitoring Metrics Publisher |
| Agent 365 trace export | Child agent identity authenticated through its blueprint |

No notebook deploys a hosted agent or automatically provisions directory/Azure
resources. Administrative snippets are explicit one-time setup, not Python cells.
Real service calls are not simulated. Run only with approved access; model calls
and Azure telemetry can incur charges. Never show `.env`, bearer tokens, or
credentials. Clear identity outputs before sharing.

## 1. Direct agent access versus OBO: start with a school assistant

Imagine an assistant entering a school's records office:

- **Direct agent access (app-only):** "I am the school's assistant. I have my own
  permission to do this job." No user needs to sign in at runtime.
- **On Behalf Of (OBO):** "Alex signed in and asked me to help. I am doing this
  task for Alex." The request identifies both the agent and the user.

Neither mode is automatically more powerful. The granted permissions and the
receiving API's checks determine what is allowed. An administrator may need to
sign in once to configure either mode; that setup sign-in is not the agent's
runtime identity.

| Question | Direct / app-only | OBO / delegated |
|---|---|---|
| Who is the work for? | The agent's own authorized job | A particular signed-in user |
| Is a user token needed at runtime? | No | Yes |
| MCP permission in this workshop | `Workshop.Read.Application` | `Workshop.Read` |
| Permission kind | Application role | Delegated scope |
| Field in the final MCP token | `roles` array | `scp` space-separated string |
| Available identity tools | `who_am_i` | `who_am_i` and `user_context` |
| Setup | Notebook 01 | Notebook 03; its comparison also reuses notebook 01 |

### A role definition is not a role assignment

Creating a badge type does not issue a badge to someone.

1. **Define:** on the MCP API, create an enabled application role with value
   `Workshop.Read.Application` and allowed member type **Applications**.
2. **Assign:** grant that role to the existing **child agent service principal**.
3. **Request:** obtain a fresh token for the MCP API.
4. **Enforce:** the MCP server checks the token and permission.

Creating the role alone is why you can still get a missing-role error.
The notebook's PowerShell assignment is a persistent directory change, not an
Azure deployment. Assigning the role only to the blueprint or your ordinary
notebook sign-in app is not the per-child grant this workshop uses.

The scope `Workshop.Read` is a separate delegated permission exposed by the same
API. It does not grant app-only access. Assigning `Workshop.Read.Application`
does not finish OBO setup either. Keep these distinct values: trying to reuse the
scope's value for the application role caused the duplicate-value setup error.

## 2. What actually happens when the agent signs in?

A token is a short-lived pass signed by Entra. Its **audience** says which API
may accept it; its claims describe the caller and permissions. An ID is just a
label, not a password or a permission.

### Direct access: the agent's own authorization

The child does not hold its own password. In this local workshop, the blueprint
holds the development credential and helps the child authenticate:

```text
Blueprint credential + child client ID (fmi_path)
    -> Entra issues an intermediate exchange token
    -> child uses that token as proof of its identity
    -> Entra issues a child token addressed to the MCP API
    -> agent sends that MCP token to the local server
    -> server validates it and requires Workshop.Read.Application
```

The intermediate token targets `api://AzureADTokenExchange`; it is not a pass
for the MCP API. The final MCP token has a permission claim shaped like:

```json
{"roles": ["Workshop.Read.Application"]}
```

The blueprint credential goes to Entra, not to the MCP server or the model.
The grant belongs to the child. This workshop does not rely on inherited
blueprint permissions.

### OBO: the agent's delegated authorization for a user

```text
User signs in through the browser client
    -> user receives a token addressed to the blueprint (access_as_user)
Blueprint credential + child client ID
    -> Entra issues intermediate proof for the child
Child proof + user token
    -> Entra issues a NEW token addressed to the MCP API
    -> server validates the agent, user context, and Workshop.Read scope
```

The first user token is exchanged, not simply forwarded to MCP. The final MCP
token has a delegated permission claim shaped like:

```json
{"scp": "Workshop.Read"}
```

OBO has **two permission links**. Both need setup:

1. **Browser -> blueprint:** `access_as_user` lets the browser get the initial
   user token for that blueprint.
2. **Child acting for the chosen user -> MCP:** a delegated `Workshop.Read` grant
   permits the downstream call. Notebook 03 uses a per-user grant, not all users.

The browser needs no direct MCP or Graph permission for this flow. A user token
addressed to Graph is not a substitute for the blueprint user token.

Signing in does not give the agent every permission the user has. For business
records, the API must check both the delegated scope and the user's access to the
requested records. A scope alone is not a complete data-access policy. Our tools
only return verified identity metadata; they do not access school records, mail,
or other users' documents.

## 3. Why does the request say scope = /.default in both modes?

The code requests `api://<MCP-API-CLIENT-ID>/.default`. Read this as:

> Issue a token for this API using the applicable permissions already configured
> and consented for this flow.

**It does not mean "give me everything" or grant new permissions.** The token
request's parameter is named `scope` even in app-only mode. That parameter name
does not turn an application role into a delegated permission:

- App-only exchange uses `grant_type=client_credentials` and produces application
  permissions in `roles`.
- OBO uses `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`,
  `requested_token_use=on_behalf_of`, and a user assertion; delegated permissions
  appear in `scp`.

`api://<MCP-API-CLIENT-ID>/Workshop.Read` is the full name of the delegated scope.
This workshop requests `/.default` at runtime and checks the resulting
`Workshop.Read` value in the token. Exposing that scope does not deploy an API.

## 4. Which ID belongs to which thing?

| Item | Meaning / where to look | Used here |
|---|---|---|
| Tenant ID | The organization's directory; Entra overview | `A365_TENANT_ID` |
| Blueprint client ID | Type of agent; Entra > Agents > Agent blueprints | `A365_OBSERVABILITY_BLUEPRINT_ID` |
| Child client/app ID | This agent instance; Entra > Agents > Agent identities | `A365_OBSERVABILITY_AGENT_ID`; token requests |
| Child Object ID | Directory record for that instance | Permission assignments; do not assume it equals the client ID |
| MCP API client ID | Your custom API; App registrations > MCP API > Overview | `MCP_WORKSHOP_API_ID` |
| API service-principal Object ID | That API's local directory representation; Enterprise applications | `resourceId` in grants |
| Browser client ID | Ordinary app that opens OBO user sign-in | `MCP_WORKSHOP_BROWSER_CLIENT_ID` |
| App role / scope ID | Identifies one permission definition | Role ID for assignment; scope ID for browser preauthorization |
| Application ID URI | Name for an API, commonly `api://<client-id>` | Prefix for token scope requests; not a deployed endpoint |

`contoso-agent365-notebook`, if already configured for the LangGraph/Graph
exercise, is an ordinary sign-in/setup app. It is not automatically the MCP API,
blueprint, or child. Reuse existing resources when their configuration matches;
do not create replacements just because a notebook starts from the beginning.

Microsoft's Agent 365 Observability API is a **different resource**, identified by
`9b975845-388f-4429-889e-eab1ef63949c`. Its token request is
`api://9b975845-388f-4429-889e-eab1ef63949c/.default`. You do not invent that GUID or
replace it with your MCP API ID. The same child can request different tokens for
different APIs; those tokens are not interchangeable.

## 5. Who enforces "read, not write"?

**The server code does, not the word Read.** Entra issues signed permission
claims; the receiving API must validate and enforce them.

| Code | Responsibility |
|---|---|
| [agent365_auth.py](./agent365_auth.py) | Blueprint/child token exchanges; different destination tokens |
| [mcp_workshop.py](./mcp_workshop.py) | Loads settings, acquires tokens, checks routing/permissions early, starts/stops loopback MCP, runs the assistant |
| [mcp_identity_server.py](./mcp_identity_server.py) | Verifies JWT signature, issuer, audience, tenant, child caller, version and expiry; authorizes tool discovery and execution |
| [workshop_helpers.py](./workshop_helpers.py) | Shared settings, Azure model connection, and local activity display |
| [workshop_tracing.py](./workshop_tracing.py) | Azure export, optional Agent 365 export, trace flush and Azure query |

The server requires `Workshop.Read.Application` for app-only access and
`Workshop.Read` for delegated access. It hides `user_context` in app-only discovery
**and rejects a direct call to it**. Hiding a tool alone is not authorization.
The model only sees authorized tools; it does not decide access.

`workshop:authorized` is an internal server gate, not another permission to create
in Entra. The external role/scope is still checked per tool. The tools contain no
business write operation. A future write tool must require its own appropriate
permission; reusing a read gate would not make a write operation safe.

`Agent(id=...)` and tracing baggage label a run. Neither authenticates the agent,
grants access, or creates a directory identity.

## 6. Setup, troubleshooting, and evidence

Use [LIVE_SETUP.md](./LIVE_SETUP.md) for prerequisites, sign-in troubleshooting,
and the setup-to-notebook map. In particular:

- Missing `Workshop.Read.Application`: define **and assign** the app role to the
  child, then request a fresh token after propagation.
- OBO failure: check both permission links and the selected demo user.
- Wrong token version: on the **MCP API app**, set
  `api.requestedAccessTokenVersion=2`. The v2 token endpoint alone is insufficient.
- Hidden WAM sign-in window: cancel with Ctrl+C; use the documented device-code
  command in a fresh PowerShell window. Never share the displayed sign-in code.
- `DeviceCodeCredential` exception: authentication has not succeeded. Changing API
  roles or IDs is not a fix for a failed Graph sign-in.

Entra sign-in logs show authentication, not model/tool traces. Notebook 02 shows
execution traces in Azure. Notebook 04 adds Agent 365 export, which needs separate
tenant authorization/licensing and tenant-side verification. A local activity
table or a flushed queue proves neither destination ingested the trace.

## 7. Optional LangGraph exercise and retained files

[Agent_ID_LangGraph_MultiAgent.ipynb](./Agent_ID_LangGraph_MultiAgent.ipynb) is
retained as a separate multi-agent exercise, along with its required
[lab_helpers.py](./lab_helpers.py). Unlike the five guided notebooks, it includes
explicit simulated identity/service paths and a model stand-in when unconfigured.
Read its mode switches and write/cleanup warnings before running it. Do not present
simulated responses as live tenant evidence. Its live model path can send directory
results to the configured model; use only an approved test tenant and approved data.

The combined SDK showcase and helper functions used only by that showcase have
been removed. Shared live helpers, authentication tests, and the LangGraph
dependency are retained. The [PowerPoint](./Agent365_Workshop.pptx) and matching
[PDF](./Agent365_Workshop.pdf) were refreshed on 14 October 2026 to follow the five
guided notebooks, with current Microsoft references and demo caveats in the
PowerPoint speaker notes. Slides are supplementary; this guide and the numbered
notebooks remain the current runbook. The exported PDF retains Microsoft
Information Protection; open it in an authorized, compatible PDF viewer.
