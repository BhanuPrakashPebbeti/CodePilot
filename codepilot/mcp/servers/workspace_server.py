"""Workspace intelligence MCP server.

Provides project-aware context: detects project type, frameworks, 
structure, and dependencies. This is the agent's "eyes" for understanding
what kind of project it's working with before taking action.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

app = FastMCP(name="workspace")


# ============================================================================
# PROJECT DETECTION
# ============================================================================

@app.tool()
def detect_project(directory: str = ".") -> str:
    """Detect project type, language, framework, and configuration.

    Scans the directory for marker files (package.json, requirements.txt,
    Cargo.toml, etc.) and returns structured project metadata.

    Args:
        directory: Project root directory.

    Returns:
        JSON string with project metadata.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        return json.dumps({"error": f"Not a directory: {directory}"})

    result: Dict[str, Any] = {
        "root": str(root),
        "languages": [],
        "frameworks": [],
        "package_manager": None,
        "has_git": (root / ".git").is_dir(),
        "has_tests": False,
        "entry_points": [],
        "config_files": [],
        "dependencies_file": None,
    }

    # --- Marker-file detection ---
    markers = {
        "package.json": ("javascript", "node", "npm"),
        "yarn.lock": ("javascript", "node", "yarn"),
        "pnpm-lock.yaml": ("javascript", "node", "pnpm"),
        "bun.lockb": ("javascript", "node", "bun"),
        "requirements.txt": ("python", None, "pip"),
        "pyproject.toml": ("python", None, "pip"),
        "setup.py": ("python", None, "pip"),
        "Pipfile": ("python", None, "pipenv"),
        "poetry.lock": ("python", None, "poetry"),
        "Cargo.toml": ("rust", None, "cargo"),
        "go.mod": ("go", None, "go"),
        "pom.xml": ("java", None, "maven"),
        "build.gradle": ("java", None, "gradle"),
        "Gemfile": ("ruby", None, "bundler"),
        "composer.json": ("php", None, "composer"),
        "Makefile": (None, None, "make"),
        "CMakeLists.txt": ("cpp", None, "cmake"),
    }

    for filename, (lang, _fmk, pkg) in markers.items():
        if (root / filename).exists():
            if lang and lang not in result["languages"]:
                result["languages"].append(lang)
            if pkg and not result["package_manager"]:
                result["package_manager"] = pkg
            result["config_files"].append(filename)

    # --- Framework detection ---
    # Python frameworks
    if "python" in result["languages"]:
        _detect_python_frameworks(root, result)

    # JS/TS frameworks
    if "javascript" in result["languages"]:
        _detect_js_frameworks(root, result)

    # --- Test directory detection ---
    test_dirs = ["tests", "test", "__tests__", "spec", "specs"]
    for td in test_dirs:
        if (root / td).is_dir():
            result["has_tests"] = True
            break

    # --- Entry points ---
    entry_candidates = [
        "main.py", "app.py", "index.py", "manage.py", "server.py",
        "index.js", "index.ts", "app.js", "app.ts", "server.js", "server.ts",
        "main.go", "main.rs", "Main.java",
        "src/index.js", "src/index.ts", "src/main.py", "src/app.py",
        "src/main.rs", "src/main.go",
    ]
    for ep in entry_candidates:
        if (root / ep).exists():
            result["entry_points"].append(ep)

    return json.dumps(result, indent=2)


def _detect_python_frameworks(root: Path, result: dict) -> None:
    """Detect Python frameworks from requirements/imports."""
    framework_markers = {
        "fastapi": "FastAPI",
        "flask": "Flask",
        "django": "Django",
        "streamlit": "Streamlit",
        "typer": "Typer CLI",
        "click": "Click CLI",
        "pytest": "pytest",
        "scrapy": "Scrapy",
        "celery": "Celery",
        "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic",
    }

    # Check requirements.txt
    req_file = root / "requirements.txt"
    if req_file.exists():
        try:
            content = req_file.read_text(encoding="utf-8").lower()
            for marker, name in framework_markers.items():
                if marker in content and name not in result["frameworks"]:
                    result["frameworks"].append(name)
        except Exception:
            pass

    # Check pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8").lower()
            for marker, name in framework_markers.items():
                if marker in content and name not in result["frameworks"]:
                    result["frameworks"].append(name)
        except Exception:
            pass


