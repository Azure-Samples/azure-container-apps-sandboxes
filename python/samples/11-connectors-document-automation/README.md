# ACA Sandbox: event-driven SharePoint document automation with OCR + LLM extraction

> **An ACA Sandbox is the direct HTTPS webhook target for a Connector
> Namespaces SharePoint trigger. Inside the sandbox, GitHub Copilot CLI uses the
> SharePoint MCP to fetch the new file, `pdftotext` / `tesseract`
> extract the invoice data, and the result is written back to
> SharePoint via the same MCP.**

## Deploy and test

**Prereqs:** `azd`, `az` CLI, Python 3.10+, a working SharePoint
site you can authorize against, and a GitHub PAT with access to
[GitHub Models](https://github.com/marketplace/models) (Copilot CLI
uses Models as its LLM backend).

```bash
azd auth login
az login

# Required: where to receive triggers + where to deliver results
azd env set GITHUB_PAT              <ghp_…>
azd env set SHAREPOINT_SITE_URL     'https://contoso.sharepoint.com/teams/Finance'
azd env set SHAREPOINT_LIBRARY_ID   '<library-list-GUID>'   # the SP list ID from the URL

# Optional folder scoping (default: process every new file in the library,
# write to /Extracted in the library root)
azd env set SHAREPOINT_INPUT_FOLDER  'testinvoices/inbound'
azd env set SHAREPOINT_OUTPUT_FOLDER 'testinvoices/extracted'

azd up
```

The post-deploy hook provisions the namespace-side glue (port
registration on the sandbox proxy, trigger config wired to the
sandbox's adcproxy URL), bootstraps the sandbox (`poppler-utils`,
`tesseract`, Copilot CLI, the FastAPI listener on `:8080`), applies
the deny-default egress policy with `X-API-Key` and
`Authorization` Transform rules, and opens **two** browser tabs for
OAuth consent on the SharePoint connections (`sharepointonline`
for the trigger, `workiqsharepoint` for the MCP).

**Confirm end-to-end** — drop one of the [sample invoices](samples/invoices)
into your input folder:

| File | What it tests |
|---|---|
| `invoice-text.pdf` | the easy case (`pdftotext` extracts directly) |
| `invoice-scanned.pdf` | same invoice as an image-only PDF — forces the agent through the `tesseract` OCR fallback |

Within ~10 seconds the namespace poll fires, the sandbox wakes via
the `OnDemand` activation, Copilot CLI walks the SharePoint MCP
(`getSiteByPath` → `listDocumentLibrariesInSite` → `getFolderChildren`
→ `readSmallBinaryFile` → `createSmallTextFile`), and a
`<filename>.json` lands in `/<output folder>/`.

Both PDFs extract to the same JSON:

```json
{
  "vendor": "Contoso Office Supplies, Inc.",
  "invoice_number": "INV-2026-00427",
  "invoice_date": "2026-05-29",
  "due_date": "2026-06-28",
  "currency": "USD",
  "line_items": [...],
  "subtotal": 3774.94,
  "tax": 328.42,
  "total": 4103.36
}
```

## Clean up

⚠️ **While this sample is deployed**, every new file in the
configured SharePoint library wakes the sandbox and Copilot CLI
runs against it — that consumes GitHub Models tokens, SharePoint
MCP calls, and a few seconds of sandbox compute per event. Fine
for a hands-on demo; you almost certainly don't want it running
indefinitely against your real library. Tear the sample down when
you're done:

```bash
azd down --purge --force
```

---

## How it works (end-to-end)

