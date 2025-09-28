import subprocess
import sys
import uuid
from typing import Dict, Callable, Optional

# --- Constants for Truncation & Caching ---

MAX_TOOL_OUTPUT_LINES = 25
MAX_LINE_LENGTH = 150
MAX_TOOL_OUTPUT_CHARS = 5000  # Cache full output if it exceeds this many characters

# --- Helper Functions ---

def _truncate_output(output: str, max_lines: int, max_line_length: int) -> str:
    """
    Truncates a string for clean display by limiting both the number of lines
    and the length of each individual line.
    """
    lines = output.splitlines()
    original_line_count = len(lines)

    truncation_message = ""
    if original_line_count > max_lines:
        omitted_lines = original_line_count - max_lines
        lines = lines[:max_lines]
        truncation_message = f"\n... (output truncated, {omitted_lines} more lines hidden) ..."

    processed_lines = []
    for line in lines:
        if len(line) > max_line_length:
            processed_lines.append(line[:max_line_length] + " ... (line truncated) ...")
        else:
            processed_lines.append(line)

    return "\n".join(processed_lines) + truncation_message


def _format_shell_output(
    result: subprocess.CompletedProcess,
    language: str,
    apply_truncation: bool = True,
) -> str:
    """
    Formats the result of a subprocess command into a structured Markdown string.
    """
    stdout = result.stdout if result.stdout is not None else ""
    stderr = result.stderr if result.stderr is not None else ""

    stdout_display = stdout.rstrip("\n")
    stderr_display = stderr.rstrip("\n")

    if apply_truncation:
        if stdout_display:
            stdout_display = _truncate_output(stdout_display, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH)
        if stderr_display:
            stderr_display = _truncate_output(stderr_display, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH)

    ok = (result.returncode == 0)
    header = "## Command Successful\n" if ok else f"## Command FAILED (Exit Code: {result.returncode})\n"

    sections = [header]
    if ok:
        if stdout_display:
            sections.append(f"### STDOUT\n```{language}\n{stdout_display}\n```\n")
        if stderr_display:
            sections.append(f"### STDERR\n```text\n{stderr_display}\n```\n")
        if not stdout_display and not stderr_display:
            sections.append("The command produced no output.\n")
    else:
        if stderr_display:
            sections.append(f"### STDERR\n```text\n{stderr_display}\n```\n")
        if stdout_display:
            sections.append(f"### STDOUT\n```{language}\n{stdout_display}\n```\n")
        if not stdout_display and not stderr_display:
            sections.append("The command produced no output.\n")

    return "".join(sections)


def _compose_cache_payload(stdout: str, stderr: str, returncode: int) -> str:
    """
    Compose a plain-text payload for caching (no markdown fences), suitable for
    line/char slicing in the cache viewer.
    """
    parts = [f"[exit_code] {returncode}"]
    if stdout:
        parts.append("[stdout]\n" + stdout)
    if stderr:
        parts.append("[stderr]\n" + stderr)
    return "\n\n".join(parts)

# --- Tool Executor Class ---