def _detect_js_frameworks(root: Path, result: dict) -> None:
    """Detect JS/TS frameworks from package.json."""
    pkg_file = root / "package.json"
    if not pkg_file.exists():
        return

    try:
        with open(pkg_file, "r") as f:
            pkg = json.load(f)

        all_deps = {}
        all_deps.update(pkg.get("dependencies", {}))
        all_deps.update(pkg.get("devDependencies", {}))

        framework_markers = {
            "react": "React",
            "next": "Next.js",
            "vue": "Vue",
            "nuxt": "Nuxt",
            "svelte": "Svelte",
            "@sveltejs/kit": "SvelteKit",
            "angular": "Angular",
            "express": "Express",
            "fastify": "Fastify",
            "vite": "Vite",
            "tailwindcss": "Tailwind CSS",
            "typescript": "TypeScript",
            "jest": "Jest",
            "vitest": "Vitest",
            "mocha": "Mocha",
        }

        for marker, name in framework_markers.items():
            if marker in all_deps and name not in result["frameworks"]:
                result["frameworks"].append(name)

        # TypeScript detection
        if (root / "tsconfig.json").exists() and "typescript" not in result["languages"]:
            result["languages"].append("typescript")

    except Exception:
        pass


# ============================================================================
# DIRECTORY TREE & STRUCTURE
# ============================================================================

