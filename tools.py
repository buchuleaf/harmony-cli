# tools.py

import subprocess
import json
import os

# --- Constants for Truncation ---
# These are now defined here as the truncation logic lives here.
MAX_TOOL_OUTPUT_LINES = 15
MAX_LINE_LENGTH = 150

# --- Helper Functions ---

def _truncate_output(output: str, max_lines: int, max_line_length: int) -> str:
    """
    Truncates a string for clean display by limiting both the number of lines
    and the length of each individual line. (Moved from main.py)
    """
    lines = output.splitlines()
    original_line_count = len(lines)

    # Step 1: Truncate the number of lines
    if original_line_count > max_lines:
        lines = lines[:max_lines]
        omitted_lines = original_line_count - max_lines
        truncation_message = f"\n... (output truncated, {omitted_lines} more lines hidden) ..."
    else:
        truncation_message = ""

    # Step 2: Truncate the length of each remaining line
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

def view_cached_output(cache: dict, cache_id: str, start_line: int = 0, line_count: int = 100) -> str:
    """
    Retrieves a specific portion of a large tool output that has been cached.
    """
    if cache_id not in cache:
        return f"## Error\nCache ID '{cache_id}' not found."

    full_output = cache[cache_id]
    lines = full_output.splitlines()
    total_lines = len(lines)

    if start_line < 0 or start_line >= total_lines:
        return f"## Error\n`start_line` is out of bounds. The output has {total_lines} lines (0-indexed)."

    end_line = min(start_line + line_count, total_lines)
    selected_lines = lines[start_line:end_line]
    
    # --- CRITICAL FIX ---
    # We now truncate the CHUNK before returning it, protecting against long lines within the chunk.
    # We use the requested line_count as the max_lines for the truncator.
    chunk_str = "\n".join(selected_lines)
    truncated_chunk = _truncate_output(chunk_str, max_lines=line_count, max_line_length=MAX_LINE_LENGTH)

    result_str = (
        f"## Viewing Cached Output (ID: {cache_id})\n"
        f"Showing lines {start_line} to {end_line - 1} of {total_lines - 1}.\n"
        "```\n"
        + truncated_chunk
        + "\n```"
    )
    return result_str

# --- Tool Registration ---

AVAILABLE_TOOLS = {
    "python": python,
    "shell": shell,
    "view_cached_output": view_cached_output,
}