class ToolExecutor:
    """
    Manages available tools and a shared output cache.
    """
    def __init__(self):
        self.cache: Dict[str, str] = {}
        self._available_tools: Dict[str, Callable[..., str]] = {
            "python": self.python,
            "shell": self.shell,
            "cache": self.cache_tool,   # unified cache tool
        }

    def execute_tool(self, tool_name: str, **kwargs) -> str:
        """Finds and executes the correct tool method."""
        method = self._available_tools.get(tool_name)
        if method is None:
            return f"## Error\nTool `{tool_name}` not found."
        try:
            return method(**kwargs)
        except TypeError as te:
            return f"## Error\nInvalid arguments for `{tool_name}`: {te}"

    # --- Core Tools ---

    def python(self, code: str, timeout: int = 30) -> str:
        """
        Executes a string of Python code and returns the formatted output.
        If the combined raw output is large, caches the full payload and shows a truncated preview.
        """
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except Exception as e:
            return f"## Python Execution FAILED\n```text\nAn unexpected error occurred: {str(e)}\n```"

        return self._finalize_with_optional_cache(result, language="python")

    def shell(self, command: str, timeout: int = 30) -> str:
        """
        Executes a shell command and returns the formatted output.
        If the combined raw output is large, caches the full payload and shows a truncated preview.
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except Exception as e:
            return f"## Shell Command FAILED\n```text\nAn unexpected error occurred: {str(e)}\n```"

        return self._finalize_with_optional_cache(result, language="bash")

    # --- Cache-aware finalization ---

    def _finalize_with_optional_cache(self, result: subprocess.CompletedProcess, language: str) -> str:
        """
        Build a user-facing markdown response, and if the raw output is large,
        store the full plain-text payload in cache and append viewing instructions.
        """
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        raw_payload = _compose_cache_payload(stdout, stderr, result.returncode)

        display_md = _format_shell_output(result, language=language, apply_truncation=True)

        if len(raw_payload) > MAX_TOOL_OUTPUT_CHARS:
            cache_id = str(uuid.uuid4())
            self.cache[cache_id] = raw_payload
            display_md += (
                "\n---\n"
                f"### Output cached (ID: `{cache_id}`)\n"
                f"- Lines: `cache(action='view', cache_id='{cache_id}', mode='lines', start=0, count=200)`\n"
                f"- Chars: `cache(action='view', cache_id='{cache_id}', mode='chars', start=0, count=5000)`\n"
                f"- Info:  `cache(action='info', cache_id='{cache_id}')`\n"
                f"- Drop:  `cache(action='drop', cache_id='{cache_id}')`\n"
            )
        return display_md

    # --- Cache Utilities ---

    def _cache_info_text(self, cache_id: str) -> str:
        full_output = self.cache[cache_id]
        total_chars = len(full_output)
        lines = full_output.splitlines()
        total_lines = len(lines)
        head = "\n".join(lines[:10])
        return (
            f"## Cache Info (ID: `{cache_id}`)\n"
            f"- `total_lines`: {total_lines}\n"
            f"- `total_chars`: {total_chars}\n"
            f"### Preview (first 10 lines)\n```text\n{head}\n```"
        )

    # Unified cache tool (no legacy params)
    def cache_tool(
        self,
        *,
        action: str,             # "view" | "info" | "drop"
        cache_id: str,
        # Viewing params (for action='view'):
        mode: Optional[str] = "lines",  # "lines" | "chars" | "info"
        start: Optional[int] = None,    # required for mode in {"lines","chars"} unless using page/page_size in lines
        count: Optional[int] = None,    # required for mode in {"lines","chars"} unless using page/page_size in lines
        context_before: int = 0,        # lines mode only
        context_after: int = 0,         # lines mode only
        page: Optional[int] = None,     # lines mode only
        page_size: Optional[int] = None # lines mode only
    ) -> str:
        """
        Unified cache tool.

        - action='info' -> show size & preview
        - action='drop' -> delete cache entry
        - action='view' -> render a slice:
              • mode='lines': start/count OR page/page_size (with optional context_before/context_after)
              • mode='chars': start/count
              • mode='info' : same as action='info'
        """
        if cache_id not in self.cache:
            return f"## Error\nCache ID `{cache_id}` not found."

        if action not in ("view", "info", "drop"):
            return "## Error\n`action` must be one of: 'view', 'info', 'drop'."

        if action == "drop":
            del self.cache[cache_id]
            return f"## OK\nCache `{cache_id}` dropped."

        if action == "info":
            return self._cache_info_text(cache_id)

        # action == "view"
        m = (mode or "lines").lower()
        if m == "info":
            return self._cache_info_text(cache_id)

        full_output = self.cache[cache_id]

        if m == "chars":
            if start is None or count is None:
                return "## Error\nChars mode requires `start` and `count`."
            if start < 0 or count <= 0:
                return "## Error\n`start` must be >= 0 and `count` must be > 0."
            total_chars = len(full_output)
            if start >= total_chars:
                return f"## Error\n`start` out of bounds. Valid range: [0, {total_chars - 1}]."
            end_char = min(start + count, total_chars)
            chunk = full_output[start:end_char]
            return (
                f"## Viewing Cached Output (ID: `{cache_id}`)\n"
                f"Showing characters `{start}` to `{end_char - 1}` of `{total_chars - 1}`.\n"
                f"```text\n{chunk}\n```"
            )

        if m == "lines":
            lines = full_output.splitlines()
            total_lines = len(lines)
            if total_lines == 0:
                return f"## Viewing Cached Output (ID: `{cache_id}`)\nCache is empty.\n"

            # Determine window
            if page is not None or page_size is not None:
                if page is None or page_size is None:
                    return "## Error\nLines mode paging requires both `page` and `page_size`."
                if page < 0 or page_size <= 0:
                    return "## Error\n`page` must be >= 0 and `page_size` must be > 0."
                s_line = page * page_size
                c_line = page_size
            else:
                if start is None or count is None:
                    return "## Error\nLines mode requires `start` and `count` (or `page` and `page_size`)."
                if start < 0 or count <= 0:
                    return "## Error\n`start` must be >= 0 and `count` must be > 0."
                s_line = start
                c_line = count

            if s_line >= total_lines:
                return f"## Error\n`start` out of bounds. Valid line range: [0, {total_lines - 1}]."

            before = max(0, context_before)
            after = max(0, context_after)

            start_idx = max(0, s_line - before)
            end_idx = min(total_lines, (s_line + c_line) + after)
            selected_lines = lines[start_idx:end_idx]
            chunk_str = "\n".join(selected_lines)

            return (
                f"## Viewing Cached Output (ID: `{cache_id}`)\n"
                f"Showing lines `{start_idx}` to `{end_idx - 1}` of `{total_lines - 1}`.\n"
                f"```text\n{chunk_str}\n```"
            )

        return "## Error\nInvalid `mode`. Use 'lines', 'chars', or 'info'."
