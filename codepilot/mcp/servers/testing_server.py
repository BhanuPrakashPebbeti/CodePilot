"""Testing & Verification MCP server — language-agnostic testing and validation.

Provides generalized tools for running tests, checking syntax, linting code,
making HTTP requests to verify APIs, and validating command output.
All tools are tech-stack agnostic — they auto-detect frameworks and languages.

Tools:
  run_tests        — Auto-detect test framework and run the full suite.
  run_single_test  — Run a specific test file with any framework.
  check_syntax     — Validate file syntax for any supported language.
  check_json_syntax— Validate JSON file syntax.
  lint_code        — Run the appropriate linter for a source file.
  http_request     — Make HTTP requests to verify API endpoints.
  verify_output    — Run a command and check output against expected text.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from fastmcp import FastMCP

app = FastMCP(name="testing")


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})


def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})


def _execute(command: str, cwd: str = ".", timeout: int = 120) -> Dict[str, Any]:
    """Run command and return result dict."""
    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timed out after {timeout}s", "exit_code": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "success": False}


def _detect_test_framework(directory: str) -> Dict[str, Any]:
    """Auto-detect which test framework(s) are available in a project."""
    root = Path(directory).resolve()
    frameworks = []

    # Python: pytest
    if any(root.rglob("test_*.py")) or any(root.rglob("*_test.py")) or (root / "tests").is_dir():
        frameworks.append({"name": "pytest", "command": "python -m pytest -v --tb=short"})

    # JavaScript/TypeScript: jest / vitest / mocha
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            scripts = pkg.get("scripts", {})

            if "vitest" in all_deps:
                frameworks.append({"name": "vitest", "command": "npx vitest run"})
            elif "jest" in all_deps:
                frameworks.append({"name": "jest", "command": "npx jest --verbose"})
            elif "mocha" in all_deps:
                frameworks.append({"name": "mocha", "command": "npx mocha"})

            # Fallback: npm test if test script exists and is non-default
            if "test" in scripts and not any(f["name"] in ("vitest","jest","mocha") for f in frameworks):
                test_cmd = scripts["test"]
                if test_cmd and "no test specified" not in test_cmd:
                    frameworks.append({"name": "npm-test", "command": "npm test -- --watchAll=false"})
        except Exception:
            pass

    # Go
    if any(root.rglob("*_test.go")):
        frameworks.append({"name": "go-test", "command": "go test ./... -v"})

    # Rust
    if (root / "Cargo.toml").exists():
        frameworks.append({"name": "cargo-test", "command": "cargo test"})

    # Java: Maven / Gradle
    if (root / "pom.xml").exists():
        frameworks.append({"name": "maven", "command": "mvn test"})
    elif (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        frameworks.append({"name": "gradle", "command": "gradle test"})

    # Ruby
    if (root / "Gemfile").exists():
        if (root / "spec").is_dir():
            frameworks.append({"name": "rspec", "command": "bundle exec rspec"})
        elif (root / "test").is_dir():
            frameworks.append({"name": "minitest", "command": "bundle exec rake test"})

    return {"frameworks": frameworks, "directory": str(root)}


# ============================================================================
# TEST RUNNERS
# ============================================================================

@app.tool()
def run_tests(directory: str = ".", args: str = "", timeout: int = 120) -> str:
    """Auto-detect the test framework and run the full test suite.

    Supports: pytest, jest, vitest, mocha, go test, cargo test,
    maven, gradle, rspec, minitest, and npm test scripts.

    Args:
        directory: Project directory containing tests.
        args: Additional arguments to pass to the test runner.
        timeout: Timeout in seconds (default: 120).

    Returns:
        JSON with framework detected, test output, and pass/fail summary.
    """
    detection = _detect_test_framework(directory)
    frameworks = detection["frameworks"]

    if not frameworks:
        return _err(
            f"No test framework detected in {directory}. "
            f"Ensure test files exist (test_*.py, *.test.js, *_test.go, etc.) "
            f"and the test framework is listed in dependencies."
        )

    fw = frameworks[0]
    cmd = fw["command"]
    if args:
        cmd += f" {args}"

    result = _execute(cmd, cwd=directory, timeout=timeout)
    summary = _parse_test_output(result["stdout"] + result["stderr"], fw["name"])

    return _ok({
        "framework": fw["name"],
        "command": cmd,
        "all_frameworks": [f["name"] for f in frameworks],
        **result,
        "summary": summary,
    })


def _parse_test_output(output: str, framework: str) -> Dict[str, Any]:
    """Parse test output for pass/fail counts (multi-framework)."""
    s = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0}

    if framework == "pytest":
        for key in ("passed", "failed", "error", "skipped"):
            m = re.search(rf"(\d+) {key}", output)
            if m:
                target = "errors" if key == "error" else key
                s[target] = int(m.group(1))
    elif framework in ("jest", "vitest"):
        m = re.search(r"Tests:\s+(\d+) passed", output)
        if m: s["passed"] = int(m.group(1))
        m = re.search(r"(\d+) failed", output)
        if m: s["failed"] = int(m.group(1))
    elif framework == "go-test":
        s["passed"] = len(re.findall(r"--- PASS:", output))
        s["failed"] = len(re.findall(r"--- FAIL:", output))
    elif framework == "cargo-test":
        m = re.search(r"test result: \w+\. (\d+) passed; (\d+) failed", output)
        if m:
            s["passed"], s["failed"] = int(m.group(1)), int(m.group(2))

    s["total"] = s["passed"] + s["failed"] + s["errors"] + s["skipped"]
    return s


@app.tool()
def run_single_test(test_file: str, cwd: str = ".", framework: str = "", args: str = "") -> str:
    """Run a single test file with the specified or auto-detected framework.

    Args:
        test_file: Path to test file.
        cwd: Working directory.
        framework: Test framework override (pytest, jest, vitest, mocha, go, cargo).
                   If empty, auto-detects from file extension.
        args: Additional arguments.

    Returns:
        JSON with test result.
    """
    if not framework:
        ext = Path(test_file).suffix.lower()
        framework = {
            ".py": "pytest", ".js": "jest", ".jsx": "jest",
            ".ts": "jest", ".tsx": "jest", ".mjs": "jest",
            ".go": "go", ".rs": "cargo", ".rb": "rspec",
        }.get(ext, "pytest")

    commands = {
        "pytest": f"python -m pytest {test_file} -v --tb=short",
        "jest": f"npx jest {test_file} --verbose",
        "vitest": f"npx vitest run {test_file}",
        "mocha": f"npx mocha {test_file}",
        "go": f"go test -v -run {test_file}",
        "cargo": f"cargo test {test_file} -- --nocapture",
        "rspec": f"bundle exec rspec {test_file}",
    }

    cmd = commands.get(framework, f"python -m pytest {test_file} -v --tb=short")
    if args:
        cmd += f" {args}"

    result = _execute(cmd, cwd=cwd)
    return _ok({"framework": framework, "test_file": test_file, "command": cmd, **result})


# ============================================================================
# SYNTAX & LINT — Language-agnostic
# ============================================================================

@app.tool()
def check_syntax(file_path: str, cwd: str = ".") -> str:
    """Check a source file for syntax errors. Auto-detects language from extension.

    Supports: Python, JavaScript/TypeScript, JSON, Go, Rust, Ruby, PHP.

    Args:
        file_path: Path to the source file.
        cwd: Working directory.

    Returns:
        JSON with valid (bool), language, and any error details.
    """
    fp = Path(file_path)
    if not fp.exists():
        return _err(f"File not found: {file_path}")

    ext = fp.suffix.lower()

    if ext == ".json":
        try:
            json.loads(fp.read_text(encoding="utf-8"))
            return _ok({"file": file_path, "language": "json", "valid": True}, "Syntax OK")
        except json.JSONDecodeError as e:
            return _ok({"file": file_path, "language": "json", "valid": False,
                         "error": str(e), "line": e.lineno}, "Syntax errors found")

    checks = {
        ".py": "python -m py_compile",
        ".js": "node --check", ".jsx": "node --check", ".mjs": "node --check",
        ".ts": "npx tsc --noEmit --allowJs", ".tsx": "npx tsc --noEmit",
        ".go": "go vet", ".rb": "ruby -c", ".php": "php -l",
    }
    lang_names = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript",
        ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    }

    cmd = checks.get(ext)
    if not cmd:
        return _ok({"file": file_path, "language": ext, "valid": None,
                     "message": f"No syntax checker for {ext} files"})

    result = _execute(f"{cmd} {file_path}", cwd=cwd, timeout=30)
    return _ok({
        "file": file_path, "language": lang_names.get(ext, ext),
        "valid": result["success"],
        "errors": result["stderr"] if not result["success"] else "",
    }, "Syntax OK" if result["success"] else "Syntax errors found")


@app.tool()
def check_json_syntax(file_path: str) -> str:
    """Validate JSON file syntax.

    Args:
        file_path: Path to JSON file.

    Returns:
        JSON with validation result.
    """
    try:
        fp = Path(file_path).resolve()
        if not fp.exists():
            return _err(f"File not found: {file_path}")
        content = fp.read_text(encoding="utf-8")
        json.loads(content)
        return _ok({"file": file_path, "valid": True}, "Valid JSON")
    except json.JSONDecodeError as e:
        return _ok({"file": file_path, "valid": False, "error": str(e),
                     "line": e.lineno, "column": e.colno}, "Invalid JSON")
    except Exception as e:
        return _err(str(e))


@app.tool()
def lint_code(file_path: str, cwd: str = ".") -> str:
    """Run the appropriate linter for a source file. Auto-detects language.

    Supports: Python (ruff/flake8), JavaScript/TypeScript (eslint),
    Go (staticcheck/go vet), Rust (clippy).

    Args:
        file_path: Path to source file.
        cwd: Working directory.

    Returns:
        JSON with linting results.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".py":
        result = _execute(f"python -m ruff check {file_path} --output-format=text", cwd=cwd)
        if result["exit_code"] != 127:
            return _ok({"file": file_path, "language": "python", "linter": "ruff",
                         "clean": result["success"], "output": result["stdout"] or result["stderr"]})
        result = _execute(f"python -m flake8 {file_path}", cwd=cwd)
        if result["exit_code"] != 127:
            return _ok({"file": file_path, "language": "python", "linter": "flake8",
                         "clean": result["success"], "output": result["stdout"] or result["stderr"]})

    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs"):
        result = _execute(f"npx eslint {file_path}", cwd=cwd, timeout=30)
        if result["exit_code"] != 127:
            return _ok({"file": file_path, "language": "javascript", "linter": "eslint",
                         "clean": result["success"], "output": result["stdout"] or result["stderr"]})

    elif ext == ".go":
        result = _execute(f"staticcheck {file_path}", cwd=cwd)
        if result["exit_code"] == 127:
            result = _execute(f"go vet {file_path}", cwd=cwd)
        linter = "staticcheck" if result["exit_code"] != 127 else "go-vet"
        return _ok({"file": file_path, "language": "go", "linter": linter,
                     "clean": result["success"], "output": result["stdout"] or result["stderr"]})

    elif ext == ".rs":
        result = _execute("cargo clippy", cwd=cwd, timeout=60)
        if result["exit_code"] != 127:
            return _ok({"file": file_path, "language": "rust", "linter": "clippy",
                         "clean": result["success"], "output": result["stdout"] or result["stderr"]})

    return _ok({"file": file_path, "language": ext, "linter": None,
                 "message": f"No linter available for {ext} files"})


