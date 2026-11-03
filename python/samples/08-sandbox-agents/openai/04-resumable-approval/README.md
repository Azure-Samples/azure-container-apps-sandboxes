# Resumable agent with human approval

Run an agent, close the application while a human reviews its draft, then
continue in a **new process using the same sandbox**. This demonstrates a
durable approval boundary rather than a prompt asking the model to wait.

The sample uses a small Azure OpenAI Chat Completions code-generation loop,
not the OpenAI Agents SDK provider used by the other demos. No new Python
dependencies beyond the repository's `openai` extra are needed.

## Steps performed

- **Create a sandbox.** Start one ACA sandbox using the `python-3.14` image
  in an existing sandbox group.
- **Upload sample data.** Upload a small sales CSV into the sandbox.
- **Generate and run Python code.** The host calls Azure OpenAI to generate
  Python. That code runs inside the sandbox, calculates revenue, and writes
  a draft report.
- **Pause for human review.** Download the draft and save the sandbox ID,
  conversation, workflow stage, and draft hash locally. Stop the sandbox
  and exit the application. No application process stays running while
  waiting for approval.
- **Approve the draft.** Review the downloaded report, then run
  `continue --approve`. Continuing without approval is rejected by the host
  application before it resumes the sandbox or calls the model.
- **Resume the same sandbox.** A new application process restores the saved
  conversation and reconnects using the same sandbox ID. It resumes the
  sandbox and checks that its draft matches the reviewed copy.
- **Finalize the report.** Newly generated Python runs in that same sandbox
  and uses the existing draft to produce a report marked "Approved by human."
  Download the final report, then stop the sandbox again.
- **Clean up.** Run `cleanup` to delete the demo sandbox. Keep the local
  reports and checkpoint.

The host application enforces approval. The stopped sandbox preserves the
files; it does not run an approval process. This lets the workflow continue
later in a new process without creating a replacement sandbox.

## What survives

| Location | Persisted state |
|---|---|
| ACA sandbox | Synthetic sales CSV, generated Python, draft and final reports |
| Local `out/session/checkpoint.json` | Sandbox ID, Azure resource context, model configuration, conversation, phase, reviewed draft hash |
| Local `out/session/` | Downloaded draft and final reports |

Stopping a sandbox does **not** save your host application's conversation.
The host checkpoint and the sandbox's filesystem are separate pieces of state.
This sample stops/resumes the existing sandbox; it does not create a snapshot
or restore a clone.

## Prerequisites

- Python 3.13 or later and the repository's dependency setup.
- Azure CLI login (`az login`) and access to an **existing sandbox group**.
  Use the shared [setup](../../../setup/) if you need to create that baseline.
- The repository-pinned `azure-containerapps-sandbox` SDK includes the
  lifecycle pollers used by this sample.
- An Azure OpenAI chat deployment supporting JSON-object responses and
  `max_completion_tokens`. Set the deployment name, not just a model name.
- Access to the sandbox data plane; for Entra model authentication, the
  `Cognitive Services OpenAI User` role on the model resource.

Use the shared `python/samples/.env` file or environment variables:

```dotenv
ACA_SUBSCRIPTION_ID=<subscription-id>
ACA_RESOURCE_GROUP=<existing-resource-group>
ACA_SANDBOXGROUP_NAME=<existing-sandbox-group>
ACA_SANDBOXGROUP_REGION=<group-region>
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=<chat-deployment>
AZURE_OPENAI_API_VERSION=2025-04-01-preview
# Optional: omit to use DefaultAzureCredential.
AZURE_OPENAI_API_KEY=<key>
```

Keep the optional key line out of the file when using Entra authentication.
Credentials stay in the host process and are never saved in the checkpoint
or uploaded to the sandbox.

## Run locally

From this directory, using the repository's `uv` workflow:

```powershell
uv run --extra openai run.py start
```

This creates one `python-3.14` sandbox, uploads a tiny synthetic CSV, asks the
model to generate a draft analysis, downloads `out/session/draft.md`, waits
for sandbox suspension, writes the checkpoint, and **exits**.

