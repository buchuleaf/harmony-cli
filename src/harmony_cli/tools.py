import os
import sys
import uuid
import json
import shutil
import tempfile
import difflib
import subprocess
import shlex
import re
from pathlib import Path
from typing import Dict, Callable, Optional, List, Tuple

# --- Constants for Truncation & Display ---

MAX_TOOL_OUTPUT_LINES = 25          # per-stream truncation when formatting small results
MAX_LINE_LENGTH = 1000
MODEL_MAX_CHARS = 25000             # soft cap for model-visible content; beyond this we auto-truncate
MODEL_MAX_OUTPUT_LINES = 120        # default line cap for model-visible stdout/stderr sections
MODEL_STRICT_OUTPUT_LINES = 40      # tighter cap for known high-noise commands (e.g. recursive ls, recursive grep)
MODEL_SEARCH_OUTPUT_LINES = 80      # mid-tier cap for broad searches
MODEL_MAX_LINE_LENGTH = 400         # line width cap for model-visible sections
DISPLAY_MAX_LINES = 25              # hard cap for on-screen display (user)
MAX_DIFF_LINES_PER_FILE = 300       # limit in diff previews to avoid explosion
MAX_PATCH_SECTIONS = 12             # cap the number of per-file sections surfaced in patch reports

# --- Markdown / Highlighting helpers ---

_ALLOWED_LEXERS = {"python", "bash", "diff", "json", "text"}

def _normalize_lexer(language: Optional[str]) -> str:
    if not language:
        return "text"
    lang = language.strip().lower()
    if lang in {"sh", "shell"}:
        lang = "bash"
    if lang in {"plaintext", "plain", "txt"}:
        lang = "text"
    if lang not in _ALLOWED_LEXERS:
        return "text"
    return lang

def _md_codeblock(body: str, language: Optional[str] = "") -> str:
    if body is None:
        body = ""
    text = body
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

def _analyze_shell_command(command: str) -> Dict[str, bool]:
    """Detect shell patterns that tend to overwhelm output buffers."""
    traits = {
        "recursive_ls": False,
        "recursive_search": False,
        "broad_search": False,
        "bulk_listing": False,
        "has_head": False,
    }
    if not command:
        return traits

    # Split on common shell separators to isolate pipelines; fall back to naive splits on parsing errors.
    segments = re.split(r"[;&\n]", command)
    for segment in segments:
        if not segment.strip():
            continue
        pipeline_parts = segment.split("|")
        for part in pipeline_parts:
            part = part.strip()
            if not part:
                continue
            try:
                tokens = shlex.split(part, posix=True)
            except ValueError:
                tokens = part.split()

            if not tokens:
                continue

            cmd = tokens[0]
            if cmd == "head":
                traits["has_head"] = True

            if cmd == "ls":
                for opt in tokens[1:]:
                    if opt == "--":
                        break
                    if opt.startswith("--"):
                        if opt == "--recursive" or opt.startswith("--recursive="):
                            traits["recursive_ls"] = True
                        continue
                    if opt.startswith("-") and "R" in opt[1:]:
                        traits["recursive_ls"] = True
                if any(opt in {"-a", "-A", "--all"} for opt in tokens[1:]):
                    traits["bulk_listing"] = True
                continue

            if cmd in {"find", "tree", "du"}:
                traits["bulk_listing"] = True
                if cmd == "du" and not any(opt.startswith("-h") for opt in tokens[1:]):
                    traits["bulk_listing"] = True
                continue

            if cmd in {"grep", "egrep", "fgrep"}:
                traits["broad_search"] = True
                for opt in tokens[1:]:
                    if opt == "--":
                        break
                    if opt.startswith("--"):
                        if opt.startswith("--recursive"):
                            traits["recursive_search"] = True
                        continue
                    if opt.startswith("-") and any(flag in opt[1:] for flag in ("r", "R", "d")):
                        traits["recursive_search"] = True
                continue

            if cmd in {"rg", "ripgrep"}:
                traits["broad_search"] = True
                traits["recursive_search"] = True
                continue

            if cmd in {"cat", "bat", "less"}:
                traits["bulk_listing"] = True

    return traits

