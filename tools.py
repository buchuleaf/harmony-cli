import subprocess
import sys
import uuid
from typing import Dict, Callable, Optional, Tuple

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

    Note: We use rstrip('\\n') to preserve indentation while removing only trailing newlines.
    """
    # Safely coerce to strings (CompletedProcess may give empty strings already)
    stdout = result.stdout if result.stdout is not None else ""
    stderr = result.stderr if result.stderr is not None else ""

    # Preserve indentation (avoid .strip()) but drop trailing newlines so code fences render nicely.
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
    line/char slicing in view_cached_output.
    """
    parts = [f"[exit_code] {returncode}"]
    if stdout:
        parts.append("[stdout]\n" + stdout)
    if stderr:
        parts.append("[stderr]\n" + stderr)
    return "\n\n".join(parts)

# ---- Normalization helpers (for backwards compatibility) ----

def _normalize_view_args(
    *,
    # New API
    mode: Optional[str],
    start: Optional[int],
    count: Optional[int],
    context_before: int,
    context_after: int,
    page: Optional[int],
    page_size: Optional[int],
    # Legacy API (accepted but discouraged)
    start_line: Optional[int],
    line_count: Optional[int],
    before_lines: int,
    after_lines: int,
    start_char: Optional[int],
    char_count: Optional[int],
) -> Tuple[str, dict, Optional[str]]:
    """
    Returns: (mode, params_dict, error_message)
    - mode: "lines" | "chars" | "info"
    - params_dict: dict of normalized params for the chosen mode
    - error_message: optional error string
    """
    # If legacy char-mode params are present, prefer chars
    legacy_char_present = (start_char is not None) or (char_count is not None)
    legacy_line_present = (start_line is not None) or (line_count is not None) or before_lines > 0 or after_lines > 0

    # If user didn't specify mode but used legacy params, infer it
    inferred_mode = None
    if mode is None:
        if legacy_char_present and legacy_line_present:
            return ("", {}, "You provided both line-based and char-based legacy parameters. Choose one.")
        if legacy_char_present:
            inferred_mode = "chars"
        elif legacy_line_present:
            inferred_mode = "lines"

    final_mode = (mode or inferred_mode or "lines").lower()
    if final_mode not in ("lines", "chars", "info"):
        return ("", {}, "Invalid `mode`. Use 'lines', 'chars', or 'info'.")

    if final_mode == "info":
        return ("info", {}, None)

    # Build normalized params
    if final_mode == "chars":
        # Prefer new API; fallback to legacy
        n_start = start if start is not None else start_char
        n_count = count if count is not None else char_count
        if n_start is None or n_count is None:
            return ("", {}, "Char mode requires `start` and `count` (or legacy `start_char` and `char_count`).")
        if n_start < 0 or n_count <= 0:
            return ("", {}, "`start` must be >= 0 and `count` must be > 0 for char mode.")
        return ("chars", {"start": n_start, "count": n_count}, None)

    # lines mode
    # If paging provided, compute start/count from page/page_size (page wins over start/count)
    if page is not None or page_size is not None:
        if page is None or page_size is None:
            return ("", {}, "Lines mode paging requires both `page` and `page_size`.")
        if page < 0 or page_size <= 0:
            return ("", {}, "`page` must be >= 0 and `page_size` must be > 0.")
        n_start = page * page_size
        n_count = page_size
    else:
        # Prefer new API; fallback to legacy
        n_start = start if start is not None else (start_line if start_line is not None else 0)
        n_count = count if count is not None else (line_count if line_count is not None else 100)

    n_before = context_before if context_before is not None else before_lines
    n_after = context_after if context_after is not None else after_lines

    if n_start < 0 or n_count <= 0:
        return ("", {}, "`start` must be >= 0 and `count` must be > 0 for lines mode.")
    if n_before < 0 or n_after < 0:
        return ("", {}, "`context_before` and `context_after` must be >= 0.")

    return ("lines", {"start": n_start, "count": n_count, "context_before": n_before, "context_after": n_after}, None)

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
            "view_cached_output": self.view_cached_output,
            "get_cache_info": self.get_cache_info,   # Deprecated alias
            "drop_cache": self.drop_cache,
        }

    def execute_tool(self, tool_name: str, **kwargs) -> str:
        """Finds and executes the correct tool method."""
        method = self._available_tools.get(tool_name)
        if method is None:
            return f"## Error\nTool `{tool_name}` not found."
        try:
            return method(**kwargs)
        except TypeError as te:
            # Helpful message when wrong kwargs are passed
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

        # Always present a tidy, truncated view for the console
        display_md = _format_shell_output(result, language=language, apply_truncation=True)

        if len(raw_payload) > MAX_TOOL_OUTPUT_CHARS:
            cache_id = str(uuid.uuid4())
            self.cache[cache_id] = raw_payload
            # Append cache instructions — short, consistent, simple.
            display_md += (
                "\n---\n"
                f"### Output cached (ID: `{cache_id}`)\n"
                f"- Use **view_cached_output** for slices:\n"
                f"  - Lines: `view_cached_output(cache_id='{cache_id}', mode='lines', start=0, count=200)`\n"
                f"  - Chars: `view_cached_output(cache_id='{cache_id}', mode='chars', start=0, count=5000)`\n"
                f"  - Info:  `view_cached_output(cache_id='{cache_id}', mode='info')`\n"
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

    def get_cache_info(self, cache_id: str) -> str:
        """
        (Deprecated) Use view_cached_output(mode='info') instead.
        """
        if cache_id not in self.cache:
            return f"## Error\nCache ID `{cache_id}` not found."
        return self._cache_info_text(cache_id)

    def drop_cache(self, cache_id: str) -> str:
        """
        Deletes a cache entry to free memory.
        """
        if cache_id not in self.cache:
            return f"## Error\nCache ID `{cache_id}` not found."
        del self.cache[cache_id]
        return f"## OK\nCache `{cache_id}` dropped."

    def view_cached_output(
        self,
        cache_id: str,
        # New simple API:
        mode: Optional[str] = None,      # "lines" | "chars" | "info"
        start: Optional[int] = None,     # default varies by mode
        count: Optional[int] = None,     # default varies by mode
        context_before: int = 0,
        context_after: int = 0,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        # Legacy (still accepted):
        start_line: Optional[int] = None,
        line_count: Optional[int] = None,
        before_lines: int = 0,
        after_lines: int = 0,
        start_char: Optional[int] = None,
        char_count: Optional[int] = None,
    ) -> str:
        """
        Simple cache viewer.
          • mode="info": show counts + first 10 lines.
          • mode="lines": show a window of lines; use start/count OR page/page_size (with optional context_before/context_after).
          • mode="chars": show a window of characters with start/count.

        Backwards compatible: legacy parameters are still accepted and normalized.
        """
        if cache_id not in self.cache:
            return f"## Error\nCache ID `{cache_id}` not found."

        full_output = self.cache[cache_id]

        # Defaults for new API if not specified
        if mode is None and all(x is None for x in (start, count, page, page_size, start_char, char_count, start_line, line_count)) \
           and context_before == 0 and context_after == 0 and before_lines == 0 and after_lines == 0:
            # Totally empty -> show info
            mode = "info"

        m, params, err = _normalize_view_args(
            mode=mode,
            start=start,
            count=count,
            context_before=context_before,
            context_after=context_after,
            page=page,
            page_size=page_size,
            start_line=start_line,
            line_count=line_count,
            before_lines=before_lines,
            after_lines=after_lines,
            start_char=start_char,
            char_count=char_count,
        )
        if err:
            return f"## Error\n{err}"

        if m == "info":
            return self._cache_info_text(cache_id)

        if m == "chars":
            total_chars = len(full_output)
            s = params["start"]
            c = params["count"]
            if s >= total_chars:
                return f"## Error\n`start` out of bounds. Valid range: [0, {total_chars - 1}]."
            end_char = min(s + c, total_chars)
            chunk = full_output[s:end_char]
            return (
                f"## Viewing Cached Output (ID: `{cache_id}`)\n"
                f"Showing characters `{s}` to `{end_char - 1}` of `{total_chars - 1}`.\n"
                f"```text\n{chunk}\n```"
            )

        # lines
        lines = full_output.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return (
                f"## Viewing Cached Output (ID: `{cache_id}`)\n"
                "Cache is empty.\n"
            )

        s_line = params["start"]
        c_line = params["count"]
        before = params.get("context_before", 0)
        after = params.get("context_after", 0)

        if s_line >= total_lines:
            return f"## Error\n`start` out of bounds. Valid line range: [0, {total_lines - 1}]."

        start_idx = max(0, s_line - before)
        end_idx = min(total_lines, (s_line + c_line) + after)
        selected_lines = lines[start_idx:end_idx]
        chunk_str = "\n".join(selected_lines)

        return (
            f"## Viewing Cached Output (ID: `{cache_id}`)\n"
            f"Showing lines `{start_idx}` to `{end_idx - 1}` of `{total_lines - 1}`.\n"
            f"```text\n{chunk_str}\n```"
        )