```
        ┌─────────────────────────────┐
        │  New file lands in          │
        │  /<input folder>/           │
        └──────────────┬──────────────┘
                       │   polled every 10s
                       ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  Connector Namespace  (westcentralus)                           │
 │  ├─ sharepointonline connection  (OAuth → your SP site)         │
 │  ├─ workiqsharepoint connection  (OAuth → your SP site, MCP)    │
 │  ├─ mcpserverConfig (kind=ManagedMcpServer, workiqsharepoint)   │
 │  └─ triggerConfig (GetOnNewFileItems)                           │
 │       authentication: ManagedServiceIdentity                    │
 │         identity  = namespace                                   │
 │         audience  = https://auth.adcproxy.io/                   │
 │       callbackUrl = https://<sbxId>--8080.<region>.adcproxy.io  │
 │       body        = @triggerBody()                              │
 └────────────────────────────┬────────────────────────────────────┘
                              │
                              │  POST callbackUrl
                              │  Authorization: Bearer <MI token>
                              │    aud = https://auth.adcproxy.io/
                              │    oid = namespace MI principalId
                              ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  adcproxy.io  (per-sandbox HTTPS, Entra-restricted)             │
 │    auth.entraId.objectIds = [ namespace MI principalId ]        │
 │    activationMode         = OnDemand                            │
 │    → wake sandbox if cold, forward POST to :8080                │
 └────────────────────────────┬────────────────────────────────────┘
                              ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  Sandbox  (ACA Sandbox, Ubuntu base)                            │
 │                                                                 │
 │   ┌─────────────────────────────────────────────────────────┐   │
 │   │ listener.py   (FastAPI :8080)                           │   │
 │   │   self-loop guard:                                      │   │
 │   │     skip if inside output folder OR name ends .json     │   │
 │   │   input-folder filter:                                  │   │
 │   │     skip if not under SHAREPOINT_INPUT_FOLDER           │   │
 │   │   else: spawn copilot --allow-all-tools -p prompt.md    │   │
 │   └────────────────────────┬────────────────────────────────┘   │
 │                            ▼                                    │
 │   ┌─────────────────────────────────────────────────────────┐   │
 │   │ GitHub Copilot CLI v1.x   (LLM = GitHub Models)         │   │
 │   │   SharePoint MCP tools, in order per request:           │   │
 │   │     getSiteByPath          → siteId                     │   │
 │   │     listDocumentLibrariesInSite → documentLibraryId     │   │
 │   │     getFolderChildren      → fileId (DriveItem)         │   │
 │   │     readSmallBinaryFile    → base64 bytes               │   │
 │   │   shell: pdftotext / pdftoppm + tesseract OCR fallback  │   │
 │   │   agent: produce normalized invoice JSON                │   │
 │   │     createFolder (if /<output folder>/ missing)         │   │
 │   │     createSmallTextFile → upload <name>.json            │   │
 │   └────────────────────────┬────────────────────────────────┘   │
 │                            ▼                                    │
 │   ┌─────────────────────────────────────────────────────────┐   │
 │   │ egress proxy  (Deny default + Transform rules)          │   │
 │   │   X-API-Key:     <namespace MCP key>  on MCP host       │   │
 │   │   Authorization: token <PAT>        on GitHub hosts     │   │
 │   │   → sandbox holds NO MCP key; key stamped at boundary   │   │
 │   └────────────────────────┬────────────────────────────────┘   │
 └────────────────────────────┼────────────────────────────────────┘
                              ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  Connector Namespace MCP runtime → workiqsharepoint backend     │
 │  → Microsoft Graph (using the SharePoint connection's OAuth)    │
 │  → write <filename>.json into /<output folder>/                 │
 └─────────────────────────────────────────────────────────────────┘
```

---

## Security model

Two SharePoint OAuth connections (one per namespace capability), one
sandbox-side managed identity, and **no SharePoint credentials
inside the sandbox**.

| Component | Purpose |
|---|---|
| **Connector Namespace MI** | Mints the bearer token attached to every trigger POST to the sandbox proxy. Granted `Container Apps SandboxGroup Data Owner` on the sandbox group so the proxy can wake the sandbox on demand. |
| **Sandbox group MI** | Sandbox-side identity (used by the proxy + by `set_egress_policy` when post-deploy applies the Deny default). |
| **sharepointonline connection** | OAuth-authorized to your SharePoint site. Used **only** by the trigger config — receives change notifications from SharePoint. |
| **workiqsharepoint connection** | OAuth-authorized to your SharePoint site. Used **only** by the MCP server — the sandbox calls JSON-RPC tools (`getSiteByPath`, `readSmallBinaryFile`, `createSmallTextFile`) through this. |
| **mcpserverConfig (`kind: ManagedMcpServer`)** | The namespace-published MCP HTTP endpoint the sandbox connects to. Authenticates with `X-API-Key` (stamped by the egress proxy). |
| **Egress proxy** | Deny default + Transform rules. Holds the MCP `X-API-Key` and the GitHub `Authorization` token. Stamps headers on outbound requests — the sandbox process never sees them. |