def _truncate_output(
    output: str,
    max_lines: int,
    max_line_length: int,
    trunc_note_template: Optional[str] = None,
) -> str:
    lines = output.splitlines()
    original_line_count = len(lines)

    truncation_message = ""
    if original_line_count > max_lines:
        omitted_lines = original_line_count - max_lines
        lines = lines[:max_lines]
        template = trunc_note_template or "... (output truncated, {omitted_lines} more lines hidden) ..."
        truncation_message = "\n" + template.format(omitted_lines=omitted_lines)

    processed_lines = []
    for line in lines:
        if len(line) > max_line_length:
            processed_lines.append(line[:max_line_length] + " ... (line truncated) ...")
        else:
            processed_lines.append(line)

    return "\n".join(processed_lines) + truncation_message

def _display_truncate(md: str, max_lines: int = DISPLAY_MAX_LINES) -> str:
    """Trim markdown for console display and append a simple hidden-line note."""
    lines = md.splitlines()
    if len(lines) <= max_lines:
        # Nothing to trim; still report using content-aware counts if needed.
        return md

    # Trim by raw lines (to respect display budget)
    trimmed_lines = lines[:max_lines]
    shown_raw_count = len(trimmed_lines)

    # Count the number of fence markers in the trimmed region. If odd, we're inside a fence.
    fence_count = sum(1 for L in trimmed_lines if L.strip().startswith("```"))
    if fence_count % 2 == 1:
        # We were cut mid-fence; close it to avoid broken formatting.
        trimmed_lines.append("```")

    trimmed = "\n".join(trimmed_lines)

    hidden_raw = max(len(lines) - shown_raw_count, 0)

    suffix = f"\n\n... {hidden_raw} lines hidden ...\n"
    return trimmed + suffix

def _compose_cache_payload(stdout: str, stderr: str, returncode: int) -> str:
    parts = [f"[exit_code] {returncode}"]
    if stdout:
        parts.append("[stdout]\n" + stdout.rstrip("\n"))
    if stderr:
        parts.append("[stderr]\n" + stderr.rstrip("\n"))
    return "\n\n".join(parts)

def _safe_rel_path(p: str) -> Path:
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
        diff_lines = diff_lines[:MAX_DIFF_LINES_PER_FILE] + [f". {omitted} lines hidden ."]
    diff_text = "\n".join(diff_lines)
    return diff_text, added, removed

# --- Tool Executor ---

