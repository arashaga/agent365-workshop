# Instructor setup: the current Agent 365 workshop

Start with [WORKSHOP_START_HERE.md](./WORKSHOP_START_HERE.md). It explains direct
agent access, OBO, scopes, app roles, token exchanges, IDs, and enforcement in
plain language. This page maps the **five numbered notebooks** to their setup and
troubleshooting. The removed combined showcase is no longer part of the runbook.

## 1. What to prepare, and who prepares it

One-time setup is persistent configuration, not a token that lasts forever.
Reuse existing resources when their settings match. These notebooks do not deploy
a hosted agent or automatically create directory/Azure resources.

| Notebook | One-time setup | Each session / evidence |
|---|---|---|
| [00 Identity](./00_Identity_Setup.ipynb) | Authorized Entra administrator creates/reuses blueprint, blueprint principal and child; instructor stores approved development credential | Fresh project kernel; match tenant, blueprint and child IDs to Entra |
| [01 Permissions](./01_Agent_Permissions.ipynb) | Administrator configures custom MCP API and assigns `Workshop.Read.Application` to the child | Obtain fresh MCP token; anonymous call gets 401; child can call `who_am_i`, but not `user_context` |
| [02 Azure traces](./02_Agent_and_Azure_Traces.ipynb) | Existing Azure model and Application Insights; CLI user has inference, ingestion and query access | Real model/tool execution; find the printed trace ID in Azure Logs |
| [03 OBO](./03_User_Delegation.ipynb) | Browser client; blueprint `access_as_user`; browser-to-blueprint consent; child-to-MCP delegated `Workshop.Read` for demo user | User signs in; same child can now call `user_context` |
| [04 Agent 365](./04_Agent365_Observability.ipynb) | Assigned qualifying license, registered-agent route or explicit child telemetry-write grant, presenter access to tenant viewer | Export to Azure and Agent 365; verify ingestion independently in each destination |

Notebooks 00-01 need neither a model nor Application Insights nor Agent 365
observability licensing. Notebook 03's permission checks do not need them either;
its optional model explanation does. Notebook 04 does not require OBO.

Azure subscription Owner/Contributor access is not Entra consent authority and
does not automatically grant model inference or telemetry data-plane access.
Use an authorized tenant administrator for directory grants.

## 2. Local environment and private settings

1. Select the project's Python 3.12+ environment in VS Code. Start notebooks from
   the project root or `Agent_365`.
2. If dependencies are missing, install
   [requirements-showcase.txt](./requirements-showcase.txt) in that environment.
   The filename is retained so existing setup commands continue to work.
3. Use [the environment template](./.env.example) to fill missing settings in the
   existing private `Agent_365/.env`. Do not overwrite existing settings or paste
   credentials into notebook cells.
4. Restart the kernel after changing settings. Use a fresh kernel per notebook.
   Configure tracing only once per kernel.

| Setting | Required for |
|---|---|
| `A365_TENANT_ID` | All guided notebooks |
| `A365_OBSERVABILITY_BLUEPRINT_ID` | Existing blueprint **client ID** |
| `A365_OBSERVABILITY_AGENT_ID` | Existing child **client ID**, despite the historical observability prefix |
| `A365_OBSERVABILITY_BLUEPRINT_SECRET` | Approved local credential held by that blueprint |
| `MCP_WORKSHOP_API_ID` | Notebooks 01-04; custom API **client ID** |
| `MCP_WORKSHOP_BROWSER_CLIENT_ID` | Notebook 03 only; ordinary public browser client |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` | Notebook 02/04 and optional model explanation in 03 |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Notebook 02/04; existing Application Insights destination |

The five guided notebooks ignore the LangGraph-only `A365_LIVE`,
`A365_ALLOW_WRITES`, `A365_CLIENT_ID`, `A365_CLIENT_SECRET`, and `A365_DEMO_*`
settings. Do not delete them if you still run the LangGraph exercise.

Keep credentials private. IDs are not passwords, but saved identity outputs still
need clearing before distribution. Never print or share complete bearer tokens,
secrets, or temporary device sign-in codes.

## 3. Identity: reuse the existing blueprint and child

Follow [notebook 00](./00_Identity_Setup.ipynb). Locate blueprints and children under
**Entra ID > Agents**, not just ordinary App registrations. The portal blueprint
wizard creates its blueprint principal; Graph-based provisioning must explicitly
ensure that principal exists.

The blueprint holds the development credential. The child receives the permissions
and is the caller to MCP. Client IDs are used to request tokens; Object IDs identify
directory records for grants. Do not copy a client ID into an Object ID placeholder
on the assumption they are equal.

An Entra identity is not an Agent 365 registration or a running program.
`Agent(id=...)` labels a local runtime agent; it provisions nothing.

## 4. Direct agent access: define, assign, request, enforce

Use steps 1-2 of [notebook 01](./01_Agent_Permissions.ipynb):

1. Reuse/create the single-tenant custom MCP API. It needs no secret or redirect.
2. Set its Application ID URI to `api://<MCP-API-CLIENT-ID>`.
3. Keep its enabled delegated scope `Workshop.Read` for OBO. Consent for the
   browser/child links is configured separately in notebook 03.
