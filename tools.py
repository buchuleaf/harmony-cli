# tools.py

import subprocess
import json

# --- Constants for Truncation ---
MAX_TOOL_OUTPUT_LINES = 25
MAX_LINE_LENGTH = 150
MAX_TOOL_OUTPUT_CHARS = 5000 # Max chars before output is cached

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
        if result.stdout: output += f"### STDOUT\n```\n{result.stdout.strip()}\n```\n"
        if result.stderr: output += f"### STDERR\n```\n{result.stderr.strip()}\n```\n"
        if not result.stdout and not result.stderr: output += "The command produced no output.\n"
    else:
        output += f"## Command FAILED (Exit Code: {result.returncode})\n"
        if result.stderr: output += f"### STDERR\n```\n{result.stderr.strip()}\n```\n"
        if result.stdout: output += f"### STDOUT\n```\n{result.stdout.strip()}\n```\n"
        if not result.stdout and not result.stderr: output += "The command produced no output.\n"
    return output

# --- Tool Executor Class ---

class ToolExecutor:
    """
    A class to manage the available tools and the shared output cache.
    This solves the problem of tools needing access to a shared state (the cache).
    """
    def __init__(self):
        self.cache = {}
        self._available_tools = {
            "python": self.python,
            "shell": self.shell,
            "view_cached_output": self.view_cached_output,
            "get_cache_info": self.get_cache_info,
            "drop_cache": self.drop_cache,
        }

    def execute_tool(self, tool_name: str, **kwargs):
        """Finds and executes the correct tool method."""
        if tool_name not in self._available_tools:
            return f"## Error\nTool '{tool_name}' not found."
        tool_method = self._available_tools[tool_name]
        return tool_method(**kwargs)

    # --- Core Tools (now methods of the class) ---

    def python(self, code: str) -> str:
        """
        Executes a string of Python code and returns the output.
        """
        try:
            result = subprocess.run(
                ["python", "-c", code], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
            )
            return _format_shell_output(result)
        except Exception as e:
            return f"## Python Execution FAILED\nError: An unexpected error occurred: {str(e)}"

    def shell(self, command: str) -> str:
        """
        Executes a shell command.
        """
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
            )
            return _format_shell_output(result)
        except Exception as e:
            return f"### Shell Command FAILED\nError: An unexpected error occurred: {str(e)}"

    def get_cache_info(self, cache_id: str) -> str:
        """
        Returns total lines, total chars, and a preview for a given cache entry.
        """
        if cache_id not in self.cache:
            return f"## Error\nCache ID '{cache_id}' not found."
        full_output = self.cache[cache_id]
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

    def drop_cache(self, cache_id: str) -> str:
        """
        Deletes a cache entry to free memory.
        """
        if cache_id not in self.cache:
            return f"## Error\nCache ID '{cache_id}' not found."
        del self.cache[cache_id]
        return f"## OK\nCache '{cache_id}' dropped."

    def view_cached_output(
        self,
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
        """
        if cache_id not in self.cache:
            return f"## Error\nCache ID '{cache_id}' not found."

        full_output = self.cache[cache_id]

        line_params_present = (start_line is not None) or (line_count is not None) or (before_lines > 0) or (after_lines > 0)
        char_params_present = (start_char is not None) or (char_count is not None)
        if line_params_present and char_params_present:
            return "## Error\nYou provided both line-based and character-based parameters. Choose exactly one mode."

        if char_params_present:
            if start_char is None or char_count is None: return "## Error\nChar mode requires both `start_char` and `char_count`."
            if start_char < 0: return "## Error\n`start_char` must be >= 0."
            total_chars = len(full_output)
            if start_char >= total_chars: return f"## Error\n`start_char` out of bounds. Valid range: [0, {total_chars - 1}]."
            if char_count <= 0: return "## Error\n`char_count` must be > 0."
            end_char = min(start_char + char_count, total_chars)
            chunk = full_output[start_char:end_char]
            return (f"## Viewing Cached Output (ID: {cache_id})\n"
                    f"Showing characters {start_char} to {end_char - 1} of {total_chars - 1}.\n"
                    "```\n" + chunk + "\n```")

        # Line-based mode is the default
        line_count = line_count if line_count is not None else 100
        start_line = start_line if start_line is not None else 0
        if line_count <= 0: return "## Error\n`line_count` must be > 0."
        if before_lines < 0: return "## Error\n`before_lines` must be >= 0."
        if after_lines < 0: return "## Error\n`after_lines` must be >= 0."

        lines = full_output.splitlines()
        total_lines = len(lines)
        if start_line < 0 or start_line >= total_lines:
            return f"## Error\n`start_line` out of bounds. Valid range: [0, {total_lines - 1}]."

        start_idx = max(0, start_line - before_lines)
        end_idx = min(total_lines, (start_line + line_count) + after_lines)
        selected_lines = lines[start_idx:end_idx]
        chunk_str = "\n".join(selected_lines)
        
        return (f"## Viewing Cached Output (ID: {cache_id})\n"
                f"Showing lines {start_idx} to {end_idx - 1} of {total_lines - 1}.\n"
                f"```\n" + chunk_str + "\n```")