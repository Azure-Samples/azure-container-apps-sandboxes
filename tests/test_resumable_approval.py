"""Offline approval workflow tests. Generated Python is never executed here."""

import importlib.util
import json
from contextlib import ExitStack
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conftest import REPO_ROOT

SAMPLE = (
    REPO_ROOT / "python" / "samples" / "08-sandbox-agents" / "openai"
    / "04-resumable-approval" / "run.py"
)
spec = importlib.util.spec_from_file_location("resumable_approval", SAMPLE)
assert spec is not None and spec.loader is not None
sample = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sample
spec.loader.exec_module(sample)


class FakeSandbox:
    def __init__(self):
        self.files = {}
        self.events = []
        self.feedback = "exit_code=0\n"

    def run(self, code):
        self.events.append(("run", code))
        target = sample.FINAL if code == "final-code" else sample.DRAFT
        self.files[target] = b"# Report\nNotebook 60, Pen 40, Folder 30. Total 130.\n"
        return self.feedback

    def write(self, path, content):
        self.events.append(("write", path))
        self.files[path] = content

    def read(self, path):
        self.events.append(("read", path))
        return self.files[path]

    def stop(self):
        self.events.append(("stop",))

    def resume(self):
        self.events.append(("resume",))

    def delete(self):
        self.events.append(("delete",))


class FakeModel:
    def __init__(self, code="draft-code"):
        self.source = code
        self.histories = []

    def code(self, messages):
        self.histories.append(list(messages))
        return self.source


@pytest.fixture
def session(tmp_path):
    state = sample.State(
        "saved-subscription", "saved-rg", "saved-group", "eastus",
        "https://example.openai.azure.com/", "chat-deployment", "2025-04-01-preview",
        sandbox_id="same-sandbox-id",
    )
    return state, tmp_path / "checkpoint.json", FakeSandbox()


def test_draft_checkpoint_then_separate_invocation_restores_same_session(session):
    state, checkpoint, sandbox = session
    sample.draft(state, checkpoint, sandbox, FakeModel())
    assert state.stage == "awaiting_approval"
    assert sandbox.events[-1] == ("stop",)
    assert sample.FINAL not in sandbox.files
    original_history = list(state.messages)

    # Discard the first host's state: only disk and the remote sandbox survive.
    restored = sample.load(checkpoint)
    assert restored is not state
    assert restored.sandbox_id == "same-sandbox-id"
    assert restored.messages == original_history
    model = FakeModel("final-code")
    sandbox.events.clear()
    sample.continue_session(restored, checkpoint, sandbox, model, approved=True)
    assert sandbox.events[0] == ("resume",)
    assert sandbox.events[1] == ("read", sample.DRAFT)
    assert not any(event[0] == "write" for event in sandbox.events)
    assert model.histories[0][:len(original_history)] == original_history
    assert restored.stage == "completed"
    assert restored.sandbox_id == "same-sandbox-id"
    assert (checkpoint.parent / "final.md").read_bytes() == sandbox.files[sample.FINAL]
    assert sandbox.events[-1] == ("stop",)


def test_unapproved_continue_has_no_sandbox_or_model_calls(session):
    state, checkpoint, sandbox = session
    sample.draft(state, checkpoint, sandbox, FakeModel())
    sandbox.events.clear()
    model = FakeModel()
    with pytest.raises(ValueError, match="Human approval required"):
        sample.continue_session(state, checkpoint, sandbox, model, approved=False)
    assert sandbox.events == []
    assert model.histories == []
    assert sample.load(checkpoint).stage == "awaiting_approval"


def test_cli_rejects_before_constructing_clients(session, monkeypatch):
    state, checkpoint, sandbox = session
    sample.draft(state, checkpoint, sandbox, FakeModel())
    connect = Mock(side_effect=AssertionError("Must not construct an Azure client"))
    monkeypatch.setattr(sample, "connect", connect)
    monkeypatch.setattr(sample, "connect_model", connect)
    monkeypatch.setattr(sys, "argv", ["run.py", "continue", "--session", str(checkpoint.parent)])
    with pytest.raises(ValueError, match="Human approval required"):
        sample.main()
    connect.assert_not_called()


