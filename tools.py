import os
import sys
import uuid
import json
import shutil
import difflib
import subprocess
import re
from pathlib import Path
from typing import Dict, Callable, Optional, List, Tuple

# --- Constants for Truncation & Caching ---

MAX_TOOL_OUTPUT_LINES = 25
MAX_LINE_LENGTH = 150
MAX_TOOL_OUTPUT_CHARS = 5000  # Pagination threshold (chars)

# Pagination defaults/limits
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000

# Max unified-diff lines per file preview (to avoid flooding the console)
MAX_DIFF_LINES_PER_FILE = 300

# --- Markdown / Highlighting helpers ---

_ALLOWED_LEXERS = {"python", "bash", "diff", "json", "text"}

def _normalize_lexer(language: Optional[str]) -> str:
    """
    Use a tiny, consistent set of lexers to keep highlighting stable across outputs.
    Fallback to 'text' if unknown/empty.
    """
    if not language:
        return "text"
    lang = language.strip().lower()
    # common aliases
    if lang in {"sh", "shell"}:
        lang = "bash"
    if lang in {"plaintext", "plain", "txt"}:
        lang = "text"
    if lang not in _ALLOWED_LEXERS:
        return "text"
    return lang

def _md_codeblock(body: str, language: Optional[str] = "") -> str:
    """
    Produce a robust fenced code block that won't break even if `body` contains backticks.
    Chooses a fence length longer than any backtick run in the body.
    Ensures a trailing newline and closing fence are always present.
    Normalizes the language to a small, consistent set.
    """
    if body is None:
        body = ""
    text = body

    # Find the longest run of backticks in the body (3+ to be safe)
    max_ticks = 0
    for m in re.finditer(r"`{3,}", text):
        max_ticks = max(max_ticks, len(m.group(0)))
    fence_len = max(3, max_ticks + 1)
    fence = "`" * fence_len
    lang = _normalize_lexer(language)

    header = f"{fence}{lang}\n"
    if not text.endswith("\n"):
        text = text + "\n"
    return f"{header}{text}{fence}\n"

# --- Helper Functions ---

def _truncate_output(output: str, max_lines: int, max_line_length: int) -> str:
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


def _format_exec_output(result: subprocess.CompletedProcess, language: str) -> str:
    """Format small results with stable, normalized lexers and robust fences."""
    lang = _normalize_lexer(language)
    stdout = result.stdout if result.stdout is not None else ""
    stderr = result.stderr if result.stderr is not None else ""

    stdout_display = stdout.rstrip("\n")
    stderr_display = stderr.rstrip("\n")

    if stdout_display:
        stdout_display = _truncate_output(stdout_display, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH)
    if stderr_display:
        stderr_display = _truncate_output(stderr_display, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH)

    ok = (result.returncode == 0)
    header = "## Command Successful\n" if ok else f"## Command FAILED (Exit Code: {result.returncode})\n"

    sections = [header]
    if ok:
        if stdout_display:
            sections.append("### STDOUT\n")
            sections.append(_md_codeblock(stdout_display, lang))
        if stderr_display:
            sections.append("### STDERR\n")
            sections.append(_md_codeblock(stderr_display, "text"))
        if not stdout_display and not stderr_display:
            sections.append("The command produced no output.\n")
    else:
        if stderr_display:
            sections.append("### STDERR\n")
            sections.append(_md_codeblock(stderr_display, "text"))
        if stdout_display:
            sections.append("### STDOUT\n")
            sections.append(_md_codeblock(stdout_display, lang))
        if not stdout_display and not stderr_display:
            sections.append("The command produced no output.\n")

    return "".join(sections)


def _compose_cache_payload(stdout: str, stderr: str, returncode: int) -> str:
    """
    Compose a plain-text payload for caching (no markdown fences), suitable for paginated viewing.
    We include exit code, stdout and stderr in a single stream of lines.
    """
    parts = [f"[exit_code] {returncode}"]
    if stdout:
        parts.append("[stdout]\n" + stdout.rstrip("\n"))
    if stderr:
        parts.append("[stderr]\n" + stderr.rstrip("\n"))
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


