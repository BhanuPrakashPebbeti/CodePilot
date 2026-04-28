"""Local workspace analysis tools — replaces workspace_server.py MCP."""

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from .exec import _clean_env, _resolve_cwd

_IGNORE = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", ".cache", "coverage",
    ".pytest_cache", ".mypy_cache", "*.egg-info",
}

_LANG_MARKERS = {
    "python": ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"],
    "nodejs": ["package.json"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
    "java": ["pom.xml", "build.gradle"],
    "ruby": ["Gemfile"],
}


def detect_project(tool_context: ToolContext, cwd: str = ".") -> dict:
    """Detect project language, frameworks, and entry points.

    Args:
        cwd: Directory to analyze.

    Returns:
        dict with ok, language, frameworks (list), entry_points (list), type.
    """
    p = _resolve_cwd(cwd, tool_context)
    if not p.is_dir():
        return {"ok": False, "error": f"Directory not found: {cwd}"}

    files = {f.name for f in p.iterdir() if f.is_file()}
    language = "unknown"
    frameworks: list = []
    entry_points: list = []
    proj_type = "unknown"

    for lang, markers in _LANG_MARKERS.items():
        if any(m in files for m in markers):
            language = lang
            break

    # Detect frameworks
    if language == "python":
        reqs = p / "requirements.txt"
        if reqs.exists():
            text = reqs.read_text(errors="replace").lower()
            for fw in ["flask", "fastapi", "django", "sanic", "tornado", "aiohttp"]:
                if fw in text:
                    frameworks.append(fw)
        for ep in ["main.py", "app.py", "server.py", "run.py", "manage.py"]:
            if (p / ep).exists():
                entry_points.append(ep)
    elif language == "nodejs":
        pkg = p / "package.json"
        if pkg.exists():
            import json
            try:
                data = json.loads(pkg.read_text())
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                for fw in ["react", "vue", "angular", "next", "nuxt", "express", "fastify", "nest"]:
                    if any(fw in d for d in deps):
                        frameworks.append(fw)
                scripts = data.get("scripts", {})
                entry_points = list(scripts.keys())[:5]
            except Exception:
                pass

    # Infer project type
    if any(f in ["react", "vue", "angular", "next", "nuxt"] for f in frameworks):
        proj_type = "web"
    elif any(f in ["flask", "fastapi", "django", "express", "fastify"] for f in frameworks):
        proj_type = "api"
    elif "cli" in str(entry_points).lower() or "cli.py" in files:
        proj_type = "cli"
    elif entry_points:
        proj_type = "script"

    return {
        "ok": True, "language": language, "frameworks": frameworks,
        "entry_points": entry_points, "type": proj_type,
    }


def get_project_tree(
    tool_context: ToolContext,
    cwd: str = ".",
    max_depth: int = 3,
) -> dict:
    """Return a directory tree of the project (ignoring common non-source dirs).

    Args:
        cwd: Root directory.
        max_depth: Maximum depth to traverse (default 3).

    Returns:
        dict with ok and tree (string).
    """
    p = _resolve_cwd(cwd, tool_context)

    def _walk(path: Path, depth: int, prefix: str) -> list:
        if depth > max_depth:
            return ["    " + prefix + "..."]
        entries = []
        try:
            items = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name))
        except PermissionError:
            return []
        for item in items:
            if item.name in _IGNORE or item.name.startswith("."):
                continue
            connector = "├── " if item != items[-1] else "└── "
            entries.append(prefix + connector + item.name)
            if item.is_dir():
                extension = "│   " if item != items[-1] else "    "
                entries.extend(_walk(item, depth + 1, prefix + extension))
        return entries

    lines = [str(p)] + _walk(p, 1, "")
    return {"ok": True, "tree": "\n".join(lines)}


def find_files(
    pattern: str,
    tool_context: ToolContext,
    cwd: str = ".",
) -> dict:
    """Find files matching a glob pattern.

    Args:
        pattern: Glob pattern (e.g. "**/*.py", "src/**/*.ts").
        cwd: Root directory.

    Returns:
        dict with ok and files (list of relative paths).
    """
    p = _resolve_cwd(cwd, tool_context)
    try:
        matches = [
            str(f.relative_to(p))
            for f in p.glob(pattern)
            if not any(part in _IGNORE for part in f.parts)
        ]
        return {"ok": True, "files": sorted(matches), "count": len(matches)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def search_codebase(
    query: str,
    tool_context: ToolContext,
    cwd: str = ".",
    file_pattern: str = "",
) -> dict:
    """Search for a string or regex across all source files.

    Args:
        query: Text or regex to search for.
        cwd: Root directory.
        file_pattern: Optional glob to restrict search (e.g. "*.py").

    Returns:
        dict with ok and matches (list of {file, line_no, line}).
    """
    p = _resolve_cwd(cwd, tool_context)
    args = ["rg", "--no-heading", "--line-number", "--max-count", "5"]
    if file_pattern:
        args += ["-g", file_pattern]
    args += ["--", query, str(p)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, env=_clean_env())
        matches = []
        for line in r.stdout.splitlines()[:50]:
            parts = line.split(":", 2)
            if len(parts) >= 3:
                matches.append({"file": parts[0], "line_no": parts[1], "line": parts[2]})
        return {"ok": True, "matches": matches, "count": len(matches)}
    except FileNotFoundError:
        # ripgrep not available — fall back to Python grep
        try:
            regex = re.compile(query)
            glob = file_pattern or "**/*"
            matches = []
            for f in p.glob(glob):
                if not f.is_file() or any(part in _IGNORE for part in f.parts):
                    continue
                try:
                    for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                        if regex.search(line):
                            matches.append({"file": str(f.relative_to(p)), "line_no": i, "line": line})
                            if len(matches) >= 50:
                                break
                except Exception:
                    pass
                if len(matches) >= 50:
                    break
            return {"ok": True, "matches": matches, "count": len(matches)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def read_dependencies(tool_context: ToolContext, cwd: str = ".") -> dict:
    """Read dependency manifest(s) from the project root.

    Returns requirements.txt, package.json, Cargo.toml, go.mod, etc. as-is.

    Args:
        cwd: Project root.

    Returns:
        dict with ok and files (dict of filename → content).
    """
    p = _resolve_cwd(cwd, tool_context)
    candidates = [
        "requirements.txt", "package.json", "Cargo.toml", "go.mod",
        "pyproject.toml", "Pipfile", "Gemfile", "pom.xml", "build.gradle",
    ]
    found = {}
    for name in candidates:
        fp = p / name
        if fp.exists():
            found[name] = fp.read_text(errors="replace")
    return {"ok": True, "files": found}