# ============================================================================
# HTTP VERIFICATION — Test API endpoints and web apps
# ============================================================================

@app.tool()
def http_request(
    url: str,
    method: str = "GET",
    headers: str = "",
    body: str = "",
    timeout: int = 10,
) -> str:
    """Make an HTTP request and return the response.

    Use this to verify API endpoints, check server health, test CRUD operations,
    and validate web application functionality after deployment.

    Args:
        url: Full URL (e.g. "http://localhost:8000/api/health").
        method: HTTP method — GET, POST, PUT, DELETE, PATCH (default: GET).
        headers: JSON string of headers (e.g. '{"Content-Type": "application/json"}').
        body: Request body for POST/PUT/PATCH. Can be JSON string or plain text.
        timeout: Request timeout in seconds (default: 10).

    Returns:
        JSON with status_code, response headers, body, and elapsed time.
    """
    import time as _time

    try:
        req_headers = {}
        if headers:
            try:
                req_headers = json.loads(headers)
            except json.JSONDecodeError:
                return _err(f"Invalid headers JSON: {headers}")

        data = body.encode("utf-8") if body else None
        if data and "Content-Type" not in req_headers:
            try:
                json.loads(body)
                req_headers["Content-Type"] = "application/json"
            except (json.JSONDecodeError, ValueError):
                req_headers["Content-Type"] = "text/plain"

        req = Request(url, data=data, headers=req_headers, method=method.upper())

        start = _time.time()
        try:
            response = urlopen(req, timeout=timeout)
            elapsed = _time.time() - start
            resp_body = response.read().decode("utf-8", errors="replace")

            try:
                body_display = json.dumps(json.loads(resp_body), indent=2)[:3000]
            except (json.JSONDecodeError, ValueError):
                body_display = resp_body[:3000]

            return _ok({
                "status_code": response.status, "url": url, "method": method.upper(),
                "response_headers": dict(list(response.headers.items())[:20]),
                "body": body_display, "body_length": len(resp_body),
                "elapsed_ms": round(elapsed * 1000, 1),
            }, f"{method.upper()} {url} → {response.status}")

        except HTTPError as e:
            elapsed = _time.time() - start
            err_body = e.read().decode("utf-8", errors="replace")[:1000]
            return _ok({
                "status_code": e.code, "url": url, "method": method.upper(),
                "error_body": err_body, "elapsed_ms": round(elapsed * 1000, 1),
            }, f"{method.upper()} {url} → {e.code} {e.reason}")

        except URLError as e:
            return _err(f"Connection failed: {url} — {e.reason}. Is the server running?")

    except Exception as e:
        return _err(f"HTTP request failed: {e}")


