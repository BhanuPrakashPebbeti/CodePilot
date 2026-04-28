"""Local testing tools — replaces testing_server.py MCP."""

import json
import re
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from .exec import _clean_env, _resolve_cwd


def _detect_test_runner(cwd: Path) -> Optional[str]:
    if (cwd / "pytest.ini").exists() or (cwd / "pyproject.toml").exists():
        return "python -m pytest"
    if (cwd / "package.json").exists():
        try:
            data = json.loads((cwd / "package.json").read_text())
            scripts = data.get("scripts", {})
            if "test" in scripts:
                return "npm test"
            if "vitest" in str(data.get("devDependencies", {})):
                return "npx vitest run"
        except Exception:
            pass
    if (cwd / "Cargo.toml").exists():
        return "cargo test"
    if (cwd / "go.mod").exists():
        return "go test ./..."
    return None


def run_tests(
    tool_context: ToolContext,
    cwd: str = ".",
    runner: str = "",
    timeout: int = 120,
) -> dict:
    """Auto-detect and run the project's test suite.

    Args:
        cwd: Project root directory.
        runner: Override test runner command (e.g. "pytest -x"). Auto-detected if empty.
        timeout: Max seconds to wait (default 120).

    Returns:
        dict with ok, passed, failed, output.
    """
    p = _resolve_cwd(cwd, tool_context)
    cmd = runner or _detect_test_runner(p) or "echo 'No test runner detected'"
    r = subprocess.run(cmd, shell=True, cwd=str(p),
                       capture_output=True, text=True, timeout=timeout, env=_clean_env())
    output = (r.stdout + r.stderr).strip()
    # Parse pass/fail counts
    passed = 0
    failed = 0
    m = re.search(r"(\d+) passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        failed = int(m.group(1))
    return {
        "ok": r.returncode == 0,
        "passed": passed,
        "failed": failed,
        "exit_code": r.returncode,
        "output": output[-3000:],
    }


def check_syntax(path: str, tool_context: ToolContext) -> dict:
    """Check Python syntax for a file.

    Args:
        path: File path to check.

    Returns:
        dict with ok and any syntax errors.
    """
    from .exec import _resolve_cwd as _rc
    p = _rc(path, tool_context)
    r = subprocess.run(
        f"python3 -m py_compile {str(p)}", shell=True,
        capture_output=True, text=True, env=_clean_env(),
    )
    return {"ok": r.returncode == 0, "error": r.stderr.strip() or None}


def http_request(
    url: str,
    tool_context: ToolContext,
    method: str = "GET",
    body: str = "",
    headers: str = "",
    timeout: int = 10,
) -> dict:
    """Send an HTTP request and return status + body.

    Args:
        url: Full URL (e.g. "http://localhost:8000/health").
        method: HTTP method (GET, POST, PUT, DELETE).
        body: Request body JSON string (for POST/PUT).
        headers: JSON string of extra headers (e.g. '{"Authorization": "Bearer ..."}').
        timeout: Max seconds to wait (default 10).

    Returns:
        dict with ok, status_code, body.
    """
    try:
        req_headers = {}
        if headers:
            try:
                req_headers = json.loads(headers)
            except Exception:
                pass
        if body:
            req_headers.setdefault("Content-Type", "application/json")

        req = urllib.request.Request(
            url, method=method,
            data=body.encode() if body else None,
            headers=req_headers,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode(errors="replace")[:4000]
            return {"ok": True, "status_code": resp.status, "body": resp_body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status_code": e.code, "body": e.read().decode(errors="replace")[:1000]}
    except urllib.error.URLError as e:
        return {"ok": False, "error": str(e.reason), "status_code": 0}
    except Exception as e:
        return {"ok": False, "error": str(e), "status_code": 0}