@app.tool()
def get_project_tree(directory: str = ".", max_depth: int = 4, max_files: int = 500) -> str:
    """Get directory tree with smart filtering (skips node_modules, .git, etc.).

    Args:
        directory: Root directory.
        max_depth: Maximum depth to traverse.
        max_files: Maximum number of entries to show.

    Returns:
        Tree-formatted string.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        return f"Not a directory: {directory}"

    skip_dirs = {
        ".git", "__pycache__", ".pytest_cache", ".venv", "venv", "env",
        "node_modules", "dist", "build", ".next", ".nuxt", ".svelte-kit",
        "target", ".cargo", ".idea", ".vscode", ".egg-info",
        "coverage", ".tox", ".mypy_cache", ".ruff_cache",
        "__pypackages__", ".turbo",
    }

    lines = [str(root.name) + "/"]
    count = [0]

    def _walk(path: Path, prefix: str, depth: int):
        if depth > max_depth or count[0] > max_files:
            return

        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return

        # Filter out skipped directories and hidden files (except key dotfiles)
        keep_hidden = {".gitignore", ".env", ".env.example", ".dockerignore", ".eslintrc.json", ".prettierrc"}
        filtered = []
        for e in entries:
            if e.name in skip_dirs:
                continue
            if e.name.startswith(".") and e.name not in keep_hidden and not e.is_file():
                continue
            filtered.append(e)

        for i, entry in enumerate(filtered):
            if count[0] > max_files:
                lines.append(prefix + "└── ... (truncated)")
                return

            is_last = (i == len(filtered) - 1)
            connector = "└── " if is_last else "├── "
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{connector}{entry.name}{suffix}")
            count[0] += 1

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension, depth + 1)

    _walk(root, "", 1)

    if count[0] > max_files:
        lines.append(f"\n... truncated at {max_files} entries")

    return "\n".join(lines)


@app.tool()
def find_files(directory: str = ".", pattern: str = "*.py", max_results: int = 100) -> str:
    """Find files matching a glob pattern recursively.

    Args:
        directory: Root directory to search.
        pattern: Glob pattern (e.g., "*.py", "*.test.js", "Dockerfile*").
        max_results: Maximum results to return.

    Returns:
        Newline-separated list of matching file paths.
    """
    root = Path(directory).resolve()
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", "target"}

    matches = []
    for path in root.rglob(pattern):
        # Skip entries inside ignored directories
        if any(part in skip_dirs for part in path.parts):
            continue
        matches.append(str(path.relative_to(root)))
        if len(matches) >= max_results:
            break

    if not matches:
        return f"No files matching '{pattern}' found in {directory}"

    return "\n".join(matches)


@app.tool()
def get_file_overview(path: str) -> str:
    """Get a high-level overview of a source file: imports, classes, functions, line count.

    Args:
        path: Path to source file.

    Returns:
        JSON string with file overview.
    """
    file_path = Path(path)
    if not file_path.exists():
        return json.dumps({"error": f"File not found: {path}"})

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return json.dumps({"error": str(e)})

    lines = content.splitlines()
    total_lines = len(lines)
    ext = file_path.suffix.lower()

    result: Dict[str, Any] = {
        "file": str(file_path),
        "extension": ext,
        "total_lines": total_lines,
        "imports": [],
        "classes": [],
        "functions": [],
        "exports": [],
    }

    # Language-specific parsing
    if ext in (".py",):
        _parse_python_overview(lines, result)
    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs"):
        _parse_js_overview(lines, result)
    elif ext in (".java",):
        _parse_java_overview(lines, result)
    elif ext in (".go",):
        _parse_go_overview(lines, result)
    elif ext in (".rs",):
        _parse_rust_overview(lines, result)

    return json.dumps(result, indent=2)


def _parse_python_overview(lines: List[str], result: dict) -> None:
    """Parse Python file for overview."""
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"^(from\s+\S+\s+)?import\s+", stripped):
            result["imports"].append({"line": i, "text": stripped})
        elif m := re.match(r"^class\s+(\w+)", stripped):
            result["classes"].append({"line": i, "name": m.group(1)})
        elif m := re.match(r"^def\s+(\w+)", stripped):
            result["functions"].append({"line": i, "name": m.group(1)})
        elif m := re.match(r"^    def\s+(\w+)", line):  # methods (indented)
            # Find parent class
            for cls in reversed(result["classes"]):
                if cls["line"] < i:
                    result["functions"].append({"line": i, "name": f"{cls['name']}.{m.group(1)}"})
                    break


def _parse_js_overview(lines: List[str], result: dict) -> None:
    """Parse JS/TS file for overview."""
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"^import\s+", stripped):
            result["imports"].append({"line": i, "text": stripped[:120]})
        elif m := re.match(r"^(?:export\s+)?class\s+(\w+)", stripped):
            result["classes"].append({"line": i, "name": m.group(1)})
        elif m := re.match(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", stripped):
            result["functions"].append({"line": i, "name": m.group(1)})
        elif m := re.match(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)\s*=>|function)", stripped):
            result["functions"].append({"line": i, "name": m.group(1)})
        elif re.match(r"^export\s+(?:default\s+)?", stripped):
            result["exports"].append({"line": i, "text": stripped[:80]})


def _parse_java_overview(lines: List[str], result: dict) -> None:
    """Parse Java file for overview."""
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"^import\s+", stripped):
            result["imports"].append({"line": i, "text": stripped})
        elif m := re.match(r"^(?:public|private|protected)?\s*(?:abstract\s+)?(?:static\s+)?class\s+(\w+)", stripped):
            result["classes"].append({"line": i, "name": m.group(1)})
        elif m := re.match(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?\w+\s+(\w+)\s*\(", stripped):
            result["functions"].append({"line": i, "name": m.group(1)})


def _parse_go_overview(lines: List[str], result: dict) -> None:
    """Parse Go file for overview."""
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if m := re.match(r"^func\s+(\w+)", stripped):
            result["functions"].append({"line": i, "name": m.group(1)})
        elif m := re.match(r"^func\s+\(\w+\s+\*?(\w+)\)\s+(\w+)", stripped):
            result["functions"].append({"line": i, "name": f"{m.group(1)}.{m.group(2)}"})
        elif m := re.match(r"^type\s+(\w+)\s+struct", stripped):
            result["classes"].append({"line": i, "name": m.group(1)})


def _parse_rust_overview(lines: List[str], result: dict) -> None:
    """Parse Rust file for overview."""
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"^use\s+", stripped):
            result["imports"].append({"line": i, "text": stripped})
        elif m := re.match(r"^(?:pub\s+)?fn\s+(\w+)", stripped):
            result["functions"].append({"line": i, "name": m.group(1)})
        elif m := re.match(r"^(?:pub\s+)?struct\s+(\w+)", stripped):
            result["classes"].append({"line": i, "name": m.group(1)})
        elif m := re.match(r"^(?:pub\s+)?enum\s+(\w+)", stripped):
            result["classes"].append({"line": i, "name": m.group(1)})


# ============================================================================
# DEPENDENCY ANALYSIS
# ============================================================================

@app.tool()
def read_dependencies(directory: str = ".") -> str:
    """Read and parse project dependencies from standard files.

    Supports: package.json, requirements.txt, pyproject.toml, Cargo.toml, go.mod.

    Args:
        directory: Project root.

    Returns:
        JSON string with dependencies grouped by type.
    """
    root = Path(directory).resolve()
    result: Dict[str, Any] = {"dependencies": {}, "dev_dependencies": {}, "source": None}

    # package.json
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            result["dependencies"] = data.get("dependencies", {})
            result["dev_dependencies"] = data.get("devDependencies", {})
            result["source"] = "package.json"
            return json.dumps(result, indent=2)
        except Exception:
            pass

    # requirements.txt
    req = root / "requirements.txt"
    if req.exists():
        try:
            lines = req.read_text().splitlines()
            deps = {}
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("-"):
                    # Parse name==version or name>=version etc.
                    m = re.match(r"([a-zA-Z0-9_-]+)\s*([><=!~]+.+)?", line)
                    if m:
                        deps[m.group(1)] = (m.group(2) or "any").strip()
            result["dependencies"] = deps
            result["source"] = "requirements.txt"
            return json.dumps(result, indent=2)
        except Exception:
            pass

    # pyproject.toml (basic parsing)
    pyp = root / "pyproject.toml"
    if pyp.exists():
        try:
            content = pyp.read_text()
            # Extract dependencies list
            dep_match = re.search(r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL)
            if dep_match:
                dep_str = dep_match.group(1)
                deps = {}
                for item in re.findall(r'"([^"]+)"', dep_str):
                    m = re.match(r"([a-zA-Z0-9_-]+)\s*(.*)", item)
                    if m:
                        deps[m.group(1)] = m.group(2) or "any"
                result["dependencies"] = deps
                result["source"] = "pyproject.toml"
                return json.dumps(result, indent=2)
        except Exception:
            pass

    return json.dumps({"error": "No recognized dependency file found", "checked": str(root)})


@app.tool()
def search_codebase(directory: str, query: str, file_pattern: str = "*", max_results: int = 50) -> str:
    """Search for text/regex across all files in a project.

    Args:
        directory: Root directory.
        query: Search string or regex pattern.
        file_pattern: Glob to filter files (e.g., "*.py", "*.js").
        max_results: Max matches to return.

    Returns:
        Matches with file path, line number, and context.
    """
    root = Path(directory).resolve()
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", "target"}

    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        # Fall back to literal search
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    results = []
    for path in root.rglob(file_pattern):
        if any(part in skip_dirs for part in path.parts):
            continue
        if not path.is_file():
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    results.append(f"{path.relative_to(root)}:{i}: {line.strip()[:150]}")
                    if len(results) >= max_results:
                        return "\n".join(results) + f"\n... (truncated at {max_results})"
        except Exception:
            continue

    return "\n".join(results) if results else f"No matches for '{query}'"


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