4. Define an enabled app role `Workshop.Read.Application`, allowed member type
   **Applications**, for direct access. Use a distinct value from the scope.
5. On the **MCP API app**, set Manifest > `api.requestedAccessTokenVersion` to `2`,
   preserving other settings. This controls the receiving API's token version.
6. Run notebook 01's idempotent PowerShell script to assign the application role
   to the child. The updated script resolves the child's Object ID from its client
   ID and reads back the assignment. It is not a grant to the blueprint.
7. Allow propagation, then obtain a fresh token. Restart the kernel if an earlier
   run cached a token.

Run administrative snippets in **PowerShell**, not Python notebook cells. Use
PowerShell 7 with Microsoft.Graph.Applications; notebook 03 also needs
Microsoft.Graph.Identity.SignIns for delegated grant commands. Install missing
modules only when needed:

```powershell
Install-Module Microsoft.Graph.Applications -Scope CurrentUser
Install-Module Microsoft.Graph.Identity.SignIns -Scope CurrentUser
```

Creating a role merely offers a permission. Assigning it issues the child that
permission. Requesting a token afterward makes it available in `roles`.
The server still has to validate and enforce it.

### Graph PowerShell sign-in problems

The administrative scripts use device-code authentication so you can open the
browser yourself instead of hunting for a hidden Windows WAM window.

If an earlier interactive command is waiting invisibly, cancel it with Ctrl+C.
Open a **fresh standalone PowerShell window**, then use the notebook's updated
sign-in command, replacing the tenant placeholder:

```powershell
$ErrorActionPreference = 'Stop'
Connect-MgGraph -TenantId '<TENANT-ID>' `
    -Scopes 'Application.Read.All','AppRoleAssignment.ReadWrite.All' `
    -UseDeviceAuthentication -ContextScope Process -ErrorAction Stop
```

Follow the address/code printed by that command. Complete sign-in using the
authorized administrator account before continuing. `-ContextScope Process`
limits the authentication context to this process; it is not a guaranteed fix
for every SDK authentication error.

If `DeviceCodeCredential authentication failed: Object reference not set...`
still occurs, **stop before the grant commands**. It is an authentication/SDK
failure, not evidence that your API/client IDs or role definition are wrong.
Inspect the installed Graph Authentication/Applications module versions and the
full error in that fresh session; if needed, have the administrator check Entra
sign-in logs and device-code policy. Do not disable tenant security policy or
recreate registrations to bypass a sign-in problem.

Cloud Shell can be a browser-hosted execution location, but still needs the
modules, the correct tenant, and an authorized account. It does not grant extra
privileges. The MCP API's Users and groups page is not a substitute for this
application-to-application assignment.

