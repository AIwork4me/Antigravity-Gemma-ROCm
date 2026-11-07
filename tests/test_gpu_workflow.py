"""Guards for .github/workflows/gpu-validation.yml (runner security, T1.1 P0-1).

Static assertions only — nothing here dispatches workflows or executes
injected commands. If someone reintroduces `${{ inputs ... }}` inside a
`run:`/`shell:` block, these tests fail.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "gpu-validation.yml"
TEXT = WF.read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT / "scripts"))

import gpu_validate as gv  # noqa: E402


def test_no_direct_input_interpolation():
    """Dispatch inputs must never be spliced into shell strings: every
    user-controlled `${{ inputs.* }}` occurrence must live on an env-var
    mapping line, never inside a run/shell/artifact-name block.
    (`${{ github.run_id }}` is server-generated and not restricted.)"""
    for line in TEXT.splitlines():
        if "inputs." in line and "${{" in line:
            assert re.match(r"^\s*GPUVAL_\w+:\s*\$\{\{", line), (
                f"user input outside an env mapping: {line.strip()!r}"
            )
    assert "${{ github.event.inputs" not in TEXT


def test_inputs_travel_via_environment():
    assert TEXT.count("GPUVAL_MODEL: ${{ inputs.model }}") >= 1
    assert "$env:GPUVAL_MODEL" in TEXT  # consumed as a variable, not spliced


def test_least_privilege_permissions():
    assert re.search(r"^permissions:\s*$\n^\s+contents:\s+read\s*$", TEXT, re.M)


def test_checkout_persists_no_credentials():
    assert "persist-credentials: false" in TEXT


def test_trusted_refs_only():
    assert "github.repository == 'AIwork4me/Antigravity-Gemma-ROCm'" in TEXT
    assert "github.ref == 'refs/heads/main'" in TEXT
    assert "workflow_dispatch" in TEXT  # manual only — no pull_request trigger
    assert "pull_request" not in TEXT.split("on:")[1].split("jobs:")[0]


def test_protected_environment_and_controls():
    assert "environment: gpu-validation" in TEXT
    assert "timeout-minutes:" in TEXT
    assert re.search(r"^concurrency:", TEXT, re.M)
    assert "cancel-in-progress: false" in TEXT


def test_inline_validator_matches_script_gate():
    """The workflow's inline regex must accept/reject exactly like
    gpu_validate.validate_model_tag (single source of truth for semantics)."""
    m = re.search(r"re\.match\(r\"(\^[^\"]+)\"", TEXT)
    assert m, "inline validator regex not found"
    inline = re.compile(m.group(1))
    for tag in ("gemma4:26b", "qwen3:8b", "a-b/c:tag-x"):
        assert gv.validate_model_tag(tag)[0]
        assert inline.match(tag)
    for tag in ("evil; calc", "a && b", "$(x)", "with space", "-x", ""):
        assert not gv.validate_model_tag(tag)[0]
        assert not inline.match(tag)
