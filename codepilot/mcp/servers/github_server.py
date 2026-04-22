"""GitHub MCP server for GitHub REST API operations."""

import logging
import os
import subprocess
from typing import Optional

import requests

from fastmcp import FastMCP

from codepilot.mcp.servers._env import get_clean_env

app = FastMCP(name="github")


def _get_headers() -> dict:
    """Get GitHub API headers with authentication.

    Returns:
        Headers dict with authorization.

    Raises:
        ValueError: If GITHUB_TOKEN not set.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is not set")

    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_repo_url(repo_url: str) -> tuple[str, str]:
    """Parse repository URL to owner and repo name.

    Args:
        repo_url: Repository URL or name.

    Returns:
        Tuple of (owner, repo_name).

    Raises:
        ValueError: If URL format invalid.
    """
    if "github.com" in repo_url:
        # Extract from full URL
        parts = repo_url.replace(".git", "").split("/")
        return parts[-2], parts[-1]
    elif "/" in repo_url:
        # owner/repo format
        owner, repo = repo_url.split("/")
        return owner, repo
    else:
        raise ValueError(f"Invalid repository format: {repo_url}")


@app.tool()
def create_repo(
    name: str,
    description: str = "",
    private: bool = False,
) -> str:
    """Create new GitHub repository.

    Args:
        name: Repository name.
        description: Repository description.
        private: Whether repository should be private.

    Returns:
        Success message with repo URL.
    """
    headers = _get_headers()

    payload = {
        "name": name,
        "description": description,
        "private": private,
    }

    response = requests.post(
        "https://api.github.com/user/repos",
        json=payload,
        headers=headers,
        timeout=30,
    )

    if response.status_code in (201, 200):
        data = response.json()
        return f"Repository created: {data['html_url']}"

    return f"Error creating repository: {response.text}"


@app.tool()
def push_branch(repo: str, branch: str = "main") -> str:
    """Push branch to GitHub repository.

    Args:
        repo: Repository (owner/repo or URL).
        branch: Branch to push.

    Returns:
        Success message.
    """
    # Note: This requires git to be configured with GitHub credentials
    try:
        owner, repo_name = _parse_repo_url(repo)
        result = subprocess.run(
            f"git push origin {branch}",
            shell=True, capture_output=True, text=True, timeout=60,
            env=get_clean_env(),
        )
        if result.returncode == 0:
            return f"Pushed {branch} to {owner}/{repo_name}"
        return f"Error pushing branch: {result.stderr.strip() or 'check git configuration'}"
    except Exception as e:
        return f"Error: {str(e)}"


@app.tool()
def open_pr(
    repo: str,
    title: str,
    body: str = "",
    head_branch: str = "main",
    base_branch: str = "main",
) -> str:
    """Open pull request on GitHub.

    Args:
        repo: Repository (owner/repo or URL).
        title: PR title.
        body: PR description.
        head_branch: Source branch.
        base_branch: Target branch.

    Returns:
        Success message with PR URL.
    """
    try:
        owner, repo_name = _parse_repo_url(repo)
        headers = _get_headers()

        payload = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
        }

        response = requests.post(
            f"https://api.github.com/repos/{owner}/{repo_name}/pulls",
            json=payload,
            headers=headers,
            timeout=30,
        )

        if response.status_code == 201:
            data = response.json()
            return f"Pull request opened: {data['html_url']}"

        if response.status_code == 422:
            # PR might already exist
            return "Pull request may already exist or invalid branch"

        return f"Error creating pull request: {response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@app.tool()
def get_repo_info(repo: str) -> str:
    """Get GitHub repository information.

    Args:
        repo: Repository (owner/repo).

    Returns:
        Repository information.
    """
    try:
        owner, repo_name = _parse_repo_url(repo)
        headers = _get_headers()

        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo_name}",
            headers=headers,
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            return f"""Repository: {data['name']}
Description: {data['description']}
Stars: {data['stargazers_count']}
Forks: {data['forks_count']}
URL: {data['html_url']}"""

        return f"Error getting repo info: {response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@app.tool()
def list_prs(repo: str, state: str = "open") -> str:
    """List pull requests for repository.

    Args:
        repo: Repository (owner/repo).
        state: PR state (open, closed, all).

    Returns:
        List of pull requests.
    """
    try:
        owner, repo_name = _parse_repo_url(repo)
        headers = _get_headers()

        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo_name}/pulls",
            params={"state": state, "per_page": 10},
            headers=headers,
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            if not data:
                return f"No {state} pull requests"

            prs = []
            for pr in data:
                prs.append(f"#{pr['number']}: {pr['title']}")

            return "\n".join(prs)

        return f"Error listing pull requests: {response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@app.tool()
def get_github_user() -> str:
    """Get authenticated GitHub user information.

    Returns:
        User information.
    """
    try:
        headers = _get_headers()
        response = requests.get(
            "https://api.github.com/user",
            headers=headers,
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            return f"""GitHub User: {data['login']}
Name: {data['name']}
Bio: {data['bio']}
Public Repos: {data['public_repos']}
Followers: {data['followers']}"""

        return "Error getting user info"
    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"

    logging.getLogger().setLevel(logging.ERROR)

    app.run(
        transport="stdio",
        show_banner=False,
        log_level="error"
    )