def test_start_reports_all_missing_configuration_without_traceback(tmp_path, monkeypatch, capsys):
    settings = (
        "ACA_SUBSCRIPTION_ID", "ACA_RESOURCE_GROUP", "ACA_SANDBOXGROUP_NAME",
        "ACA_SANDBOXGROUP_REGION", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT",
    )
    for key in settings:
        monkeypatch.delenv(key, raising=False)
    sample_dir = tmp_path / "session"
    monkeypatch.setattr(
        sys, "argv", ["run.py", "start", "--session", str(sample_dir)],
    )
    with pytest.raises(SystemExit) as result:
        sample.main()
    captured = capsys.readouterr()
    assert result.value.code == 2
    assert "Missing required settings:" in captured.err
    assert all(key in captured.err for key in settings)
    assert "Traceback" not in captured.err
    assert not sample_dir.exists()


@pytest.mark.parametrize("changed", ["local", "remote"])
def test_modified_draft_cannot_be_finalized(session, changed):
    state, checkpoint, sandbox = session
    sample.draft(state, checkpoint, sandbox, FakeModel())
    if changed == "local":
        (checkpoint.parent / "draft.md").write_bytes(b"edited")
    else:
        sandbox.files[sample.DRAFT] = b"edited"
    model = FakeModel("final-code")
    with pytest.raises(ValueError, match="draft"):
        sample.continue_session(state, checkpoint, sandbox, model, approved=True)
    assert model.histories == []
    assert sample.FINAL not in sandbox.files


def test_failed_phase_is_bounded_and_not_silently_replayed(session):
    state, checkpoint, sandbox = session
    sandbox.feedback = "exit_code=1\ntraceback"
    model = FakeModel()
    with pytest.raises(RuntimeError, match="4 attempts"):
        sample.draft(state, checkpoint, sandbox, model)
    assert len(model.histories) == sample.MAX_TURNS
    assert sample.load(checkpoint).stage == "preparing"
    with pytest.raises(ValueError, match="Cannot continue"):
        sample.continue_session(state, checkpoint, sandbox, model, approved=True)


def test_stop_failure_does_not_claim_awaiting_approval(session):
    state, checkpoint, sandbox = session
    sandbox.stop = Mock(side_effect=TimeoutError("still running"))
    with pytest.raises(TimeoutError):
        sample.draft(state, checkpoint, sandbox, FakeModel())
    assert sample.load(checkpoint).stage == "preparing"
    sample.cleanup(state, checkpoint, sandbox)
    assert sample.load(checkpoint).stage == "deleted"


def test_cleanup_reconnects_using_saved_context_without_model_config(session, monkeypatch):
    state, checkpoint, sandbox = session
    sample.save(checkpoint, state)
    monkeypatch.setenv("ACA_SUBSCRIPTION_ID", "different-subscription")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    seen = []

    def connect(stack, restored, path, *, create):
        seen.append((restored.subscription, restored.sandbox_id, create))
        return sandbox

    monkeypatch.setattr(sample, "connect", connect)
    model = Mock(side_effect=AssertionError("Cleanup must not construct a model"))
    monkeypatch.setattr(sample, "connect_model", model)
    monkeypatch.setattr(sys, "argv", ["run.py", "cleanup", "--session", str(checkpoint.parent)])
    sample.main()
    assert seen == [("saved-subscription", "same-sandbox-id", False)]
    assert sandbox.events == [("delete",)]
    assert sample.load(checkpoint).stage == "deleted"
    model.assert_not_called()
    sample.main()
    assert sandbox.events == [("delete",)]


def test_cleanup_failure_keeps_checkpoint_recoverable(session):
    state, checkpoint, sandbox = session
    sample.save(checkpoint, state)
    sandbox.delete = Mock(side_effect=TimeoutError("not deleted"))
    with pytest.raises(TimeoutError):
        sample.cleanup(state, checkpoint, sandbox)
    assert sample.load(checkpoint).stage != "deleted"
    assert sample.load(checkpoint).sandbox_id == "same-sandbox-id"


@pytest.mark.parametrize(
    "content", [b"", b"x" * 64_001, b"\xff"], ids=["empty", "oversized", "invalid-utf8"],
)
def test_invalid_artifact_rejected_before_local_write(session, content):
    _, checkpoint, sandbox = session
    sandbox.files[sample.DRAFT] = content
    with pytest.raises((ValueError, UnicodeDecodeError)):
        sample.artifact(sandbox, sample.DRAFT, checkpoint.parent / "draft.md")
    assert not (checkpoint.parent / "draft.md").exists()


