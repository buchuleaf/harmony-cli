import os
import sys
import uuid
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Callable, Optional, List

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


def _format_exec_output(
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


def _safe_rel_path(p: str) -> Path:
    """
    Enforce relative, safe paths (no absolute paths, no parent traversal).
    """
    if os.name == "nt" and (":" in p or p.startswith("\\") or p.startswith("/")):
        raise ValueError("Absolute paths are not allowed.")
    if p.startswith("/") or p.startswith("./../") or p.startswith("../") or ".." in Path(p).parts:
        raise ValueError("Parent traversal or absolute paths are not allowed.")
    return Path(p).resolve().relative_to(Path.cwd().resolve())


# --- Tool Executor Class ---

class ToolExecutor:
    """
    Manages available tools, a shared output cache, and patch application.
    """
    def __init__(self):
        self.cache: Dict[str, str] = {}
        self._available_tools: Dict[str, Callable[..., str]] = {
            "run": self.run,           # unified python/shell
            "cache": self.cache_tool,  # view/info/drop cache
            "apply_patch": self.apply_patch,
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
        except Exception as e:
            return f"## Error\n{type(e).__name__}: {e}"

    # --- Unified Executor ---

    def run(self, kind: str, code: str, timeout: int = 30) -> str:
        """
        Execute either Python or Shell based on `kind`.
        kind: "python" | "shell"
        """
        kind = (kind or "").lower()
        if kind not in ("python", "shell"):
            return "## Error\n`kind` must be 'python' or 'shell'."

        try:
            if kind == "python":
                result = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    stdin=subprocess.DEVNULL,
                )
                return self._finalize_with_optional_cache(result, language="python")
            else:
                result = subprocess.run(
                    code,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    stdin=subprocess.DEVNULL,
                )
                return self._finalize_with_optional_cache(result, language="bash")
        except subprocess.TimeoutExpired as te:
            return f"## Error\nExecution timed out after {timeout}s.\n```text\n{te}\n```"
        except Exception as e:
            return f"## Error\nExecution failed: {e}"

    # --- Cache-aware finalization ---

    def _finalize_with_optional_cache(self, result: subprocess.CompletedProcess, language: str) -> str:
        """
        Build a user-facing markdown response, and if the raw output is large,
        store the full plain-text payload in cache and append viewing instructions.
        """
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        raw_payload = _compose_cache_payload(stdout, stderr, result.returncode)

        display_md = _format_exec_output(result, language=language, apply_truncation=True)

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

    # --- Apply Patch ---

    def apply_patch(self, patch: str) -> str:
        """
        Apply a stripped-down, file-oriented diff format safely.

        See developer tool description for exact grammar and rules.
        """
        if not isinstance(patch, str) or not patch.strip():
            return "## Error\n`patch` must be a non-empty string."

        lines = patch.splitlines()
        i = 0

        # Validate envelope
        if i >= len(lines) or lines[i].strip() != "*** Begin Patch":
            return "## Error\nPatch must start with '*** Begin Patch'."
        i += 1

        ops_applied: List[str] = []

        def read_until_file_end(idx: int) -> int:
            # For Update File hunks, optional '*** End of File' terminator allowed per grammar.
            return idx

        while i < len(lines):
            line = lines[i].rstrip("\n")
            if line.strip() == "*** End Patch":
                break

            # ADD FILE
            if line.startswith("*** Add File: "):
                path = line[len("*** Add File: "):].strip()
                try:
                    rel = _safe_rel_path(path)
                except Exception as e:
                    return f"## Error\nInvalid Add path '{path}': {e}"

                i += 1
                content_lines = []
                while i < len(lines):
                    l = lines[i]
                    if l.startswith("*** "):  # start of next op or end
                        break
                    if not l.startswith("+"):
                        return f"## Error\nAdd File '{path}' expects lines starting with '+'. Offending line: {l}"
                    content_lines.append(l[1:])
                    i += 1

                # Write file (create parent dirs)
                abs_path = Path.cwd() / rel
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text("\n".join(content_lines) + ("\n" if content_lines and not content_lines[-1].endswith("\n") else ""), encoding="utf-8")
                ops_applied.append(f"Added {rel}")
                continue

            # DELETE FILE
            if line.startswith("*** Delete File: "):
                path = line[len("*** Delete File: "):].strip()
                try:
                    rel = _safe_rel_path(path)
                except Exception as e:
                    return f"## Error\nInvalid Delete path '{path}': {e}"

                abs_path = Path.cwd() / rel
                if abs_path.exists():
                    if abs_path.is_dir():
                        return f"## Error\nDelete File points to a directory: {rel}"
                    abs_path.unlink()
                    ops_applied.append(f"Deleted {rel}")
                else:
                    return f"## Error\nCannot delete non-existent file: {rel}"
                i += 1
                continue

            # UPDATE FILE
            if line.startswith("*** Update File: "):
                path = line[len("*** Update File: "):].strip()
                try:
                    rel = _safe_rel_path(path)
                except Exception as e:
                    return f"## Error\nInvalid Update path '{path}': {e}"

                abs_path = Path.cwd() / rel
                if not abs_path.exists() or abs_path.is_dir():
                    return f"## Error\nUpdate target does not exist or is a directory: {rel}"

                i += 1
                # Optional Move to
                move_to: Optional[Path] = None
                if i < len(lines) and lines[i].startswith("*** Move to: "):
                    newp = lines[i][len("*** Move to: "):].strip()
                    try:
                        move_to = _safe_rel_path(newp)
                    except Exception as e:
                        return f"## Error\nInvalid Move to path '{newp}': {e}"
                    i += 1

                # Collect hunks
                file_text = abs_path.read_text(encoding="utf-8")
                file_lines = file_text.splitlines()

                def find_subseq(hay: List[str], needle: List[str]) -> int:
                    """Return start index of consecutive subsequence 'needle' in 'hay', or -1."""
                    if not needle:
                        return 0
                    for s in range(0, len(hay) - len(needle) + 1):
                        if hay[s:s+len(needle)] == needle:
                            return s
                    return -1

                changed = False
                while i < len(lines) and lines[i].startswith("@@"):
                    # Skip @@ header line (optional)
                    i += 1
                    before_seq: List[str] = []
                    after_seq:  List[str] = []
                    while i < len(lines) and not lines[i].startswith("*** ") and not lines[i].startswith("@@"):
                        hl = lines[i]
                        if not hl:
                            # preserve empty line context
                            before_seq.append("")
                            after_seq.append("")
                            i += 1
                            continue

                        prefix = hl[0]
                        content = hl[1:] if len(hl) > 0 else ""
                        if prefix == " ":
                            before_seq.append(content)
                            after_seq.append(content)
                        elif prefix == "-":
                            before_seq.append(content)
                        elif prefix == "+":
                            after_seq.append(content)
                        else:
                            return f"## Error\nInvalid hunk line prefix '{prefix}' in Update for '{rel}'."
                        i += 1

                    # Apply hunk
                    start_idx = find_subseq(file_lines, before_seq)
                    if start_idx == -1:
                        return (
                            "## Error\nFailed to locate hunk in target file.\n"
                            f"File: {rel}\n"
                            "Tip: Increase unique context in the hunk (use @@ headers and 3 lines of context)."
                        )
                    end_idx = start_idx + len(before_seq)
                    file_lines = file_lines[:start_idx] + after_seq + file_lines[end_idx:]
                    changed = True

                    # Optional "*** End of File" terminator (ignore if present)
                    if i < len(lines) and lines[i].strip() == "*** End of File":
                        i += 1

                if not changed:
                    return f"## Error\nNo hunks provided for Update File: {rel}"

                # Write back
                final_text = "\n".join(file_lines)
                abs_path.write_text(final_text + ("" if final_text.endswith("\n") or final_text == "" else "\n"), encoding="utf-8")

                # Move if requested
                if move_to:
                    new_abs = Path.cwd() / move_to
                    new_abs.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(abs_path), str(new_abs))
                    ops_applied.append(f"Updated {rel} -> moved to {move_to}")
                else:
                    ops_applied.append(f"Updated {rel}")
                continue

            # Unknown line
            return f"## Error\nUnrecognized patch directive: {line}"

        if i >= len(lines) or lines[i].strip() != "*** End Patch":
            return "## Error\nPatch must end with '*** End Patch'."

        if not ops_applied:
            return "## Error\nPatch contained no operations."

        # Summary
        bullets = "\n".join(f"- {op}" for op in ops_applied)
        return f"## Patch Applied\n{bullets}"
