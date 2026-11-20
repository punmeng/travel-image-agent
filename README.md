# Travel image agent

A Microsoft Foundry Hosted Agent that turns travel descriptions into handwritten
storyboard illustrations using `MAI-Image-2.7-Flash`. Python, FastAPI, Entra ID and
a small vanilla HTML preview; no API keys or browser-side credentials.

```text
Travel description -> conversational prompt -> MAI Flash -> validated image
                                                       -> direct PNG response
                                                       -> optional private Blob URL
```

This public repository contains generic configuration and a fictional itinerary.
It does not include a running public API, account access, deployment state,
private itineraries, generated pictures or login caches.

## Files

| Path | Purpose |
| --- | --- |
| `travel_image.py` | Storyboard prompt, exact MAI request, image validation and optional private storage |
| `main.py` | Hosted `/invocations` handler and health endpoints |
| `azure.yaml` | Code deployment into your existing Foundry project |
| `local_preview.py`, `index.html` | Loopback-only preview and image sharing controls |
| `sample-itinerary.txt` | Fictional static input; keep its copy in `index.html` synchronized |
| `docs/integration/Foundry-agent-integration.html` | Offline-readable Chinese guide with downloadable client and requirements |
| `docs/integration/Foundry-agent-integration-complete.md` | The same integration guide in Markdown, including complete client source |
| `docs/integration/generate_travel_image.py` | Standalone external caller, with no project imports |

## Prerequisites

- Python 3.13, Azure CLI and Azure Developer CLI with the Microsoft Foundry extension.
- Your own existing Foundry project with Hosted Agents support.
- An existing conversational deployment such as `gpt-4.1-mini`, plus a deployed
  image model named **`MAI-Image-2.7-Flash`** in the account serving your MAI endpoint.
- Permission to deploy the agent and grant its runtime identity the required
  model access. Model availability, capacity and regional support vary.

Setup, hosted compute and generation can incur Azure charges. This template does
not create model deployments or storage accounts. Obtain approval before
provisioning or generating images.

## Install and configure

PowerShell examples below use placeholders; replace every `<...>` value.
Use your organization's approved Python package index when required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Optional isolated Azure CLI cache; set it before login AND invocation.
$env:AZURE_CONFIG_DIR = Join-Path $PWD ".azure-cli-local"
$env:AZURE_TENANT_ID = "<tenant-id>"
az login --tenant $env:AZURE_TENANT_ID

$env:AZURE_AI_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-4.1-mini"
$env:MAI_IMAGE_ENDPOINT = "https://<account>.services.ai.azure.com/mai/v1/images/generations"
$env:FOUNDRY_AGENT_ENDPOINT = "$($env:AZURE_AI_PROJECT_ENDPOINT)/agents/travel-image-agent/endpoint/protocols/invocations?api-version=v1"
```

`.env.example` documents the variables; the Python programs do **not** load `.env`
automatically. Set variables in the invoking process or your deployment system.
Keep real values and credentials outside Git. The model deployment is the source
constant `IMAGE_MODEL`; the caller does not select a model, size or art style.

## Deploy to your existing project

Install the current Microsoft Foundry azd extension following the
[official hosted agent deployment guide](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent).
Select an explicit subscription in the azd environment; do not rely on a shared
Azure CLI default.

```powershell
azd env new dev
azd env set AZURE_SUBSCRIPTION_ID "<subscription-id>"
azd env set AZURE_TENANT_ID $env:AZURE_TENANT_ID
azd env set AZURE_AI_PROJECT_ID "/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>"
azd env set AZURE_AI_PROJECT_ENDPOINT $env:AZURE_AI_PROJECT_ENDPOINT
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME $env:AZURE_AI_MODEL_DEPLOYMENT_NAME
azd env set MAI_IMAGE_ENDPOINT $env:MAI_IMAGE_ENDPOINT
# Direct mode does not use storage. These non-secret defaults satisfy the template.
azd env set AZURE_STORAGE_ACCOUNT_URL ""
azd env set AZURE_STORAGE_CONTAINER "travel-images"
azd env set IMAGE_URL_TTL_HOURS "24"
azd deploy travel-image-agent --no-prompt
azd ai agent show --output json
```

Use the Invocations URL returned for your deployment as `FOUNDRY_AGENT_ENDPOINT`;
confirm the ACTIVE version and actual endpoint routing after each deployment.
The source `azure.yaml` uses remote Python code build; Docker and a new ACR are
not required. Do not run `azd provision` for an already-configured project unless
you intentionally need infrastructure changes. Deployment creates immutable
agent versions; it does not invoke image generation.

### Identity and least privilege

The hosted runtime uses `DefaultAzureCredential` with the platform's managed
identity. Set `AZURE_CLIENT_ID` in the runtime only if you intentionally select a
user-assigned identity. Confirm the runtime's actual principal before granting
access; it may differ from the project's identity.

| Identity | Required permission |
| --- | --- |
| External caller | `Foundry Agent Consumer`, data action `Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action`, at an appropriate project/account scope covering the endpoint |
| Runtime: conversational model | `Cognitive Services OpenAI User` at the serving account |
| Runtime: MAI | Data action `Microsoft.CognitiveServices/accounts/MAI/images/action` at the MAI account, via an appropriate role |
| Runtime: optional Blob | `Storage Blob Data Contributor` on the private container, plus `Storage Blob Delegator` on the storage account |

An OpenAI-only grant may omit the MAI image action. Have an administrator inspect
current role definitions and assign only required permissions. Do not grant
broad Contributor access or enable account keys to work around an unknown error.

Caller scope: `https://ai.azure.com/.default`.
Runtime MAI scope: `https://cognitiveservices.azure.com/.default`.
The preview and standalone caller use Azure CLI explicitly for development;
the standalone caller also supports an explicit Managed Identity mode in Azure.

