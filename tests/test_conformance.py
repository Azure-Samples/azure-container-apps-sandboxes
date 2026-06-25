"""Offline gate: structural invariants for the samples tree.

The plain asserts below hold on main and must stay green. The three
specs at the bottom encode audit findings A1, A2, and A3; the fixes
have landed, so they now assert hard.
"""

import re
import tomllib

import yaml

from conftest import (
    REPO_ROOT,
    _git_ls_files,
    sample_dirs,
    tracked_md_files,
)

EM_DASH = "\u2014"


def _cli_run_shell_files():
    return _git_ls_files("cli/**/run.sh")


def _cli_egress_yaml_files():
    return _git_ls_files("cli/**/*.yaml", "cli/**/*.yml")


# --- invariants that hold today -------------------------------------------


def test_cli_tree_is_python_free():
    py = _git_ls_files("cli/**/*.py")
    assert not py, f"unexpected .py files under cli/: {py}"
    python_dirs = [
        p
        for p in (REPO_ROOT / "cli").rglob("python")
        if p.is_dir()
    ]
    assert not python_dirs, f"unexpected 'python' dirs under cli/: {python_dirs}"


def test_no_em_dash_in_markdown():
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in tracked_md_files()
        if EM_DASH in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"em-dash found in markdown: {offenders}"


def test_root_readme_indexes_every_sample():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    missing = []
    for base in ("python/samples", "cli/samples"):
        for d in sample_dirs(base):
            if d.name not in readme:
                missing.append(f"{base}/{d.name}")
    assert not missing, f"root README.md missing sample entries: {missing}"


def test_cli_egress_yaml_is_valid():
    for path in _cli_egress_yaml_files():
        with path.open(encoding="utf-8") as fh:
            yaml.safe_load(fh)


def test_pyproject_declares_expected_extras():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = set(data["project"]["optional-dependencies"])
    for expected in ("openai", "agents", "connectors"):
        assert expected in extras, f"missing extra: {expected}"


# --- audit findings A1, A2, A3 (fixed) ------------------------------------

RAW_INSTALL_RE = re.compile(
    r"raw\.githubusercontent\.com/[^\s\"']*install\.(?:sh|ps1)"
)


def test_a1_install_url_uses_aka_ms():
    offenders = []
    targets = tracked_md_files() + _cli_run_shell_files()
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if RAW_INSTALL_RE.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "aca install references must use https://aka.ms/aca-cli-install "
        f"(or -ps), not raw.githubusercontent.com: {offenders}"
    )


NAME_FLAG_RE = re.compile(
    r"aca\s+sandboxgroup\s+(?:identity|role)\b[^\n]*--name\b"
)


def test_a2_sandboxgroup_subcommands_use_group_flag():
    offenders = []
    for path in _cli_run_shell_files():
        text = path.read_text(encoding="utf-8")
        if NAME_FLAG_RE.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "aca sandboxgroup identity/role must use --group, not --name: "
        f"{offenders}"
    )


def _has_transform_rule(doc) -> bool:
    if not isinstance(doc, dict):
        return False
    for rule in doc.get("rules", []) or []:
        action = rule.get("action") if isinstance(rule, dict) else None
        if isinstance(action, dict) and action.get("type") == "Transform":
            return True
    return False


def test_a3_deny_transform_policy_sets_traffic_inspection():
    offenders = []
    for path in _cli_egress_yaml_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        if doc.get("defaultAction") == "Deny" and _has_transform_rule(doc):
            if "trafficInspection" not in doc:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "Deny + Transform egress policy must set trafficInspection (Full): "
        f"{offenders}"
    )