@pytest.mark.parametrize("corrupt", [
    {"version": 2},
    {"messages": [{"role": "tool", "content": "bad"}]},
    {"sandbox_id": 123},
    {"stage": "unknown"},
])
def test_corrupt_checkpoint_is_rejected(session, corrupt):
    state, checkpoint, _ = session
    sample.save(checkpoint, state)
    data = json.loads(checkpoint.read_text())
    data.update(corrupt)
    checkpoint.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        sample.load(checkpoint)


def test_sdk_adapter_uses_lifecycle_pollers_and_execution_deadline():
    client = Mock()
    client.exec.return_value = SimpleNamespace(exit_code=0, stdout="ok", stderr="")
    sandbox = sample.AzureSandbox(client)
    assert sandbox.run("print('ok')").startswith("exit_code=0\n")
    client.write_file.assert_called_once_with(
        f"{sample.WORKSPACE}/step.py", b"print('ok')",
    )
    client.exec.assert_called_once_with(
        f"timeout --signal=KILL 30s python3 {sample.WORKSPACE}/step.py",
    )
    sandbox.stop()
    client.begin_stop.assert_called_once_with(polling_timeout=120)
    client.begin_stop.return_value.result.assert_called_once()
    sandbox.resume()
    client.begin_resume.return_value.result.assert_called_once()
    sandbox.delete()
    client.begin_delete.return_value.result.assert_called_once()


def test_real_sdk_connection_factory_saves_id_and_sets_deny_egress(session, monkeypatch):
    import azure.containerapps.sandbox as sdk
    import azure.identity as identity
    from unittest.mock import MagicMock

    state, checkpoint, _ = session
    group = Mock()
    group.begin_create_sandbox.return_value.result.return_value.sandbox_id = "created-id"
    group_context = MagicMock()
    group_context.__enter__.return_value = group
    factory = Mock(return_value=group_context)
    monkeypatch.setattr(sdk, "SandboxGroupClient", factory)
    monkeypatch.setattr(identity, "DefaultAzureCredential", MagicMock())

    with ExitStack() as stack:
        sample.connect(stack, state, checkpoint, create=True)
    assert sample.load(checkpoint).sandbox_id == "created-id"
    options = group.begin_create_sandbox.call_args.kwargs
    assert options["disk"] == "python-3.14"
    assert options["egress_policy"].default_action == "Deny"
    assert options["egress_policy"].traffic_inspection == "Full"
    assert "ports" not in options and "environment" not in options

    with ExitStack() as stack:
        sample.connect(stack, sample.load(checkpoint), checkpoint, create=False)
    group.get_sandbox_client.assert_called_once_with("created-id")
    assert group.begin_create_sandbox.call_count == 1
    assert factory.call_args.kwargs["subscription_id"] == "saved-subscription"
    assert factory.call_args.kwargs["resource_group"] == "saved-rg"


@pytest.mark.parametrize("response", [
    None, "{}", '{"code": ""}', '{"code": 42}', '{"code": "ok", "extra": true}',
])
def test_model_adapter_rejects_invalid_code(session, monkeypatch, response):
    import openai
    from unittest.mock import MagicMock

    state, _, _ = session
    config = SimpleNamespace(
        endpoint=state.model_endpoint, deployment=state.model_deployment,
        api_version=state.api_version, api_key="test-key-not-a-real-secret",
    )
    monkeypatch.setattr(sample, "load_azure_openai_env", lambda: config)
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
    )
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(openai, "AzureOpenAI", Mock(return_value=context))
    with ExitStack() as stack:
        model = sample.connect_model(stack, state)
        with pytest.raises((ValueError, RuntimeError)):
            model.code([sample.Message("user", "Make a draft")])


def test_model_configuration_drift_rejected_before_client_creation(session, monkeypatch):
    import openai

    state, _, _ = session
    config = SimpleNamespace(
        endpoint="https://different.openai.azure.com/", deployment=state.model_deployment,
        api_version=state.api_version, api_key=None,
    )
    monkeypatch.setattr(sample, "load_azure_openai_env", lambda: config)
    factory = Mock(side_effect=AssertionError("Must not construct model client"))
    monkeypatch.setattr(openai, "AzureOpenAI", factory)
    with ExitStack() as stack, pytest.raises(ValueError, match="configuration changed"):
        sample.connect_model(stack, state)
    factory.assert_not_called()


def test_sample_compiles_and_readme_has_no_em_dash():
    compile(SAMPLE.read_text(encoding="utf-8"), str(SAMPLE), "exec")
    assert "\u2014" not in SAMPLE.with_name("README.md").read_text(encoding="utf-8")