## Invoke: direct image contract

```http
POST <your-full-https-invocations-url>
Authorization: Bearer <token-from-Azure-Identity>
Content-Type: application/json
Accept: image/png

{"description":"A fictional three-day city trip: riverside, gallery, market.","response_format":"image"}
```

Success is HTTP 200 with binary image bytes, not JSON or base64. The agent
validates PNG/JPEG integrity and exactly 1024 x 1024 dimensions. It returns
`Content-Type`, `x-image-sha256`, `x-agent-invocation-id` and a suggested attachment
filename. The PNG-focused preview/client reject an unexpected non-PNG response.
The 30 MiB application limit is not a documented Foundry platform quota.

```powershell
.\.venv\Scripts\python.exe docs\integration\generate_travel_image.py --input sample-itinerary.txt --output travel-storyboard.png --auth cli
```

That standalone file needs only `docs\integration\requirements.txt`; copy those
two files into another application if desired. Set `FOUNDRY_AGENT_ENDPOINT` and
`AZURE_TENANT_ID`, or pass `--endpoint` and `--tenant-id`.
In Azure use `--auth managed-identity`, optionally `--client-id <identity-client-id>`.
This mode needs a real Managed Identity execution environment and its own caller
role assignment.

Errors are explicit; the agent returns a safe JSON error for upstream workflow
failures. Do not automatically retry a generation POST after a timeout: the
original job may still be running and a retry can incur a second charge.

## Local sharing preview

With the same shell configuration and an already-deployed agent:

```powershell
.\.venv\Scripts\python.exe local_preview.py --port 8765
```

Open `http://127.0.0.1:8765/`. `share via image` invokes your deployed agent using
the fixed fictional itinerary, then displays the actual returned PNG. There is
no generation on page load. A click incurs model usage.

The bridge binds only to loopback, checks Host and same-origin requests, requires
a custom header, caps the request body and serializes generation. It keeps CLI
credentials server-side and honors your `AZURE_CONFIG_DIR`. Do not expose it
through a public tunnel or treat it as an authenticated cloud website.

- Download PNG, copy text and open Instagram with `share to Instagram`.
- The Threads web intent opens a **text** draft; manually attach the PNG.
- Native file share is shown only when supported and requires a separate click
  after generation. Available apps depend on the browser/device.
- Clipboard, capability-check, cancellation and sharing errors have visible
  download/manual-copy fallbacks.

No automatic social publishing or image attachment is implemented. An opened
share sheet is not a confirmed post. Official references:
[Threads intents](https://developers.facebook.com/documentation/threads/threads-web-intents.md/),
[Instagram Stories](https://developers.facebook.com/documentation/instagram-platform/sharing-to-stories.md/),
[Web Share API](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/share).

## Optional private Blob URLs

`response_format` defaults to `"url"` for compatibility. Always send `"image"`
when you want no Blob access. Direct mode bypasses storage validation, preflight,
delegation, upload and SAS entirely.

URL mode requires `AZURE_STORAGE_ACCOUNT_URL`, a **private** pre-existing
`AZURE_STORAGE_CONTAINER`, the storage permissions above and network
connectivity from the agent. Set `IMAGE_URL_TTL_HOURS` to 1-168 (default 24).
The agent preflights storage before billed generation, uploads with no overwrite
and returns a read-only HTTPS user-delegation SAS with an expiry.

Private endpoint/firewall policies may require approved network integration.
Do not disable organizational policy or make a container public to bypass a
403. Treat returned SAS URLs as temporary credentials; keep them out of Git/logs.
`verify_live.py` is an optional manual acceptance tool for either response mode;
running it invokes generation and incurs usage.

## Validation and publication hygiene

```powershell
.\.venv\Scripts\python.exe -m unittest -q
```

Tests cover the MAI contract, direct bytes/storage bypass, private SAS behavior,
image corruption, safe errors, loopback guards, public configuration and
documentation/client synchronization. They do not generate paid images.

Before sanitizing this public source, the original deployment and standalone
caller completed real Foundry -> MAI -> PNG rounds, with visual inspection.
Those private endpoints, evidence identifiers and artifacts are intentionally
omitted. This public configurable copy has not been newly deployed or billed for
another image; verify your own configured environment before production use.
Generated handwritten lettering can contain errors: review captions before
sharing. Model inference sends the supplied itinerary to your Azure deployments.

The publication history is intentionally clean and independent of local
development/checkpoint history. Ignore rules exclude credentials, deployment
state, logs and images; review staged changes before every public push.
