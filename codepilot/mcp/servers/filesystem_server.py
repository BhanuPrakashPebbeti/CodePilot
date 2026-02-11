"""Filesystem MCP server with fine-grained file tools."""

import logging
import os
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

app = FastMCP(name="filesystem")


@app.tool()
def read_file(path: str) -> str:
    """Read entire file content.

    Args:
        path: Path to file (relative or absolute).

    Returns:
        File content.

    Raises:
        FileNotFoundError: If file doesn't exist.
        IsADirectoryError: If path is a directory.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if file_path.is_dir():
        raise IsADirectoryError(f"Path is a directory: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@app.tool()
def read_lines(path: str, start: int, end: int) -> str:
    """Read specific lines from file.

    Args:
        path: Path to file.
        start: Start line number (1-indexed).
        end: End line number (1-indexed, inclusive).

    Returns:
        Content of specified lines.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Convert to 0-indexed
    start_idx = max(0, start - 1)
    end_idx = min(len(lines), end)

    return "".join(lines[start_idx:end_idx])


@app.tool()
def write_file(path: str, content: str) -> str:
    """Write content to file (overwrite if exists).

    Args:
        path: Path to file.
        content: Content to write.

    Returns:
        Success message.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return f"File written: {path}"


@app.tool()
def append_file(path: str, content: str) -> str:
    """Append content to file.

    Args:
        path: Path to file.
        content: Content to append.

    Returns:
        Success message.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "a", encoding="utf-8") as f:
        f.write(content)

    return f"Content appended to: {path}"


@app.tool()
def replace_in_file(path: str, search: str, replace: str) -> str:
    """Replace text in file.

    Args:
        path: Path to file.
        search: Text to find.
        replace: Text to replace with.

    Returns:
        Success message with count of replacements.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_content = content.replace(search, replace)
    count = content.count(search)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return f"Replaced {count} occurrence(s) in {path}"


@app.tool()
def create_file(path: str, content: str = "") -> str:
    """Create file with optional content.

    Args:
        path: Path to file.
        content: Optional content to write (default: empty file).

    Returns:
        Success message.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    if content:
        return f"File created with content: {path}"
    return f"Empty file created: {path}"

    return f"File created: {path}"


@app.tool()
def delete_file(path: str) -> str:
    """Delete file.

    Args:
        path: Path to file.

    Returns:
        Success message.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    file_path.unlink()
    return f"File deleted: {path}"


@app.tool()
def list_dir(path: str = ".") -> str:
    """List directory contents.

    Args:
        path: Directory path.

    Returns:
        Directory listing with file types.

    Raises:
        NotADirectoryError: If path is not a directory.
    """
    dir_path = Path(path)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    items = []
    try:
        for item in sorted(dir_path.iterdir()):
            item_type = "dir" if item.is_dir() else "file"
            items.append(f"{item.name:<50} ({item_type})")
    except PermissionError:
        return f"Permission denied: {path}"

    return "\n".join(items) if items else f"Empty directory: {path}"


@app.tool()
def file_exists(path: str) -> bool:
    """Check if file exists.

    Args:
        path: Path to file.

    Returns:
        True if file exists.
    """
    return Path(path).exists()


@app.tool()
def project_tree() -> str:
    """Get project directory tree structure.

    Returns:
        Directory tree visualization.
    """
    root = Path(".")
    tree_lines = []

    def add_tree(path: Path, prefix: str = "", is_last: bool = True):
        """Recursively add directory contents to tree."""
        if len(tree_lines) > 1000:  # Limit output
            return

        # Skip common ignored directories
        skip_dirs = {
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".venv",
            "venv",
            "node_modules",
            "dist",
            "build",
            ".forgecode",
            ".egg-info",
        }

        if path.name in skip_dirs:
            return

        try:
            items = sorted(path.iterdir())
        except PermissionError:
            return

        dirs = [item for item in items if item.is_dir()]
        files = [item for item in items if item.is_file()]

        for i, item in enumerate(dirs + files):
            is_last_item = i == len(dirs) + len(files) - 1
            current_prefix = "└── " if is_last_item else "├── "
            tree_lines.append(prefix + current_prefix + item.name)

            if item.is_dir():
                next_prefix = prefix + ("    " if is_last_item else "│   ")
                add_tree(item, next_prefix, is_last_item)

    tree_lines.append(".")
    add_tree(root)

    return "\n".join(tree_lines[:1000])


@app.tool()
def file_summary(file_path: Optional[str] = None) -> str:
    """Get summary of files in project.

    Args:
        file_path: Optional specific file path.

    Returns:
        Summary of files.
    """
    if file_path:
        path = Path(file_path)
        if not path.exists():
            return f"File not found: {file_path}"

        if path.is_file():
            size = path.stat().st_size
            lines = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
            return f"{file_path}: {lines} lines, {size} bytes"

    # Summary of all files
    root = Path(".")
    file_count = 0
    total_size = 0
    skip_dirs = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}

    for root_dir, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for file in files:
            file_count += 1
            try:
                total_size += os.path.getsize(os.path.join(root_dir, file))
            except OSError:
                pass

    return f"Total files: {file_count}, Total size: {total_size} bytes"


@app.tool()
def edit_line(path: str, line_number: int, new_content: str) -> str:
    """Edit a specific line in file.

    Args:
        path: Path to file.
        line_number: Line number to edit (1-indexed).
        new_content: New content for the line.

    Returns:
        Success message.

    Raises:
        FileNotFoundError: If file doesn't exist.
        IndexError: If line number is out of range.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if line_number < 1 or line_number > len(lines):
        raise IndexError(f"Line {line_number} out of range (file has {len(lines)} lines)")

    lines[line_number - 1] = new_content if new_content.endswith("\n") else new_content + "\n"

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return f"Line {line_number} edited in {path}"


