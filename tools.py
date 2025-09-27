# tools.py

import subprocess
import json
import os

# --- Constants for Truncation ---
MAX_TOOL_OUTPUT_LINES = 15
MAX_LINE_LENGTH = 150

# --- Helper Functions ---

def _truncate_output(output: str, max_lines: int, max_line_length: int) -> str:
    """
    Truncates a string for clean display by limiting both the number of lines
    and the length of each individual line.
    """
    lines = output.splitlines()
    original_line_count = len(lines)

    if original_line_count > max_lines:
        lines = lines[:max_lines]
        omitted_lines = original_line_count - max_lines
        truncation_message = f"\n... (output truncated, {omitted_lines} more lines hidden) ..."
    else:
        truncation_message = ""

    processed_lines = []
    for line in lines:
        if len(line) > max_line_length:
            processed_lines.append(line[:max_line_length] + " ... (line truncated) ...")
        else:
            processed_lines.append(line)

    return "\n".join(processed_lines) + truncation_message


def _format_shell_output(result: subprocess.CompletedProcess) -> str:
    """Formats the result of a subprocess command into a structured Markdown string."""
    output = ""
    if result.returncode == 0:
        output += "## Command Successful\n"
        if result.stdout: output += f"### STDOUT\n```\n{result.stdout.lstrip('\\n').rstrip()}\n```\n"
        if result.stderr: output += f"### STDERR\n```\n{result.stderr.lstrip('\\n').rstrip()}\n```\n"
        if not result.stdout and not result.stderr: output += "The command produced no output.\n"
    else:
        output += f"## Command FAILED (Exit Code: {result.returncode})\n"
        if result.stderr: output += f"### STDERR\n```\n{result.stderr.lstrip('\\n').rstrip()}\n```\n"
        if result.stdout: output += f"### STDOUT\n```\n{result.stdout.lstrip('\\n').rstrip()}\n```\n"
        if not result.stdout and not result.stderr: output += "The command produced no output.\n"
    return output

# --- Core Tools ---

def python(code: str) -> str:
    """
    Executes a string of Python code and returns the output.
    This is the PREFERRED tool for all tasks involving file manipulation, data processing, or complex logic.
    """
    try:
        result = subprocess.run(
            ["python", "-c", code], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
        )
        return _format_shell_output(result)
    except Exception as e:
        return f"## Python Execution FAILED\nError: An unexpected error occurred: {str(e)}"

def shell(command: str) -> str:
    """
    Executes a shell command. Use this ONLY for tasks that CANNOT be done with Python.
    """
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
        )
        return _format_shell_output(result)
    except Exception as e:
        return f"### Shell Command FAILED\nError: An unexpected error occurred: {str(e)}"

def get_cache_info(cache: dict, cache_id: str) -> str:
    """
    Returns total lines, total chars, and a preview for a given cache entry.
    """
    if cache_id not in cache:
        return f"## Error\nCache ID '{cache_id}' not found."
    full_output = cache[cache_id]
    total_chars = len(full_output)
    lines = full_output.splitlines()
    total_lines = len(lines)
    head = "\n".join(lines[:10])
    return (
        f"## Cache Info (ID: {cache_id})\n"
        f"- total_lines: {total_lines}\n"
        f"- total_chars: {total_chars}\n"
        "### Preview (first 10 lines)\n```\n" + head + "\n```"
    )

def drop_cache(cache: dict, cache_id: str) -> str:
    """
    Deletes a cache entry to free memory.
    """
    if cache_id not in cache:
        return f"## Error\nCache ID '{cache_id}' not found."
    del cache[cache_id]
    return f"## OK\nCache '{cache_id}' dropped."

def view_cached_output(
    cache: dict,
    cache_id: str,
    start_line: int = None,
    line_count: int = None,
    before_lines: int = 0,
    after_lines: int = 0,
    start_char: int = None,
    char_count: int = None,
) -> str:
    """
    Retrieve a specific portion of a large tool output that has been cached.
    Supports either line-based or character-based ranges, but not both in one call.

    Modes:
      • Line mode: start_line, line_count[, before_lines, after_lines]
      • Char mode: start_char, char_count
    """
    if cache_id not in cache:
        return f"## Error\nCache ID '{cache_id}' not found."

    full_output = cache[cache_id]

    # --- Enforce exclusive mode selection
    line_params_present = (start_line is not None) or (line_count is not None) or (before_lines not in (None, 0)) or (after_lines not in (None, 0))
    char_params_present = (start_char is not None) or (char_count is not None)
    if line_params_present and char_params_present:
        return (
            "## Error\n"
            "You provided both line-based and character-based parameters. "
            "Choose exactly one mode: either (start_line, line_count[, before_lines, after_lines]) "
            "or (start_char, char_count)."
        )

    # --- Character-based mode ---
    if char_params_present:
        if start_char is None or char_count is None:
            return "## Error\nChar mode requires both `start_char` and `char_count`."
        if start_char < 0:
            return "## Error\n`start_char` must be >= 0."
        total_chars = len(full_output)
        if start_char >= total_chars:
            return (
                "## Error\n"
                f"`start_char` out of bounds. Valid range: [0, {total_chars - 1}] "
                f"(the output has {total_chars} characters, 0-indexed)."
            )
        if char_count <= 0:
            return "## Error\n`char_count` must be > 0."
        end_char = min(start_char + char_count, total_chars)
        chunk = full_output[start_char:end_char]
        return (
            f"## Viewing Cached Output (ID: {cache_id})\n"
            f"Showing characters {start_char} to {end_char - 1} of {total_chars - 1}.\n"
            "```\n"
            + chunk
            + "\n```"
        )

    # --- Line-based mode (default) ---
    if line_count is None:
        line_count = 100
    if start_line is None:
        start_line = 0
    if line_count <= 0:
        return "## Error\n`line_count` must be > 0."
    if before_lines is None or before_lines < 0:
        return "## Error\n`before_lines` must be >= 0."
    if after_lines is None or after_lines < 0:
        return "## Error\n`after_lines` must be >= 0."

    lines = full_output.splitlines()
    total_lines = len(lines)

    if start_line < 0 or start_line >= total_lines:
        suggestion = max(0, min(start_line, total_lines - 1))
        return (
            "## Error\n"
            f"`start_line` out of bounds. Valid range: [0, {total_lines - 1}]. "
            f"The output has {total_lines} lines (0-indexed). "
            f"Try `start_line={suggestion}`."
        )

    # Expand window with before/after context (bounded to file)
    start_idx = max(0, start_line - (before_lines or 0))
    end_idx = min(total_lines, (start_line + line_count) + (after_lines or 0))
    selected_lines = lines[start_idx:end_idx]
    chunk_str = "\n".join(selected_lines)

    # Only apply line-aware truncation in line mode (char mode is returned raw)
    truncated_chunk = _truncate_output(chunk_str, max_lines=(end_idx - start_idx), max_line_length=MAX_LINE_LENGTH)

    return (
        f"## Viewing Cached Output (ID: {cache_id})\n"
        f"Showing lines {start_idx} to {end_idx - 1} of {total_lines - 1}.\n"
        f"(Requested: start_line={start_line}, line_count={line_count}, before_lines={before_lines}, after_lines={after_lines})\n"
        "```\n"
        + truncated_chunk
        + "\n```"
    )

# --- Tool Registration ---

AVAILABLE_TOOLS = {
    "python": python,
    "shell": shell,
    "view_cached_output": view_cached_output,
    "get_cache_info": get_cache_info,
    "drop_cache": drop_cache,
}
