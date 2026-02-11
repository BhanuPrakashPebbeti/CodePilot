"""Bash MCP server for command execution."""
import logging
import os
import subprocess
from typing import Dict, Any

from fastmcp import FastMCP

app = FastMCP(name="bash")


def _execute_command(command: str, cwd: str = ".", timeout: int = 60) -> Dict[str, Any]:
    """Internal helper to execute commands.
    
    Args:
        command: Command to execute.
        cwd: Working directory.
        timeout: Command timeout in seconds.
    
    Returns:
        Dictionary with stdout, stderr, exit_code, and success.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }

    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout} seconds", "exit_code": -1, "success": False}
    except Exception as e:
        return {"error": str(e), "exit_code": -1, "success": False}


@app.tool()
def run_command(command: str, cwd: str = ".") -> str:
    """Run bash command and return output.

    Args:
        command: Command to execute.
        cwd: Working directory.

    Returns:
        JSON string with stdout, stderr, and exit code.
    """
    result = _execute_command(command, cwd)
    return str(result)


@app.tool()
def run_python(file_path: str, args: str = "") -> str:
    """Run Python file and return output.

    Args:
        file_path: Path to Python file.
        args: Command line arguments.

    Returns:
        JSON string with stdout, stderr, and exit code.
    """
    command = f"python {file_path}"
    if args:
        command += f" {args}"

    result = _execute_command(command)
    return str(result)


@app.tool()
def install_package(package: str) -> str:
    """Install Python package using pip.

    Args:
        package: Package name or requirements.

    Returns:
        Installation output.
    """
    command = f"pip install {package}"
    result = _execute_command(command)
    return str(result)


@app.tool()
def check_command_exists(command: str) -> bool:
    """Check if command exists in PATH.

    Args:
        command: Command name.

    Returns:
        True if command exists.
    """
    result = subprocess.run(
        f"which {command}",
        shell=True,
        capture_output=True,
    )
    return result.returncode == 0


@app.tool()
def npm_command(command: str, cwd: str = ".") -> str:
    """Run npm command.
    
    Args:
        command: npm command (e.g., "install", "start", "run build").
        cwd: Working directory.
    
    Returns:
        Command output.
    """
    full_command = f"npm {command}"
    result = _execute_command(full_command, cwd=cwd)
    return str(result)


@app.tool()
def install_npm_packages(packages: str, cwd: str = ".", dev: bool = False) -> str:
    """Install npm packages.
    
    Args:
        packages: Space-separated package names (e.g., "react react-dom axios").
        cwd: Working directory.
        dev: Install as dev dependencies.
    
    Returns:
        Installation output.
    """
    flag = "--save-dev" if dev else ""
    command = f"npm install {flag} {packages}"
    result = _execute_command(command, cwd=cwd)
    return str(result)
    command = f"npm install {flag} {packages}"
    return run_command(command, cwd=cwd)


@app.tool()
def check_node_installed() -> str:
    """Check if Node.js and npm are installed.
    
    Returns:
        Version information or error message.
    """
    node_result = subprocess.run(
        "node --version",
        shell=True,
        capture_output=True,
        text=True,
    )
    
    npm_result = subprocess.run(
        "npm --version",
        shell=True,
        capture_output=True,
        text=True,
    )
    
    if node_result.returncode == 0 and npm_result.returncode == 0:
        return f"Node: {node_result.stdout.strip()}, npm: {npm_result.stdout.strip()}"
    return "Node.js/npm not installed"


@app.tool()
def get_system_info() -> str:
    """Get system information.

    Returns:
        System info summary.
    """
    info_parts = []

    # Python version
    py_result = subprocess.run(
        "python --version",
        shell=True,
        capture_output=True,
        text=True,
    )
    info_parts.append(f"Python: {py_result.stdout.strip()}")

    # OS info
    os_result = subprocess.run(
        "uname -a",
        shell=True,
        capture_output=True,
        text=True,
    )
    if os_result.returncode == 0:
        info_parts.append(f"OS: {os_result.stdout.strip()}")

    return "\n".join(info_parts)

if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"

    logging.getLogger().setLevel(logging.ERROR)

    app.run(
        transport="stdio",
        show_banner=False,
        log_level="error"
    )