# ============================================================================
# FUNCTIONAL VERIFICATION — Check actual program behavior
# ============================================================================

@app.tool()
def verify_output(
    command: str,
    expected: str,
    cwd: str = ".",
    timeout: int = 30,
    expect_in: str = "stdout",
) -> str:
    """Run a command and verify its output contains expected text.

    Use this for functional testing: run the program and check that the
    output matches what you expect. Works with any language or tool.

    Args:
        command: Command to execute (e.g. "python solver.py", "node app.js").
        expected: Text that should appear in the output.
        cwd: Working directory.
        timeout: Timeout in seconds.
        expect_in: Where to look — "stdout", "stderr", or "any" (default: stdout).

    Returns:
        JSON with passed (bool), actual output, and match details.
    """
    result = _execute(command, cwd=cwd, timeout=timeout)

    if expect_in == "stdout":
        search_text = result["stdout"]
    elif expect_in == "stderr":
        search_text = result["stderr"]
    else:
        search_text = result["stdout"] + "\n" + result["stderr"]

    found = expected in search_text
    regex_match = False
    if not found:
        try:
            if re.search(expected, search_text):
                found = True
                regex_match = True
        except re.error:
            pass

    return _ok({
        "passed": found, "command": command, "expected": expected[:200],
        "match_type": "regex" if regex_match else ("literal" if found else "none"),
        "exit_code": result["exit_code"],
        "stdout": result["stdout"][:2000],
        "stderr": result["stderr"][:1000],
    }, "✓ Output matches expected" if found else "✗ Expected text not found in output")


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