[Microsoft Graph PowerShell authentication](https://learn.microsoft.com/powershell/microsoftgraph/authentication-commands)

## 5. OBO: set up both links, not just the scope

Use steps 1-3 of [notebook 03](./03_User_Delegation.ipynb):

1. Reuse/create an ordinary public browser client, separate from the blueprint and
   child. Register Mobile and desktop redirect **`http://localhost:8400`**.
   Store its client ID in `MCP_WORKSHOP_BROWSER_CLIENT_ID`. No secret is needed.
2. On the blueprint API, include `api://<BLUEPRINT-CLIENT-ID>` in `identifierUris`,
   request v2 tokens, expose enabled `access_as_user`, and preauthorize the browser
   with that scope's unique ID. Preserve existing manifest entries.
3. In the browser app, request the blueprint's delegated `access_as_user` permission
   and obtain the required administrator consent.
4. Separately grant the **child** delegated `Workshop.Read` on the MCP API for the
   demo user's Object ID. Notebook 03's script preserves existing scopes and stops
   if multiple matching per-user grants need review.
5. Allow propagation, restart the kernel, and compare app-only and OBO calls.
   The latter should return the verified user Object ID and the same child client
   ID. No token should be printed.

The initial user token must target the blueprint, not Graph or MCP. The child
combines user proof with its own authentication to obtain a **new** downstream
token. The app-only role does not replace this delegated grant. The browser
itself needs no direct MCP/Graph access.

Notebook 03's explicit grant is per user; do not replace it with an all-users grant
just to make a demo work. Record and revoke temporary grants after the workshop;
do not rely only on an expiry field for cleanup.

## 6. Model and Azure traces

Follow [notebook 02](./02_Agent_and_Azure_Traces.ipynb):

- Reuse an existing model deployment. The code uses **Azure CLI user credentials**
  for inference, not the child MCP token. Ensure CLI sign-in is current in the
  correct tenant and the user has inference access, such as Cognitive Services
  OpenAI User on the model resource.
- Configure the existing Application Insights connection string. The CLI user
  needs **Monitoring Metrics Publisher** for ingestion and appropriate Logs query
  access. These Azure permissions are separate from MCP app roles.
- Run the real Agent Framework assistant with protected MCP tools. Identity tool
  results are sent to the configured Azure model; use approved test identities.
  Bearer tokens and the blueprint credential are not sent to the model.
- Run the printed KQL in **Application Insights > Logs**, in KQL mode. Match the
  exact trace ID, timestamps, and invocation/model/tool records.

[workshop_tracing.py](./workshop_tracing.py) configures native Agent Framework
instrumentation and the explicit Azure exporter. It rejects an already configured
provider/inherited OTLP destinations rather than silently retaining a different
setup. Sensitive prompt capture and optional Microsoft SDK-health uploads are
disabled. The local activity table is a copy for teaching, not cloud evidence.

## 7. Optional Agent 365 destination

Follow [notebook 04](./04_Agent365_Observability.ipynb), after notebook 02 works:

1. Confirm an assigned qualifying Microsoft 365 E7 or Microsoft Agent 365 license
   in the tenant, per the linked Microsoft prerequisites.
2. Confirm whether the existing child is Agent 365 registered. The supported
   registered-agent S2S route does not require an explicit observability role.
3. For the existing Entra-only route, an authorized administrator can use notebook
   04's script to assign `Agent365.Observability.OtelWrite` to that child's Object
   ID on Microsoft's Observability service principal. The API's fixed client ID is
   `9b975845-388f-4429-889e-eab1ef63949c`. If that principal is absent, stop and have
   the administrator establish the official service; do not register an imitation.
4. A telemetry grant is **not full Agent 365 registration** and does not guarantee
   an agent management card. Full onboarding can create additional resources; do
   not run it blindly over existing manual setup.
5. Obtain a fresh token after propagation. Add the Agent 365 exporter alongside
   Azure, then verify the run separately in your available tenant activity surface.
   Confirm that surface and presenter access before promising it in a demo.

An Agent 365 token targets Microsoft's API, not your MCP API. The same blueprint/
child exchange mechanism obtains it, but the destination and permission are
different. A flushed queue proves neither HTTP acceptance nor ingestion.

[Agent 365 authentication routes](https://learn.microsoft.com/microsoft-agent-365/developer/observability-authentication-setup)
and [HTTP 403 troubleshooting](https://learn.microsoft.com/microsoft-agent-365/developer/microsoft-opentelemetry#http-403-forbidden).

## 8. Troubleshooting and cleanup

| Symptom | Check / action |
|---|---|
| Missing `Workshop.Read.Application` | Role exists AND is assigned to this child for this MCP API; get a fresh token |
| Duplicate permission value | Keep delegated `Workshop.Read`; use distinct application role `Workshop.Read.Application` |
| Token v1 / wrong `aud`, `tid`, or `azp` | Receiving MCP API requests v2; compare client IDs and tenant; do not substitute Object IDs |
| App-only cannot call `user_context` | Expected: this tool requires delegated user context |
| OBO fails | Browser redirect, blueprint audience, both permission links, chosen demo user |
| Anonymous MCP call gets 401 | Expected authentication rejection |
| `NotImplementedError` in `asyncio.create_subprocess_exec` on Windows | Older helper used async subprocess transports unsupported by Jupyter's selector loop. Reload the updated helper by restarting the kernel and run from the top. The helper now uses standard subprocess startup and threaded shutdown waits; no Entra change or notebook loop-policy override is needed |
| Valid token lacks required permission | Server denies access; renaming tools or model instructions cannot grant it |
| Model works but export fails | Inference and telemetry are different connections/permissions |
| Agent 365 reports missing OtelWrite / 403 | Confirm registration route or explicit child telemetry role, fresh token, and licensing |
| No Azure trace yet | Inspect exporter errors, ingestion/query permissions, exact trace ID and time range; do not claim ingestion |
| Tracing already configured | Restart kernel; configure once |

Stop local servers/kernels after use. Revoke temporary app-role assignments and
per-user delegated grants as appropriate; rotate/remove workshop credentials.
Do not delete shared tenant resources without their owner's approval. Clear saved
identity outputs before sharing notebooks.

The optional [LangGraph notebook](./Agent_ID_LangGraph_MultiAgent.ipynb) and its
[legacy helper](./lab_helpers.py) are retained, including their own explicit
simulation and live-write controls. They are separate from this live guided path.