def _diff_and_stats(old_lines: List[str], new_lines: List[str], from_name: str, to_name: str) -> Tuple[str, int, int]:
    old_with_nl = [l + "\n" for l in old_lines]
    new_with_nl = [l + "\n" for l in new_lines]

    diff_iter = difflib.unified_diff(
        old_with_nl, new_with_nl,
        fromfile=from_name, tofile=to_name,
        lineterm="", n=3,
    )
    diff_lines = list(diff_iter)

    added = 0
    removed = 0
    for dl in diff_lines:
        if not dl:
            continue
        if dl.startswith("+") and not (dl.startswith("+++") or dl.startswith("@@")):
            added += 1
        elif dl.startswith("-") and not (dl.startswith("---") or dl.startswith("@@")):
            removed += 1

    if len(diff_lines) > MAX_DIFF_LINES_PER_FILE:
        omitted = len(diff_lines) - MAX_DIFF_LINES_PER_FILE
        diff_lines = diff_lines[:MAX_DIFF_LINES_PER_FILE] + [f"... (diff truncated, {omitted} more lines hidden) ..."]

    diff_text = "\n".join(diff_lines)
    return diff_text, added, removed


# --- Tool Executor Class ---

class ToolExecutor:
    """
    Manages available tools, a shared output cache, and patch application.
    Tools:
      - run(kind="python"|"shell", code, timeout?)
      - cache(action="view"|"info"|"drop", cache_id, page?, page_size?)
      - apply_patch(patch)
    """
    def __init__(self):
        self.cache: Dict[str, str] = {}
        self._available_tools: Dict[str, Callable[..., str]] = {
            "run": self.run,           # unified python/shell
            "cache": self.cache_tool,  # simple pagination
            "apply_patch": self.apply_patch,
        }

    def execute_tool(self, tool_name: str, **kwargs) -> str:
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
            else:
                result = subprocess.run(
                    code,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    stdin=subprocess.DEVNULL,
                )
        except subprocess.TimeoutExpired as te:
            return "## Error\nExecution timed out after {}s.\n".format(timeout) + _md_codeblock(str(te), "text")
        except Exception as e:
            return "## Error\nExecution failed:\n" + _md_codeblock(str(e), "text")

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        raw_payload = _compose_cache_payload(stdout, stderr, result.returncode)

        # Small outputs: render directly with consistent lexers
        if len(raw_payload) <= MAX_TOOL_OUTPUT_CHARS:
            lang = "python" if kind == "python" else "bash"
            return _format_exec_output(result, language=lang)

        # Large outputs: cache + return page 1 with hints
        cache_id = str(uuid.uuid4())
        self.cache[cache_id] = raw_payload

        first_page_md = self._render_cache_page(cache_id, page=0, page_size=DEFAULT_PAGE_SIZE)

        header = "## Command Successful\n" if result.returncode == 0 else f"## Command FAILED (Exit Code: {result.returncode})\n"
        return (
            header +
            f"### Output cached (ID: `{cache_id}`)\n" +
            first_page_md +
            "\n_Use `cache(action='view', cache_id='{cache_id}', page=1)` for the next page, or `cache(action='info', cache_id='{cache_id}')` for totals._\n"
                .replace("{cache_id}", cache_id)
        )

    # --- Cache / Pagination ---

    def _render_cache_page(self, cache_id: str, page: int, page_size: int) -> str:
        full_output = self.cache[cache_id]
        lines = full_output.splitlines()
        total_lines = len(lines)

        page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        total_pages = (total_lines + page_size - 1) // page_size if total_lines else 1

        # Clamp page into range [0, total_pages-1]
        if page < 0:
            page = 0
        if page >= total_pages:
            page = max(0, total_pages - 1)

        start = page * page_size
        end = min(start + page_size, total_lines)
        slice_lines = lines[start:end]
        body = "\n".join(slice_lines)

        display_page = page + 1
        nav = []
        if page > 0:
            prevp = page - 1
            nav.append(f"- Prev: `cache(action='view', cache_id='{cache_id}', page={prevp})`")
        if page < total_pages - 1:
            nextp = page + 1
            nav.append(f"- Next: `cache(action='view', cache_id='{cache_id}', page={nextp})`")

        nav_md = ("\n".join(nav)) if nav else "_(single page)_"

        return (
            f"#### Page {display_page} of {total_pages} (lines {start}–{max(end - 1, start)} of {max(total_lines - 1, 0)})\n"
            + _md_codeblock(body, "text")
            + f"{nav_md}"
        )

    def _cache_info_text(self, cache_id: str) -> str:
        full_output = self.cache[cache_id]
        total_chars = len(full_output)
        total_lines = len(full_output.splitlines())
        total_pages = (total_lines + DEFAULT_PAGE_SIZE - 1) // DEFAULT_PAGE_SIZE if total_lines else 1

        head_preview = "\n".join(full_output.splitlines()[:min(10, total_lines)])
        return (
            f"## Cache Info (ID: `{cache_id}`)\n"
            f"- total_lines: **{total_lines}**\n"
            f"- total_chars: **{total_chars}**\n"
            f"- total_pages (page_size={DEFAULT_PAGE_SIZE}): **{total_pages}**\n"
            f"### Preview\n" + _md_codeblock(head_preview, "text") +
            f"_Use `cache(action='view', cache_id='{cache_id}', page=0, page_size={DEFAULT_PAGE_SIZE})` to start browsing._"
                .replace("{cache_id}", cache_id)
        )

    def cache_tool(
        self,
        *,
        action: str,             # "view" | "info" | "drop"
        cache_id: str,
        page: Optional[int] = 0,
        page_size: Optional[int] = DEFAULT_PAGE_SIZE,
    ) -> str:
        """
        Simple, line-based pagination.

        - action='info' -> show totals & a short preview
        - action='drop' -> delete cache entry
        - action='view' -> show page N (0-based) with optional page_size (<= MAX_PAGE_SIZE)
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
        p = 0 if page is None else int(page)
        ps = DEFAULT_PAGE_SIZE if page_size is None else int(page_size)
        ps = max(1, min(ps, MAX_PAGE_SIZE))
        return self._render_cache_page(cache_id, page=p, page_size=ps)

    # --- Apply Patch (detailed reporting) ---

    def apply_patch(self, patch: str) -> str:
        """
        Apply a stripped-down, file-oriented diff format safely.
        Returns a detailed report with line stats and a unified-diff preview per file.
        """
        if not isinstance(patch, str) or not patch.strip():
            return "## Error\n`patch` must be a non-empty string."

        lines = patch.splitlines()
        i = 0

        # Validate envelope
        if i >= len(lines) or lines[i].strip() != "*** Begin Patch":
            return "## Error\nPatch must start with '*** Begin Patch'."
        i += 1

        results_md: List[str] = []
        summary_ops: List[str] = []

        def _write_file(path: Path, content_lines: List[str]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            text = "\n".join(content_lines)
            if text and not text.endswith("\n"):
                text += "\n"
            path.write_text(text, encoding="utf-8")

        while i < len(lines):
            line = lines[i].rstrip("\n")
            if line.strip() == "*** End Patch":
                break

            # ADD FILE
            if line.startswith("*** Add File: "):
                raw_path = line[len("*** Add File: "):].strip()
                try:
                    rel = _safe_rel_path(raw_path)
                except Exception as e:
                    return f"## Error\nInvalid Add path '{raw_path}': {e}"

                i += 1
                content_lines: List[str] = []
                while i < len(lines):
                    l = lines[i]
                    if l.startswith("*** "):  # next op or end
                        break
                    if not l.startswith("+"):
                        return f"## Error\nAdd File '{raw_path}' expects lines starting with '+'. Offending line: {l}"
                    content_lines.append(l[1:])
                    i += 1

                abs_path = Path.cwd() / rel
                already_exists = abs_path.exists()
                if already_exists and abs_path.is_dir():
                    return f"## Error\nCannot add file; path is a directory: {rel}"
                if already_exists:
                    return f"## Error\nCannot add file; it already exists: {rel}"

                _write_file(abs_path, content_lines)

                # Report
                added_count = len(content_lines)
                summary_ops.append(f"Added {rel} (+{added_count})")
                preview = "\n".join("+" + c for c in content_lines[:min(30, len(content_lines))])
                trunc_note = ""
                if len(content_lines) > 30:
                    trunc_note = f"\n... (initial content truncated, {len(content_lines) - 30} more lines hidden) ..."
                block = (
                    f"### Added: `{rel}`\n"
                    f"- Lines added: **{added_count}**\n"
                    + _md_codeblock(preview + trunc_note, "diff")
                )
                results_md.append(block)
                continue

            # DELETE FILE
            if line.startswith("*** Delete File: "):
                raw_path = line[len("*** Delete File: "):].strip()
                try:
                    rel = _safe_rel_path(raw_path)
                except Exception as e:
                    return f"## Error\nInvalid Delete path '{raw_path}': {e}"

                abs_path = Path.cwd() / rel
                if abs_path.exists():
                    if abs_path.is_dir():
                        return f"## Error\nDelete File points to a directory: {rel}"
                    old_text = abs_path.read_text(encoding="utf-8")
                    old_lines = old_text.splitlines()
                    old_count = len(old_lines)
                    abs_path.unlink()
                    summary_ops.append(f"Deleted {rel} (-{old_count})")
                    results_md.append(
                        f"### Deleted: `{rel}`\n"
                        f"- Previous line count: **{old_count}**\n"
                    )
                else:
                    return f"## Error\nCannot delete non-existent file: {rel}"
                i += 1
                continue

            # UPDATE FILE
            if line.startswith("*** Update File: "):
                raw_path = line[len("*** Update File: "):].strip()
                try:
                    rel = _safe_rel_path(raw_path)
                except Exception as e:
                    return f"## Error\nInvalid Update path '{raw_path}': {e}"

                abs_path = Path.cwd() / rel
                if not abs_path.exists() or abs_path.is_dir():
                    return f"## Error\nUpdate target does not exist or is a directory: {rel}"

                i += 1
                move_to: Optional[Path] = None
                moved_to_text = ""
                if i < len(lines) and lines[i].startswith("*** Move to: "):
                    newp = lines[i][len("*** Move to: "):].strip()
                    try:
                        move_to = _safe_rel_path(newp)
                        moved_to_text = f" -> moved to `{move_to}`"
                    except Exception as e:
                        return f"## Error\nInvalid Move to path '{newp}': {e}"
                    i += 1

                old_text = abs_path.read_text(encoding="utf-8")
                old_lines = old_text.splitlines()

                def find_subseq(hay: List[str], needle: List[str]) -> int:
                    if not needle:
                        return 0
                    for s in range(0, len(hay) - len(needle) + 1):
                        if hay[s:s+len(needle)] == needle:
                            return s
                    return -1

                changed = False
                file_lines = old_lines[:]

                while i < len(lines) and lines[i].startswith("@@"):
                    i += 1
                    before_seq: List[str] = []
                    after_seq:  List[str] = []
                    while i < len(lines) and not lines[i].startswith("*** ") and not lines[i].startswith("@@"):
                        hl = lines[i]
                        if hl == "":
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

                    if i < len(lines) and lines[i].strip() == "*** End of File":
                        i += 1

                if not changed:
                    return f"## Error\nNo hunks provided for Update File: {rel}"

                new_text = "\n".join(file_lines)
                if new_text and not new_text.endswith("\n"):
                    new_text += "\n"
                abs_path.write_text(new_text, encoding="utf-8")

                if move_to:
                    new_abs = Path.cwd() / move_to
                    new_abs.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(abs_path), str(new_abs))
                    final_read_path = new_abs
                else:
                    final_read_path = abs_path

                final_text = final_read_path.read_text(encoding="utf-8")
                final_lines = final_text.splitlines()
                diff_text, added, removed = _diff_and_stats(
                    old_lines, final_lines, from_name=str(rel), to_name=str(move_to if move_to else rel)
                )
                net = added - removed
                summary_ops.append(f"Updated {rel}{moved_to_text} (+{added}/-{removed}, net {net:+d})")

                block = (
                    f"### Updated: `{rel}`{moved_to_text}\n"
                    f"- Lines added: **{added}**, removed: **{removed}**, net: **{net:+d}**\n"
                    + _md_codeblock(diff_text if diff_text.strip() else "(no visible diff; whitespace-only change or metadata)", "diff")
                )
                results_md.append(block)
                continue

            # Unknown line
            return f"## Error\nUnrecognized patch directive: {line}"

        if i >= len(lines) or lines[i].strip() != "*** End Patch":
            return "## Error\nPatch must end with '*** End Patch'."

        if not summary_ops:
            return "## Error\nPatch contained no operations."

        summary_list = "\n".join(f"- {op}" for op in summary_ops)
        detail = "\n\n".join(results_md)
        return f"## Patch Applied\n{summary_list}\n\n---\n{detail}"
