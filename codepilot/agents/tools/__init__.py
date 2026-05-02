"""Local ADK FunctionTools for CodePilot agents.

All internal capabilities run as direct Python calls (no MCP subprocess overhead).
Only Playwright and GitHub remain as external MCP servers.

Notion tools use local notion_client for structured databases:
  Planner     → notion_setup_project (creates page + 3 child DBs) + notion_create_task
  Developer   → notion_update_task + notion_add_comment + notion_log_event + notion_query_tasks
  Runtime     → notion_log_event
  TestAgent   → notion_create_qa_page + notion_log_qa_step + notion_finalize_qa + notion_add_artifact
  DebugAgent  → notion_query_tasks + notion_update_task + notion_add_comment + notion_log_event
  Finalizer   → notion_finalize_project + notion_log_event
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
    # Setup (PlannerAgent)
    notion_setup_project,
    # Task management
    notion_create_task,
    notion_update_task,
    notion_query_tasks,
    notion_add_comment,
    # Activity logging
    notion_log_event,
    # Test artifacts
    notion_add_artifact,
    # QA sub-page workflow (TestAgent)
    notion_create_qa_page,
    notion_log_qa_step,
    notion_add_screenshot,
    notion_finalize_qa,
    # Finalization (FinalizerAgent)
    notion_finalize_project,
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
from .slack_hitl import slack_ask_human, slack_notify, slack_structured_notify
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
    # Planning
    create_plan, get_plan_status,
    # Memory (check prior work)
    get_recent_conversations, search_memories,
    # Notion: setup full project structure + create tasks + log plan
    notion_setup_project, notion_create_task, notion_log_event,
    # Slack: structured plan-ready notification only
    slack_structured_notify,
    # State: store all notion IDs + project state
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
    # Git (conventional commits)
    git_init, git_status, git_add, git_commit, git_commit_all, git_info,
    # Task management
    get_current_task, start_task, complete_task, fail_task, skip_task, get_plan_status,
    # Notion: query tasks + update status + comment + log events
    notion_query_tasks, notion_update_task, notion_add_comment, notion_log_event,
    # Slack: one structured message when all tasks done
    slack_structured_notify,
]

RUNTIME_TOOLS = [
    # Execution
    run_command, start_background_process, stop_background_process,
    wait_for_port, get_background_output,
    # Verification
    http_request, run_tests,
    # Notion: log run/error events to Activity Log DB
    notion_log_event,
    # State
    set_state,
    # Slack: structured error notification only (silent on success)
    slack_structured_notify,
]

TEST_TOOLS = [
    # HTTP + unit testing
    http_request, run_tests, check_syntax,
    # File system: create screenshots dir
    create_directory, run_command,
    # State (test_result, screenshot_paths, notion_qa_page_id)
    set_state,
    # Notion: QA sub-page + step logs + screenshot recording + artifacts
    notion_create_qa_page, notion_log_qa_step, notion_add_screenshot,
    notion_finalize_qa, notion_add_artifact, notion_log_event,
    # Slack: one structured test-result notification
    slack_structured_notify,
]

DEBUG_TOOLS = [
    # Error analysis
    parse_error, find_errors_in_output, read_log_tail,
    # File fixing
    read_file, replace_in_file, write_file,
    # Execution
    run_command,
    # Memory: search known fixes, store new ones
    search_memories, store_memory,
    # Task management
    fail_task,
    # Exit-condition gate
    check_exit_conditions, force_exit_conditions,
    # Notion: query blocked tasks + update + comment + log fixes
    notion_query_tasks, notion_update_task, notion_add_comment, notion_log_event,
    # Slack: HITL only (no regular notifications)
    slack_ask_human,
    # State
    set_state,
]

FINALIZER_TOOLS = [
    # File writing (README)
    read_file, write_file,
    # Process management
    stop_background_process,
    # Git (final commit + push)
    git_status, git_add, git_commit_all, git_info, git_push,
    # Execution
    run_command,
    # Memory
    store_memory, get_project_context,
    # Notion: finalize project + log deploy event
    notion_finalize_project, notion_log_event,
    # Slack: final notification with ALL links (repo, PR, Notion)
    slack_structured_notify,
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
    # notion — structured project management
    "notion_setup_project",
    "notion_create_task", "notion_update_task", "notion_query_tasks", "notion_add_comment",
    "notion_log_event",
    "notion_add_artifact",
    "notion_create_qa_page", "notion_log_qa_step", "notion_add_screenshot", "notion_finalize_qa",
    "notion_finalize_project",
    # slack
    "slack_notify", "slack_ask_human", "slack_structured_notify",
    # validation
    "check_exit_conditions", "force_exit_conditions",
    # bundles
    "PLANNER_TOOLS", "DEVELOPER_TOOLS", "RUNTIME_TOOLS",
    "TEST_TOOLS", "DEBUG_TOOLS", "FINALIZER_TOOLS",
    # state / control
    "set_state", "exit_loop", "ALLOWED_STATE_KEYS",
]
