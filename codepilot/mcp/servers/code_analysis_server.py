"""Code analysis MCP server for understanding codebases."""

import logging
import os
import re
from pathlib import Path
from typing import List, Dict, Any

from fastmcp import FastMCP

app = FastMCP(name="code_analysis")


@app.tool()
def find_functions(file_path: str, language: str = "python") -> str:
    """Find all function definitions in a file.
    
    Args:
        file_path: Path to source file.
        language: Programming language (python, javascript, typescript, java, etc.).
    
    Returns:
        List of function names and their line numbers.
    """
    patterns = {
        "python": r"^\s*def\s+(\w+)\s*\(",
        "javascript": r"^\s*(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\([^)]*\)\s*=>))",
        "typescript": r"^\s*(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\([^)]*\)\s*=>))",
        "java": r"^\s*(?:public|private|protected)?\s*(?:static)?\s*\w+\s+(\w+)\s*\(",
        "cpp": r"^\s*(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{",
        "c": r"^\s*(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{",
    }
    
    pattern = patterns.get(language.lower(), patterns["python"])
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        functions = []
        for i, line in enumerate(lines, 1):
            match = re.search(pattern, line)
            if match:
                func_name = match.group(1) or match.group(2) if match.lastindex >= 2 else match.group(1)
                functions.append(f"Line {i}: {func_name}")
        
        return "\n".join(functions) if functions else "No functions found"
    
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def find_classes(file_path: str, language: str = "python") -> str:
    """Find all class definitions in a file.
    
    Args:
        file_path: Path to source file.
        language: Programming language.
    
    Returns:
        List of class names and their line numbers.
    """
    patterns = {
        "python": r"^\s*class\s+(\w+)",
        "javascript": r"^\s*class\s+(\w+)",
        "typescript": r"^\s*(?:export\s+)?class\s+(\w+)",
        "java": r"^\s*(?:public|private|protected)?\s*(?:abstract)?\s*class\s+(\w+)",
        "cpp": r"^\s*class\s+(\w+)",
    }
    
    pattern = patterns.get(language.lower(), patterns["python"])
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        classes = []
        for i, line in enumerate(lines, 1):
            match = re.search(pattern, line)
            if match:
                classes.append(f"Line {i}: {match.group(1)}")
        
        return "\n".join(classes) if classes else "No classes found"
    
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def find_imports(file_path: str, language: str = "python") -> str:
    """Find all import statements in a file.
    
    Args:
        file_path: Path to source file.
        language: Programming language.
    
    Returns:
        List of imports.
    """
    patterns = {
        "python": r"^\s*(?:from\s+[\w.]+\s+)?import\s+.+",
        "javascript": r"^\s*import\s+.+from\s+['\"].+['\"]",
        "typescript": r"^\s*import\s+.+from\s+['\"].+['\"]",
        "java": r"^\s*import\s+[\w.]+;",
    }
    
    pattern = patterns.get(language.lower(), patterns["python"])
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        imports = []
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line):
                imports.append(f"Line {i}: {line.strip()}")
        
        return "\n".join(imports) if imports else "No imports found"
    
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def count_lines_of_code(file_path: str) -> str:
    """Count lines of code (excluding comments and blank lines).
    
    Args:
        file_path: Path to source file.
    
    Returns:
        Statistics about the file.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        total = len(lines)
        blank = sum(1 for line in lines if not line.strip())
        comments = sum(1 for line in lines if line.strip().startswith(('#', '//', '/*', '*')))
        code = total - blank - comments
        
        return f"""Total lines: {total}
Code lines: {code}
Blank lines: {blank}
Comment lines: {comments}"""
    
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def find_todos(directory: str) -> str:
    """Find all TODO/FIXME comments in a directory.
    
    Args:
        directory: Directory to search.
    
    Returns:
        List of TODOs with file and line number.
    """
    todos = []
    pattern = re.compile(r'(TODO|FIXME|HACK|XXX|NOTE)[::\s]+(.+)', re.IGNORECASE)
    
    try:
        for root, dirs, files in os.walk(directory):
            # Skip common directories
            dirs[:] = [d for d in dirs if d not in {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build'}]
            
            for file in files:
                if file.endswith(('.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.h')):
                    file_path = Path(root) / file
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            for i, line in enumerate(f, 1):
                                match = pattern.search(line)
                                if match:
                                    todos.append(f"{file_path}:{i} [{match.group(1)}] {match.group(2).strip()}")
                    except:
                        pass
        
        return "\n".join(todos) if todos else "No TODOs found"
    
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def analyze_dependencies(file_path: str) -> str:
    """Analyze dependencies in package.json, requirements.txt, or similar files.
    
    Args:
        file_path: Path to dependency file.
    
    Returns:
        List of dependencies.
    """
    try:
        file_name = Path(file_path).name
        
        if file_name == "package.json":
            import json
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            deps = data.get('dependencies', {})
            dev_deps = data.get('devDependencies', {})
            
            result = "Dependencies:\n"
            for name, version in deps.items():
                result += f"  {name}: {version}\n"
            
            if dev_deps:
                result += "\nDev Dependencies:\n"
                for name, version in dev_deps.items():
                    result += f"  {name}: {version}\n"
            
            return result
        
        elif file_name == "requirements.txt":
            with open(file_path, 'r') as f:
                deps = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            
            return "Requirements:\n" + "\n".join(f"  {dep}" for dep in deps)
        
        else:
            return "Unsupported dependency file format"
    
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    
    app.run(
        transport="stdio",
        show_banner=False,
        log_level="error"
    )