@app.tool()
def edit_lines(path: str, start_line: int, end_line: int, new_content: str) -> str:
    """Edit lines in range (replace with new content).

    Args:
        path: Path to file.
        start_line: Start line number (1-indexed).
        end_line: End line number (1-indexed, inclusive).
        new_content: New content to replace the range.

    Returns:
        Success message.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start_idx = max(0, start_line - 1)
    end_idx = min(len(lines), end_line)

    # Create new content with proper newlines
    new_lines = new_content.split("\n")
    new_lines = [line + "\n" if i < len(new_lines) - 1 else line 
                 for i, line in enumerate(new_lines)]

    result_lines = lines[:start_idx] + new_lines + lines[end_idx:]

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(result_lines)

    return f"Lines {start_line}-{end_line} replaced in {path}"


@app.tool()
def insert_lines(path: str, line_number: int, content: str) -> str:
    """Insert lines at specific position.

    Args:
        path: Path to file.
        line_number: Insert before this line (1-indexed).
        content: Content to insert.

    Returns:
        Success message.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    insert_idx = max(0, min(line_number - 1, len(lines)))
    new_lines = content.split("\n")
    new_lines = [line + "\n" if i < len(new_lines) - 1 else line 
                 for i, line in enumerate(new_lines)]

    result_lines = lines[:insert_idx] + new_lines + lines[insert_idx:]

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(result_lines)

    return f"Content inserted before line {line_number} in {path}"


@app.tool()
def delete_lines(path: str, start_line: int, end_line: int) -> str:
    """Delete lines in range.

    Args:
        path: Path to file.
        start_line: Start line number (1-indexed).
        end_line: End line number (1-indexed, inclusive).

    Returns:
        Success message.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start_idx = max(0, start_line - 1)
    end_idx = min(len(lines), end_line)

    result_lines = lines[:start_idx] + lines[end_idx:]

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(result_lines)

    return f"Lines {start_line}-{end_line} deleted from {path}"


@app.tool()
def count_lines(path: str) -> int:
    """Count total lines in file.

    Args:
        path: Path to file.

    Returns:
        Number of lines.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        return len(f.readlines())


@app.tool()
def create_project_structure(base_path: str, structure: str) -> str:
    """Create multiple directories at once for project structure.
    
    Args:
        base_path: Base project directory path.
        structure: Comma-separated list of subdirectories (e.g., "src,tests,docs,config").
    
    Returns:
        Success message.
    
    Example:
        create_project_structure("myapp", "frontend/src,frontend/public,backend/api,backend/models")
    """
    base = Path(base_path)
    base.mkdir(parents=True, exist_ok=True)
    
    dirs = [d.strip() for d in structure.split(",")]
    created = []
    
    for dir_path in dirs:
        full_path = base / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
        created.append(str(full_path))
    
    return f"Created {len(created)} directories in {base_path}: {', '.join(dirs)}"


@app.tool()
def get_file_info(path: str) -> str:
    """Get detailed file information.

    Args:
        path: Path to file.

    Returns:
        File information.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    stat = file_path.stat()
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = len(f.readlines())

    return f"""File: {path}
Size: {stat.st_size} bytes
Lines: {lines}
Modified: {stat.st_mtime}
Created: {stat.st_ctime}"""



@app.prompt()
def summarize_file(file_path: str) -> str:
    """Summarize the content and purpose of a file.

    Args:
        file_path: Path to file to summarize.

    Returns:
        Prompt for summarization.
    """
    try:
        content = read_file(file_path)
        return f"""Please summarize the following file '{file_path}':

```
{content[:2000]}
```

Provide a brief summary of what this file does and its purpose."""
    except Exception as e:
        return f"Error reading file: {e}"


@app.prompt()
def explain_file_purpose(file_path: str) -> str:
    """Explain the purpose and structure of a file.

    Args:
        file_path: Path to file to explain.

    Returns:
        Prompt for explanation.
    """
    try:
        content = read_file(file_path)
        return f"""Analyze the file '{file_path}' and explain:
1. What is the main purpose of this file?
2. What are the key components/classes/functions?
3. How does it fit into the project?

File content:
```
{content[:3000]}
```"""
    except Exception as e:
        return f"Error reading file: {e}"

if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"

    logging.getLogger().setLevel(logging.ERROR)

    app.run(
        transport="stdio",
        show_banner=False,
        log_level="error"
    )
