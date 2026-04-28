"""Local ADK FunctionTools for CodePilot agents.

These replace all internal MCP servers (filesystem, bash, git, workspace,
testing, debug, environment, planning, memory) with direct Python calls.
No subprocess spawn overhead — tools run in the same agent process.

Only external integrations (Playwright, GitHub) remain as MCP.
Notion and Slack now use local Python tools (notion_tools, slack_hitl)
for fine-grained schema control and HITL support.

Tool sets per agent
-------------------
Planner    → workspace + environment + planning + memory + notion (create project/tasks/log)
Developer  → fs + exec + git + workspace + environment + planning + notion (update task status)
Runtime    → exec + testing + state + notion (log execution) + slack (notify on failure)
TestAgent  → testing + state + notion (log test result)
DebugAgent → debug_tools + fs + exec + memory + validation + state + notion + slack (HITL)
Finalizer  → fs + git + exec + memory + state + notion (project status) + slack (notify)
             (+ GitHub MCP externally for PR creation)
"""

from .debug_tools import find_errors_in_output, parse_error, read_log_tail
from .environment import check_runtime, check_venv, create_venv, detect_runtimes
from .exec import (
    get_background_output,
    run_command,
    run_script,
    start_background_process,
    stop_background_process,
    wait_for_port,
)
from .fs import (
    append_file,
    copy_file,
    create_directory,
    delete_file,
    edit_lines,
    file_exists,
    list_directory,
    move_file,
    read_file,
    read_lines,
    replace_in_file,
    search_in_file,
    write_file,
)
from .git import (
    git_add,
    git_checkout,
    git_commit,
    git_commit_all,
    git_create_branch,
    git_diff,
    git_info,
    git_init,
    git_log,
    git_push,
    git_status,
)
from .memory_tools import (
    delete_memory,
    get_project_context,
    get_recent_conversations,
    search_memories,
    store_memory,
)
from .notion_tools import (
    notion_add_task,
    notion_create_project,
    notion_log_execution,
    notion_update_project_status,
    notion_update_task_status,
)
from .planning import (
    complete_task,
    create_plan,
    fail_task,
    get_current_task,
    get_plan_status,
    skip_task,
    start_task,
)
from .slack_hitl import slack_ask_human, slack_notify
from .testing import check_syntax, http_request, run_tests
from .state import exit_loop, set_state, ALLOWED_STATE_KEYS
from .validation import check_exit_conditions, force_exit_conditions
from .workspace import (
    detect_project,
    find_files,
    get_project_tree,
    read_dependencies,
    search_codebase,
)

# ---------------------------------------------------------------------------
# Agent tool bundles
# ---------------------------------------------------------------------------

PLANNER_TOOLS = [
    # Project understanding
    detect_project, get_project_tree, find_files, read_dependencies,
    # Environment
    detect_runtimes, check_runtime,
    # Planning (writes to ADK state)
    create_plan, get_plan_status,
    # Memory (check prior work before planning)
    get_recent_conversations, search_memories,
    # Notion: create project page + add tasks + log plan event
    notion_create_project, notion_add_task, notion_log_execution,
    # State: store notion_project_id after creating project
    set_state,
]

DEVELOPER_TOOLS = [
    # Filesystem
    read_file, write_file, append_file, replace_in_file, edit_lines,
    create_directory, list_directory, file_exists, search_in_file,
    # Execution
    run_command, run_script,
    # Project analysis
    detect_project, get_project_tree, find_files, search_codebase, read_dependencies,
    # Environment
    detect_runtimes, check_runtime, create_venv, check_venv,
    # Git (conventional commits: "feat: ...", "fix: ...", "chore: ...")
    git_init, git_status, git_add, git_commit, git_commit_all, git_info,
    # Task management
    get_current_task, start_task, complete_task, fail_task, skip_task, get_plan_status,
    # Notion: update task status as work progresses
    notion_update_task_status, notion_log_execution,
]

RUNTIME_TOOLS = [
    # Execution
    run_command, start_background_process, stop_background_process,
    wait_for_port, get_background_output,
    # Verification
    http_request, run_tests,
    # Notion: log run/error events
    notion_log_execution,
    # Slack: notify on failure so humans are aware
    slack_notify,
]

TEST_TOOLS = [
    # HTTP testing (Playwright MCP handles browser UI)
    http_request, run_tests, check_syntax,
    # Notion: log test results, mark task BLOCKED on failure
    notion_update_task_status, notion_log_execution,
]

DEBUG_TOOLS = [
    # Error analysis
    parse_error, find_errors_in_output, read_log_tail,
    # File fixing
    read_file, replace_in_file, write_file,
    # Execution (run diagnostics)
    run_command,
    # Memory (search known fixes, save new ones)
    search_memories, store_memory,
    # Task management (mark task failed when fix is exhausted)
    fail_task,
    # Exit-condition gate (MUST check before exit_loop)
    check_exit_conditions, force_exit_conditions,
    # Notion: log fixes + mark tasks BLOCKED/DONE/FAILED
    notion_update_task_status, notion_log_execution,
    # Slack: notify on persistent failures + HITL decisions
    slack_notify, slack_ask_human,
]

FINALIZER_TOOLS = [
    # File writing (README)
    read_file, write_file,
    # Process management (stop servers)
    stop_background_process,
    # Git (final commit + push)
    git_status, git_add, git_commit_all, git_info, git_push,
    # Execution (cleanup commands)
    run_command,
    # Memory (save session summary)
    store_memory, get_project_context,
    # Notion: mark project COMPLETED/FAILED with summary
    notion_update_project_status, notion_log_execution,
    # Slack: post completion/failure notification
    slack_notify,
]

__all__ = [
    # fs
    "read_file", "write_file", "append_file", "replace_in_file", "edit_lines",
    "create_directory", "list_directory", "delete_file", "move_file", "copy_file",
    "file_exists", "search_in_file",
    # exec
    "run_command", "run_script", "start_background_process", "stop_background_process",
    "wait_for_port", "get_background_output",
    # git
    "git_init", "git_status", "git_add", "git_commit", "git_commit_all",
    "git_log", "git_diff", "git_info", "git_create_branch", "git_checkout", "git_push",
    # workspace
    "detect_project", "get_project_tree", "find_files", "search_codebase", "read_dependencies",
    # planning
    "create_plan", "get_current_task", "start_task", "complete_task",
    "fail_task", "skip_task", "get_plan_status",
    # testing
    "run_tests", "check_syntax", "http_request",
    # environment
    "detect_runtimes", "check_runtime", "create_venv", "check_venv",
    # debug
    "parse_error", "find_errors_in_output", "read_log_tail",
    # memory
    "store_memory", "search_memories", "get_recent_conversations",
    "get_project_context", "delete_memory",
    # notion
    "notion_create_project", "notion_add_task", "notion_update_task_status",
    "notion_update_project_status", "notion_log_execution",
    # slack
    "slack_notify", "slack_ask_human",
    # validation
    "check_exit_conditions", "force_exit_conditions",
    # bundles
    "PLANNER_TOOLS", "DEVELOPER_TOOLS", "RUNTIME_TOOLS",
    "TEST_TOOLS", "DEBUG_TOOLS", "FINALIZER_TOOLS",
    # state / control
    "set_state", "exit_loop", "ALLOWED_STATE_KEYS",
]