class ToolExecutor:
    """
    Tools:
      - exec(kind="python"|"shell", code, timeout?)
      - python(code, timeout?)
      - shell(command, timeout?)
      - apply_patch(patch)
    Each tool returns a dict:
      { "model": full_or_truncated_for_model, "display": ~10-line console view }
    """
    def __init__(self):
        self._available_tools: Dict[str, Callable[..., Dict[str, str]]] = {
            #"exec": self.exec,
            "python": self.python,
            "shell": self.shell,  # <-- Add this line
            #"apply_patch": self.apply_patch,
        }

    def shell(self, command: str, timeout: int = 30) -> Dict[str, str]:
        # Call the existing exec method with kind="shell"
        return self.exec(kind="shell", code=command, timeout=timeout)
    
    def execute_tool(self, tool_name: str, **kwargs) -> Dict[str, str]:
        # This method will now correctly find the "shell" tool.
        method = self._available_tools.get(tool_name)
        if method is None:
            # Note: I've slightly improved the error message for clarity.
            msg = f"## Error\nTool `{tool_name}` not found."
            return {"model": msg, "display": _display_truncate(msg)}
        try:
            return method(**kwargs)
        except TypeError as te:
            msg = f"## Error\nInvalid arguments for `{tool_name}`: {te}"
            return {"model": msg, "display": _display_truncate(msg)}
        except Exception as e:
            msg = f"## Error\n{type(e).__name__}: {e}"
            return {"model": msg, "display": _display_truncate(msg)}

    # --- run ---

    def _run_python_process(self, code: str, timeout: int) -> subprocess.CompletedProcess:
        # In frozen bundles, delegate to the app with --python-tool
        if getattr(sys, "frozen", False):
            return self._run_python_process_frozen(code, timeout)
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    
    def _run_python_process_frozen(self, code: str, timeout: int) -> subprocess.CompletedProcess:
        tmp = None
        tmp_path: Optional[str] = None
        try:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
            tmp.write(code)
            tmp.flush()
            tmp_path = tmp.name
        finally:
            if tmp:
                tmp.close()

        try:
            if not tmp_path:
                raise RuntimeError("Failed to prepare temporary python file")
            
            # Debug: print what we're calling
            import sys
            cmd = [sys.executable, "--python-tool", tmp_path]
            # For frozen apps, sys.executable is the executable path
            # Make sure we're passing args correctly
            
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        finally:
            try:
                if tmp_path:
                    os.unlink(tmp_path)
            except OSError:
                pass

    def exec(self, kind: str, code: str, timeout: int = 30) -> Dict[str, str]:
        kind = (kind or "").lower()
        if kind not in ("python", "shell"):
            msg = "## Error\n`kind` must be 'python' or 'shell'."
            return {"model": msg, "display": _display_truncate(msg)}

        command_traits: Dict[str, bool] = {}
        if kind == "shell" and isinstance(code, str):
            command_traits = _analyze_shell_command(code)

        try:
            if kind == "python":
                result = self._run_python_process(code, timeout)
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
            msg = "## Error\nExecution timed out after {}s.\n".format(timeout) + _md_codeblock(str(te), "text")
            return {"model": msg, "display": _display_truncate(msg)}
        except Exception as e:
            msg = "## Error\nExecution failed:\n" + _md_codeblock(str(e), "text")
            return {"model": msg, "display": _display_truncate(msg)}

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        stdout_clean = stdout.rstrip("\n")
        stderr_clean = stderr.rstrip("\n")

        stdout_for_model = _truncate_output(
            stdout_clean,
            MODEL_STRICT_OUTPUT_LINES if command_traits.get("recursive_ls") or command_traits.get("recursive_search") else MODEL_MAX_OUTPUT_LINES,
            MODEL_MAX_LINE_LENGTH,
        )
        stderr_for_model = _truncate_output(stderr_clean, MODEL_MAX_OUTPUT_LINES, MODEL_MAX_LINE_LENGTH)

        ok = (result.returncode == 0)
        header = "## Command Successful\n" if ok else f"## Command FAILED (Exit Code: {result.returncode})\n"

        model_md_sections: List[str] = [header]
        if stdout_for_model:
            model_md_sections.append("### STDOUT\n")
            model_md_sections.append(_md_codeblock(stdout_for_model, "bash" if kind == "shell" else "python"))
        if stderr_for_model:
            model_md_sections.append("### STDERR\n")
            model_md_sections.append(_md_codeblock(stderr_for_model, "text"))
        if not stdout_for_model and not stderr_for_model:
            model_md_sections.append("The command produced no output.\n")

        model_content = "".join(model_md_sections)

        if len(model_content) > MODEL_MAX_CHARS:
            truncated_payload = _compose_cache_payload(stdout_for_model, stderr_for_model, result.returncode)
            kept = truncated_payload[:MODEL_MAX_CHARS]
            model_content = (
                header
                + "### OUTPUT (combined)\n"
                + _md_codeblock(kept, "text")
                + f"_MODEL NOTE: Result automatically truncated to protect the context window (kept first {MODEL_MAX_CHARS} of {len(truncated_payload)} chars after safety limits). Consider narrowing the command or asking for specific ranges._\n"
            )

        display_sections = [header]
        if stdout:
            display_sections.append("### STDOUT\n")
            display_sections.append(
                _md_codeblock(
                    _truncate_output(
                        stdout_clean,
                        MAX_TOOL_OUTPUT_LINES,
                        MAX_LINE_LENGTH,
                        trunc_note_template=". {omitted_lines} lines hidden .",
                    ),
                    "bash" if kind == "shell" else "python",
                )
            )
        if stderr:
            display_sections.append("### STDERR\n")
            display_sections.append(
                _md_codeblock(
                    _truncate_output(
                        stderr_clean,
                        MAX_TOOL_OUTPUT_LINES,
                        MAX_LINE_LENGTH,
                        trunc_note_template=". {omitted_lines} lines hidden .",
                    ),
                    "text",
                )
            )
        if not stdout and not stderr:
            display_sections.append("The command produced no output.\n")

        # Helpful display notes for common noisy commands
        notes: List[str] = []
        if command_traits.get("recursive_ls"):
            notes.append("Recursive directory listings are trimmed to protect the context window. Narrow the path, add a depth flag, or pipe into `head` for a quick peek.")
        if command_traits.get("bulk_listing"):
            notes.append("Large file listings are abbreviated. Consider filters (e.g., `find ... -maxdepth`, `du -h`) or piping through `head`.")
        if command_traits.get("recursive_search") and "has_head" not in command_traits:
            notes.append("Recursive search results are clipped. Pipe the command into `head` or refine the pattern to keep output manageable.")

        for note in notes:
            display_sections.append(f"_{note}_\n")

        display_content = _display_truncate("".join(display_sections), DISPLAY_MAX_LINES)
        return {"model": model_content, "display": display_content}

    def python(self, code: str, timeout: int = 30) -> Dict[str, str]:
        try:
            result = self._run_python_process(code, timeout)
        except subprocess.TimeoutExpired as te:
            msg = "## Error\nExecution timed out after {}s.\n".format(timeout) + _md_codeblock(str(te), "text")
            return {"model": msg, "display": _display_truncate(msg)}
        except Exception as e:
            msg = "## Error\nExecution failed:\n" + _md_codeblock(str(e), "text")
            return {"model": msg, "display": _display_truncate(msg)}

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        stdout_clean = stdout.rstrip("\n")
        stderr_clean = stderr.rstrip("\n")

        stdout_for_model = _truncate_output(stdout_clean, MODEL_MAX_OUTPUT_LINES, MODEL_MAX_LINE_LENGTH)
        stderr_for_model = _truncate_output(stderr_clean, MODEL_MAX_OUTPUT_LINES, MODEL_MAX_LINE_LENGTH)

        ok = (result.returncode == 0)
        header = "## Command Successful\n" if ok else f"## Command FAILED (Exit Code: {result.returncode})\n"

        model_md_sections: List[str] = [header]
        if stdout_for_model:
            model_md_sections.append("### STDOUT\n")
            model_md_sections.append(_md_codeblock(stdout_for_model, "python"))
        if stderr_for_model:
            model_md_sections.append("### STDERR\n")
            model_md_sections.append(_md_codeblock(stderr_for_model, "text"))
        if not stdout_for_model and not stderr_for_model:
            model_md_sections.append("The command produced no output.\n")

        model_content = "".join(model_md_sections)

        if len(model_content) > MODEL_MAX_CHARS:
            truncated_payload = _compose_cache_payload(stdout_for_model, stderr_for_model, result.returncode)
            kept = truncated_payload[:MODEL_MAX_CHARS]
            model_content = (
                header
                + "### OUTPUT (combined)\n"
                + _md_codeblock(kept, "text")
                + f"_MODEL NOTE: Result automatically truncated to protect the context window (kept first {MODEL_MAX_CHARS} of {len(truncated_payload)} chars after safety limits)._\n"
            )

        display_sections = [header]
        if stdout:
            display_sections.append("### STDOUT\n")
            display_sections.append(
                _md_codeblock(
                    _truncate_output(
                        stdout_clean,
                        MAX_TOOL_OUTPUT_LINES,
                        MAX_LINE_LENGTH,
                        trunc_note_template=". {omitted_lines} lines hidden .",
                    ),
                    "python",
                )
            )
        if stderr:
            display_sections.append("### STDERR\n")
            display_sections.append(
                _md_codeblock(
                    _truncate_output(
                        stderr_clean,
                        MAX_TOOL_OUTPUT_LINES,
                        MAX_LINE_LENGTH,
                        trunc_note_template=". {omitted_lines} lines hidden .",
                    ),
                    "text",
                )
            )
        if not stdout and not stderr:
            display_sections.append("The command produced no output.\n")

        display_content = _display_truncate("".join(display_sections), DISPLAY_MAX_LINES)
        return {"model": model_content, "display": display_content}

    # --- apply_patch (forgiving; partial hunks; overwrite support) ---

    def apply_patch(self, patch: str) -> Dict[str, str]:
        if not isinstance(patch, str) or not patch.strip():
            msg = "## Error\n`patch` must be a non-empty string."
            return {"model": msg, "display": _display_truncate(msg)}

        # Strip code fences if present
        text = patch.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
            if text.endswith("```"):
                text = text[:-3]
        lines = text.splitlines()
        i = 0

        def _strip(s: str) -> str:
            return s.rstrip("\n")

        def _is_begin(line: str) -> bool:
            return _strip(line).strip().lower() == "*** begin patch"

        def _is_end(line: str) -> bool:
            return _strip(line).strip().lower() == "*** end patch"

        def _match_header(line: str) -> Tuple[Optional[str], Optional[str]]:
            s = _strip(line).strip()
            m = re.match(r"^\*\*\*\s*(Add File|Delete File|Update File|Overwrite File|Move to)\s*:\s*(.+)$", s, flags=re.IGNORECASE)
            if not m:
                return None, None
            op = m.group(1).strip().lower()
            arg = m.group(2).strip()
            if op == "add file": return "add", arg
            if op == "delete file": return "delete", arg
            if op == "update file": return "update", arg
            if op == "overwrite file": return "overwrite", arg
            if op == "move to": return "move_to", arg
            return None, None

        if i >= len(lines) or not _is_begin(lines[i]):
            msg = "## Error\nPatch must start with '*** Begin Patch'."
            return {"model": msg, "display": _display_truncate(msg)}
        i += 1

        results_md: List[str] = []
        summary_ops: List[str] = []
        any_errors: List[str] = []

        def _write_file(path: Path, content_lines: List[str]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            txt = "\n".join(content_lines)
            if txt and not txt.endswith("\n"):
                txt += "\n"
            path.write_text(txt, encoding="utf-8")

        while i < len(lines):
            raw = lines[i]
            if _is_end(raw):
                break

            op, arg = _match_header(raw)
            if not op:
                msg = f"## Error\nUnrecognized patch directive: {raw}"
                return {"model": msg, "display": _display_truncate(msg)}

            # ADD
            if op == "add":
                raw_path = arg
                try:
                    rel = _safe_rel_path(raw_path)
                except Exception as e:
                    msg = f"## Error\nInvalid Add path '{raw_path}': {e}"
                    return {"model": msg, "display": _display_truncate(msg)}
                i += 1
                content_lines: List[str] = []
                while i < len(lines):
                    l = lines[i]
                    mo, _a = _match_header(l)
                    if mo or _is_end(l):
                        break
                    if not l.startswith("+"):
                        msg = f"## Error\nAdd File '{raw_path}' expects lines starting with '+'. Offending line: {l}"
                        return {"model": msg, "display": _display_truncate(msg)}
                    content_lines.append(l[1:])
                    i += 1

                abs_path = Path.cwd() / rel
                if abs_path.exists():
                    old_text = abs_path.read_text(encoding="utf-8")
                    old_lines = old_text.splitlines()
                else:
                    old_lines = []
                _write_file(abs_path, content_lines)
                diff_text, added, removed = _diff_and_stats(old_lines, content_lines, from_name="/dev/null", to_name=str(rel))
                net = added - removed
                summary_ops.append(f"Added {rel} (+{added}/-{removed}, net {net:+d})")
                block = f"### Added: `{rel}`\n- Lines added: **{added}**, removed: **{removed}**, net: **{net:+d}**\n" + _md_codeblock(diff_text, "diff")
                results_md.append(block)
                continue

            # DELETE
            if op == "delete":
                raw_path = arg
                try:
                    rel = _safe_rel_path(raw_path)
                except Exception as e:
                    msg = f"## Error\nInvalid Delete path '{raw_path}': {e}"
                    return {"model": msg, "display": _display_truncate(msg)}
                abs_path = Path.cwd() / rel
                if not abs_path.exists() or abs_path.is_dir():
                    msg = f"## Error\nDelete target does not exist or is a directory: {rel}"
                    return {"model": msg, "display": _display_truncate(msg)}
                old_text = abs_path.read_text(encoding="utf-8")
                old_lines = old_text.splitlines()
                abs_path.unlink()
                diff_text, added, removed = _diff_and_stats(old_lines, [], from_name=str(rel), to_name="/dev/null")
                net = added - removed
                summary_ops.append(f"Deleted {rel} (+{added}/-{removed}, net {net:+d})")
                block = f"### Deleted: `{rel}`\n- Lines added: **{added}**, removed: **{removed}**, net: **{net:+d}**\n" + _md_codeblock(diff_text, "diff")
                results_md.append(block)
                i += 1
                continue

            # OVERWRITE
            if op == "overwrite":
                raw_path = arg
                try:
                    rel = _safe_rel_path(raw_path)
                except Exception as e:
                    msg = f"## Error\nInvalid Overwrite path '{raw_path}': {e}"
                    return {"model": msg, "display": _display_truncate(msg)}
                i += 1
                new_content: List[str] = []
                while i < len(lines):
                    l = lines[i]
                    mo, _a = _match_header(l)
                    if mo or _is_end(l):
                        break
                    if not l.startswith("+"):
                        msg = f"## Error\nOverwrite File '{raw_path}' expects lines starting with '+'. Offending line: {l}"
                        return {"model": msg, "display": _display_truncate(msg)}
                    new_content.append(l[1:])
                    i += 1

                abs_path = Path.cwd() / rel
                old_text = abs_path.read_text(encoding="utf-8") if abs_path.exists() else ""
                old_lines = old_text.splitlines()
                _write_file(abs_path, new_content)
                final_lines = new_content
                diff_text, added, removed = _diff_and_stats(old_lines, final_lines, from_name=str(rel), to_name=str(rel))
                net = added - removed
                summary_ops.append(f"Overwrote {rel} (+{added}/-{removed}, net {net:+d})")
                block = f"### Overwrote: `{rel}`\n- Lines added: **{added}**, removed: **{removed}**, net: **{net:+d}**\n" + _md_codeblock(diff_text if diff_text.strip() else "(no visible diff)", "diff")
                results_md.append(block)
                continue

            # UPDATE
            if op == "update":
                raw_path = arg
                try:
                    rel = _safe_rel_path(raw_path)
                except Exception as e:
                    msg = f"## Error\nInvalid Update path '{raw_path}': {e}"
                    return {"model": msg, "display": _display_truncate(msg)}

                abs_path = Path.cwd() / rel
                if not abs_path.exists() or abs_path.is_dir():
                    msg = f"## Error\nUpdate target does not exist or is a directory: {rel}"
                    return {"model": msg, "display": _display_truncate(msg)}

                i += 1
                move_to: Optional[Path] = None
                moved_to_text = ""
                if i < len(lines):
                    mop, mto = _match_header(lines[i])
                    if mop == "move_to":
                        newp = mto
                        try:
                            move_to = _safe_rel_path(newp)
                            moved_to_text = f" -> moved to `{move_to}`"
                        except Exception as e:
                            msg = f"## Error\nInvalid Move to path '{newp}': {e}"
                            return {"model": msg, "display": _display_truncate(msg)}
                        i += 1

                old_text = abs_path.read_text(encoding="utf-8")
                old_lines = old_text.splitlines()
                file_lines = old_lines[:]

                def find_subseq(hay: List[str], needle: List[str]) -> int:
                    if not needle:
                        return 0
                    for s in range(0, len(hay) - len(needle) + 1):
                        if hay[s:s+len(needle)] == needle:
                            return s
                    return -1

                changed_any = False
                hunk_reports: List[str] = []

                # Process hunks within this update section
                while i < len(lines):
                    if _is_end(lines[i]):
                        break
                    mo, _a = _match_header(lines[i])
                    if mo:
                        break  # next file section

                    # Collect one contiguous hunk (context + +/- lines) until blank line or next header/end
                    hunk_lines: List[str] = []
                    while i < len(lines):
                        s = lines[i]
                        if _is_end(s):
                            break
                        mo2, _a2 = _match_header(s)
                        if mo2:
                            break
                        if s.strip() == "":
                            # blank lines separate hunks; keep one and advance
                            i += 1
                            break
                        hunk_lines.append(s)
                        i += 1

                    # Partition into context/added/removed
                    ctx = [l[1:] for l in hunk_lines if l.startswith(" ")]
                    add = [l[1:] for l in hunk_lines if l.startswith("+")]
                    rem = [l[1:] for l in hunk_lines if l.startswith("-")]

                    # If there's no explicit context, try a simple replace: remove `rem` then insert `add` at first match
                    pos = find_subseq(file_lines, ctx if ctx else rem)
                    if pos == -1 and ctx:
                        any_errors.append(f"Could not find context in {rel} for a hunk; skipped.")
                        continue

                    if ctx:
                        pos = find_subseq(file_lines, ctx)
                        if pos != -1:
                            # Replace exact context block with (rem applied + add)
                            # Build the hunk application window
                            before = file_lines[:pos]
                            window = file_lines[pos:pos+len(ctx)]
                            after = file_lines[pos+len(ctx):]

                            # Apply removals inside the window if provided; otherwise treat ctx as the target block.
                            if rem:
                                # Remove any exact sub-sequences in order
                                # (Simplified approach: remove lines present in `rem` from `window` by first occurrence)
                                win = window[:]
                                for rl in rem:
                                    try:
                                        win.remove(rl)
                                    except ValueError:
                                        pass
                                window = win

                            # Insert adds where the context was
                            new_window = window + add
                            file_lines = before + new_window + after
                            changed_any = True
                        else:
                            any_errors.append(f"Context not found in {rel}; hunk skipped.")
                    else:
                        # No context: raw remove-then-insert at first position of `rem` or at file end
                        if rem:
                            pos = find_subseq(file_lines, rem)
                            if pos != -1:
                                file_lines = file_lines[:pos] + file_lines[pos+len(rem):]
                                changed_any = True
                        # Insert adds at the end
                        if add:
                            file_lines.extend(add)
                            changed_any = True

                final_text = "\n".join(file_lines) + ("\n" if file_lines and not file_lines[-1].endswith("\n") else "")
                abs_path.write_text(final_text, encoding="utf-8")

                # Optionally move the file
                if move_to:
                    new_abs = Path.cwd() / move_to
                    new_abs.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(abs_path), str(new_abs))
                    rel = move_to
                    abs_path = new_abs

                diff_text, added, removed = _diff_and_stats(old_lines, file_lines, from_name=str(rel), to_name=str(rel))
                net = added - removed
                summary_ops.append(f"Updated {rel}{moved_to_text} (+{added}/-{removed}, net {net:+d})")
                block = f"### Updated: `{rel}`{moved_to_text}\n- Lines added: **{added}**, removed: **{removed}**, net: **{net:+d}**\n" + _md_codeblock(diff_text if diff_text.strip() else "(no visible diff)", "diff")
                results_md.append(block)
                continue

            # Should never reach here
            msg = f"## Error\nUnrecognized patch directive: {raw}"
            return {"model": msg, "display": _display_truncate(msg)}

        if i >= len(lines) or not _is_end(lines[i]):
            msg = "## Error\nPatch must end with '*** End Patch'."
            return {"model": msg, "display": _display_truncate(msg)}

        status_line = "## ✅ Patch Applied\n" if summary_ops else "## ⚠️ Patch Processed (no changes)\n"
        if any_errors:
            status_line = "## ⚠️ Patch Applied With Warnings\n"

        summary_list = "\n".join(f"- {op}" for op in summary_ops) if summary_ops else "_(no changes)_"
        warnings_list = ("\n".join(f"- {w}" for w in any_errors)) if any_errors else ""
        warnings_block = f"\n### Warnings\n{warnings_list}\n" if warnings_list else ""

        omitted_sections = 0
        if results_md:
            if len(results_md) > MAX_PATCH_SECTIONS:
                omitted_sections = len(results_md) - MAX_PATCH_SECTIONS
                detail_blocks = results_md[:MAX_PATCH_SECTIONS] + [
                    f"_NOTE: {omitted_sections} additional file sections hidden to protect the context window. Ask for specific files or smaller hunks if you need the rest._"
                ]
            else:
                detail_blocks = results_md
        else:
            detail_blocks = ["_(no details)_"]

        if omitted_sections:
            warnings_block = warnings_block or ""
            extra_warning = f"- Truncated {omitted_sections} additional file section(s) to keep output manageable."
            if warnings_block:
                warnings_block = warnings_block.rstrip("\n") + "\n" + extra_warning + "\n"
            else:
                warnings_block = f"\n### Warnings\n{extra_warning}\n"

        detail = "\n\n".join(detail_blocks)
        full_md = f"{status_line}{summary_list}{warnings_block}\n\n---\n{detail}"

        # Auto-truncate for the model if the patch report is enormous
        if len(full_md) > MODEL_MAX_CHARS:
            kept = full_md[:MODEL_MAX_CHARS]
            model_content = (
                kept
                + f"\n_MODEL NOTE: Patch result truncated to protect context (kept first {MODEL_MAX_CHARS} of {len(full_md)} chars). Ask for specific files or smaller diffs if needed._\n"
            )
        else:
            model_content = full_md

        display_content = _display_truncate(full_md, DISPLAY_MAX_LINES)
        return {"model": model_content, "display": display_content}