Open the downloaded draft in an editor. The expected total revenue is **130**:
Notebook 60, Pen 40, Folder 30. The harness does not certify the model's
calculations; verifying the draft is the human's job.

In another terminal/process, from the same directory:

```powershell
# Demonstrates rejection without resuming or calling Azure/model APIs.
uv run --extra openai run.py continue

# After reviewing the draft:
uv run --extra openai run.py continue --approve
```

The approved command restores the saved conversation and Azure context,
resumes the saved sandbox ID, verifies that its draft matches the reviewed
local copy, and asks the model to finalize the existing report. It downloads
`out/session/final.md` and suspends the sandbox again. Compare the sandbox ID
printed by both commands. Check the final report for the same figures.

If you prefer `pip`, install the repository's existing dependencies and
`openai` extra, and replace the
`uv run ... run.py` prefix above with `python run.py`. No `uv` runtime API is used.

## Cleanup and costs

```powershell
uv run --extra openai run.py cleanup
```

Cleanup uses **only the saved resource context and sandbox ID** and waits for
deletion. It does not need model configuration or delete your group, deployment,
or local reports. Repeat cleanup safely if the sandbox was already deleted.
Keep the checkpoint until cleanup succeeds.

Suspension is not deletion and should not be treated as free storage.
Sandbox execution/storage and Azure OpenAI calls can incur charges. The idle
suspension interval is 300 seconds as a fallback, not an automatic deletion
policy. The application does not create a resource group, sandbox group, or
model deployment. Local launch is still a **live Azure/model run**.

## Decisions and boundaries

- One sandbox per session, reused across two application runs.
- Public Python image so the OS and Python are already supplied; no OS setup
  or dependency downloads inside the sandbox. Generated code uses the stdlib.
- Deny-default egress with Full inspection; no ports, mounted credentials,
  or managed identity assigned by this sample. Model calls originate on the host.
- Explicit `--approve` is enforced by host code before continuation, not by
  the model. Approval gates the second phase, **not each line of generated code**.
  The first phase already executes untrusted code in the sandbox. Instructions
  such as "work only in this directory" are not filesystem security controls.
- Four model/code attempts per phase; 30-second execution deadline using
  the Linux image's `timeout` utility; 60-second model request timeout with
  automatic model retries disabled; execution feedback is truncated.
- Reports must be nonempty UTF-8 and at most 64,000 bytes after download.
  This is not a streaming download limit or protection against excessive output.
- Configuration and conversation are local, readable JSON, not a credential
  store. Protect them as potentially sensitive data. Use only synthetic inputs:
  output and report text are sent to the configured model.
- Run one process per session directory. This is a learning example, not a
  multi-user approval service or a crash-safe exactly-once workflow engine.

## Failures and recovery

Errors propagate visibly; no failed operation is reported as successful.
The checkpoint is saved after sandbox creation and before generated code runs.
If creation succeeds but the host crashes before recording its ID, inspect the
group for labels `scenario=resumable-approval` and `session=<directory-name>`
and manually clean up that orphan.

Only `awaiting_approval` can continue. If a phase fails, times out, or the host
dies midway, use cleanup and start a new session rather than automatically
replaying potentially non-idempotent code. `--session out/another-run` selects
a fresh directory; use the same option with continue and cleanup.

Do not edit the checkpoint or draft. Changed draft hashes fail closed. Changing
Azure environment variables does not redirect an existing session; model endpoint,
deployment, and API version must match the checkpoint. Lost local state, sandbox
deletion/expiry, or service retention limits cannot be recovered by this sample.

## Offline tests

From the repository root:

```powershell
uv run --group test --extra openai pytest tests/test_resumable_approval.py
```

Tests use fake sandbox/model clients. They never run generated code on your
computer, contact Azure, or incur model charges. A passing offline test is
not proof that your live credentials, region, image, or model deployment work.
