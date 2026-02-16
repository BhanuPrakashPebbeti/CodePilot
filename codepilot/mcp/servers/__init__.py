"""MCP server modules for CodePilot.

Available servers:
  - workspace_server:    Project detection, tree, file overview, dependencies
  - filesystem_server:   File CRUD operations
  - bash_server:         Command execution, background process management
  - testing_server:      Test runners, syntax validators, HTTP verification
  - debug_server:        Error parsing, log reading
  - environment_server:  Runtime detection, installation, venv management
  - planning_server:     Plan creation, task tracking, progress management
  - git_server:          Local git operations
  - github_server:       GitHub API (optional, requires token)
"""