### What's enforced — and where

Three independent checks gate this flow; each is short-lived and
audience-scoped:

1. **Trigger → sandbox** — `adcproxy.io` validates the namespace
   MI's bearer token (signature, `iss`, `aud=https://auth.adcproxy.io/`,
   `oid ∈ port.entraId.objectIds`). Wrong `oid` ⇒ **403**, no
   token ⇒ **401**.
2. **Sandbox → MCP** — the namespace MCP HTTP endpoint requires
   `X-API-Key`. The sandbox doesn't have the key; the egress proxy
   stamps it on every outbound request to the MCP host. A request
   leaving the sandbox to any other host without a matching
   Transform rule is dropped by the Deny default.
3. **MCP → SharePoint** — the upstream `workiqsharepoint` MCP
   server uses the connection's OAuth token (acquired once at deploy
   via the official `listConsentLinks` + `confirmConsentCode` ARM
   APIs that postdeploy.py drives) to call
   Microsoft Graph. The token never leaves the namespace's runtime.

The sandbox holds **no SharePoint credential, no MCP API key**.
Compromise of the sandbox process leaks only the GitHub PAT
(needed locally by Copilot CLI to authenticate to GitHub Models
*before any network call* — see [Going further](#going-further-per-file-child-sandboxes)
for why this trade-off exists and the path off it).

### Where the namespace API key lives

| Location | Holds namespace API key? |
|---|---|
| Bicep state / azd deployment state | ❌ |
| Operator shell history | ❌ |
| Connector Namespace control plane | ✅ (issued by `listApiKey`) |
| Sandbox env / disk / memory | ❌ |
| Sandbox egress proxy | ✅ |
| Outbound MCP request on the wire | ✅ (stamped by proxy) |

---

## SharePoint MCP tool reference

The Work IQ SharePoint MCP server (`workiqsharepoint`) publishes
35 tools. The prompt in [`host/prompt.md`](host/prompt.md) walks
Copilot through the six it actually needs, so the model doesn't
waste tokens on `tools/list`:

| Tool | Purpose |
|---|---|
| `getSiteByPath(hostname, serverRelativePath)` | Resolve the configured site URL → `siteId` |
| `listDocumentLibrariesInSite(siteId)` | List drives in the site → pick the `documentLibraryId` |
| `getFolderChildren(documentLibraryId, parentFolderId="root")` | Enumerate a folder; match `FileLeafRef` to find the new file's DriveItem id |
| `readSmallBinaryFile(fileId, documentLibraryId)` | Download bytes (returns base64; agent decodes) |
| `createFolder(...)` | Create the output folder if it doesn't exist |
| `createSmallTextFile(filename, contentText, documentLibraryId, parentfolderId)` | Upload the extracted result JSON |

The trigger payload's `ID` field is the SharePoint **list item ID**,
which is **not** the **DriveItem ID** that file tools require. The
prompt walks Copilot through the site → drive → folder lookup to
translate.

---

## Re-iterating without `azd up`

For prompt or listener tweaks you don't need a full provision —
the post-deploy script also exposes a `--skip-oauth` mode that
hot-reloads the sandbox without re-popping the OAuth tabs:

```bash
python infra/scripts/postdeploy.py --skip-oauth
```

This re-uploads `host/*` into the existing sandbox, re-applies the
egress policy + transforms, restarts uvicorn, refreshes the port
registration, and re-PUTs the trigger config.

---

## Going further: per-file child sandboxes

The shipped design uses one long-lived host sandbox that serializes
requests through Copilot CLI. The sandbox is isolated from any
other tenant in the sandbox group, but multiple files from the same
SharePoint library share the same ACA Sandbox VM (in per-run
workspaces `/work/<run_id>/`).

For **true per-file isolation** — one ACA Sandbox per invoice,
destroyed after — the host listener would spawn a child sandbox per
incoming trigger. That requires an Azure credential **inside** the
host sandbox to call `Microsoft.App/sandboxGroups/.../begin_create_sandbox`.
Whether sandboxes expose IMDS / an attached MI in this preview is
not yet confirmed. When that lands, the listener becomes a tiny
dispatcher and the GitHub PAT trade-off goes away too (each child
sandbox gets its own egress policy that injects the PAT inline at
the boundary).
