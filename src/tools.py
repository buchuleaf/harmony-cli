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

# --- Constants for Truncation & Display ---

MAX_TOOL_OUTPUT_LINES = 25          # per-stream truncation when formatting small results
MAX_LINE_LENGTH = 1000
MODEL_MAX_CHARS = 25000             # soft cap for model-visible content; beyond this we auto-truncate
DISPLAY_MAX_LINES = 25              # hard cap for on-screen display (user)
MAX_DIFF_LINES_PER_FILE = 300       # limit in diff previews to avoid explosion

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

def _display_truncate(md: str, max_lines: int = DISPLAY_MAX_LINES) -> str:
    """
    Trim any markdown to ~N raw lines for console display, but report *accurate* content-line counts:
    - Content lines exclude code-fence markers (```), headings (lines starting with '#'), and blank lines.
    - If truncation cuts inside a code fence, append a closing fence to keep rendering stable.
    """
    lines = md.splitlines()
    if len(lines) <= max_lines:
        # Nothing to trim; still report using content-aware counts if needed.
        return md

    # Trim by raw lines (to respect display budget)
    trimmed_lines = lines[:max_lines]

    # Count the number of fence markers in the trimmed region. If odd, we're inside a fence.
    fence_count = sum(1 for L in trimmed_lines if L.strip().startswith("```"))
    if fence_count % 2 == 1:
        # We were cut mid-fence; close it to avoid broken formatting.
        trimmed_lines.append("```")

    # Content-aware counting
    def _count_visible(ls: list[str]) -> int:
        visible = 0
        in_code = False
        for L in ls:
            s = L.strip()
            if s.startswith("```"):
                in_code = not in_code
                continue  # fence markers are not content
            if not s:
                continue  # skip blank
            if not in_code and s.startswith("#"):
                continue  # headings are not counted as content
            visible += 1
        return visible

    total_visible = _count_visible(lines)
    shown_visible = _count_visible(trimmed_lines)
    hidden_visible = max(total_visible - shown_visible, 0)

    trimmed = "\n".join(trimmed_lines)
    return trimmed + (
        f"\n\n... (display truncated: showing {shown_visible} of {total_visible} content lines, "
        f"{hidden_visible} hidden) ...\n"
    )


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
        diff_lines = diff_lines[:MAX_DIFF_LINES_PER_FILE] + [f"... (diff truncated, {omitted} more lines hidden) ..."]
    diff_text = "\n".join(diff_lines)
    return diff_text, added, removed

# --- Tool Executor ---

class ToolExecutor:
    """
    Tools:
      - exec(kind="python"|"shell", code, timeout?)
      - apply_patch(patch)
    Each tool returns a dict:
      { "model": full_or_truncated_for_model, "display": ~10-line console view }
    """
    def __init__(self):
        self._available_tools: Dict[str, Callable[..., Dict[str, str]]] = {
            "exec": self.exec,
            "apply_patch": self.apply_patch,
        }

    def execute_tool(self, tool_name: str, **kwargs) -> Dict[str, str]:
        method = self._available_tools.get(tool_name)
        if method is None:
            return {"model": f"## Error\nTool `{tool_name}` not found.", "display": f"## Error\nTool `{tool_name}` not found."}
        try:
            return method(**kwargs)
        except TypeError as te:
            msg = f"## Error\nInvalid arguments for `{tool_name}`: {te}"
            return {"model": msg, "display": _display_truncate(msg)}
        except Exception as e:
            msg = f"## Error\n{type(e).__name__}: {e}"
            return {"model": msg, "display": _display_truncate(msg)}

    # --- run ---

    def exec(self, kind: str, code: str, timeout: int = 30) -> Dict[str, str]:
        kind = (kind or "").lower()
        if kind not in ("python", "shell"):
            msg = "## Error\n`kind` must be 'python' or 'shell'."
            return {"model": msg, "display": _display_truncate(msg)}

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
            msg = "## Error\nExecution timed out after {}s.\n".format(timeout) + _md_codeblock(str(te), "text")
            return {"model": msg, "display": _display_truncate(msg)}
        except Exception as e:
            msg = "## Error\nExecution failed:\n" + _md_codeblock(str(e), "text")
            return {"model": msg, "display": _display_truncate(msg)}

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        raw_payload = _compose_cache_payload(stdout, stderr, result.returncode)

        # Build model-oriented markdown (full unless auto-truncated by size)
        ok = (result.returncode == 0)
        header = "## Command Successful\n" if ok else f"## Command FAILED (Exit Code: {result.returncode})\n"

        # For smaller results, keep the nice sectioned formatting
        if len(raw_payload) <= MODEL_MAX_CHARS:
            model_md_sections = [header]
            if stdout:
                model_md_sections.append("### STDOUT\n")
                model_md_sections.append(_md_codeblock(stdout.rstrip("\n"), "bash" if kind == "shell" else "python"))
            if stderr:
                model_md_sections.append("### STDERR\n")
                model_md_sections.append(_md_codeblock(stderr.rstrip("\n"), "text"))
            if not stdout and not stderr:
                model_md_sections.append("The command produced no output.\n")
            model_content = "".join(model_md_sections)
        else:
            # Auto-truncate to protect context window
            kept = raw_payload[:MODEL_MAX_CHARS]
            model_content = (
                header
                + "### OUTPUT (combined)\n"
                + _md_codeblock(kept, "text")
                + f"_MODEL NOTE: Result automatically truncated to protect the context window (kept first {MODEL_MAX_CHARS} of {len(raw_payload)} chars). Consider narrowing the command or asking for specific ranges._\n"
            )

        # Build compact on-screen display (~10 lines)
        display_sections = [header]
        if stdout:
            display_sections.append("### STDOUT\n")
            display_sections.append(_md_codeblock(_truncate_output(stdout.rstrip("\n"), MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH), "bash" if kind == "shell" else "python"))
        if stderr:
            display_sections.append("### STDERR\n")
            display_sections.append(_md_codeblock(_truncate_output(stderr.rstrip("\n"), MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH), "text"))
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
                if abs_path.exists() and abs_path.is_dir():
                    msg = f"## Error\nCannot add file; path is a directory: {rel}"
                    return {"model": msg, "display": _display_truncate(msg)}
                if abs_path.exists():
                    msg = f"## Error\nCannot add file; it already exists: {rel}"
                    return {"model": msg, "display": _display_truncate(msg)}

                _write_file(abs_path, content_lines)
                added_count = len(content_lines)
                summary_ops.append(f"Added {rel} (+{added_count})")
                preview = "\n".join("+" + c for c in content_lines[:min(30, len(content_lines))])
                trunc_note = ""
                if len(content_lines) > 30:
                    trunc_note = f"\n... (initial content truncated, {len(content_lines) - 30} more lines hidden) ..."
                block = f"### Added: `{rel}`\n- Lines added: **{added_count}**\n" + _md_codeblock(preview + trunc_note, "diff")
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
                if abs_path.exists():
                    if abs_path.is_dir():
                        msg = f"## Error\nDelete File points to a directory: {rel}"
                        return {"model": msg, "display": _display_truncate(msg)}
                    old_text = abs_path.read_text(encoding="utf-8")
                    old_lines = old_text.splitlines()
                    old_count = len(old_lines)
                    abs_path.unlink()
                    summary_ops.append(f"Deleted {rel} (-{old_count})")
                    results_md.append(f"### Deleted: `{rel}`\n- Previous line count: **{old_count}**\n")
                else:
                    msg = f"## Error\nCannot delete non-existent file: {rel}"
                    return {"model": msg, "display": _display_truncate(msg)}
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

                while i < len(lines):
                    line = lines[i]
                    if _is_end(line):
                        break
                    mop, _a = _match_header(line)
                    if mop in {"add","delete","update","overwrite","move_to"}:
                        break
                    if not line.startswith("@@"):
                        if line.strip() == "":
                            i += 1
                            continue
                        msg = f"## Error\nExpected a hunk starting with '@@' in Update for '{rel}', got: {line}"
                        return {"model": msg, "display": _display_truncate(msg)}
                    i += 1  # consume hunk header

                    before_seq: List[str] = []
                    after_seq:  List[str] = []
                    while i < len(lines):
                        hl = lines[i]
                        if _is_end(hl):
                            break
                        mop2, _a2 = _match_header(hl)
                        if mop2 in {"add","delete","update","overwrite","move_to"} or hl.startswith("@@"):
                            break

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
                            msg = f"## Error\nInvalid hunk line prefix '{prefix}' in Update for '{rel}'."
                            return {"model": msg, "display": _display_truncate(msg)}
                        i += 1

                    start_idx = find_subseq(file_lines, before_seq)
                    if start_idx == -1:
                        relaxed_hay = [s.rstrip() for s in file_lines]
                        relaxed_need = [s.rstrip() for s in before_seq]
                        start_idx = find_subseq(relaxed_hay, relaxed_need)

                    if start_idx == -1:
                        hunk_reports.append(f"- ⚠️  Hunk not applied (context not found); size before/after: {len(before_seq)}/{len(after_seq)}")
                        continue

                    end_idx = start_idx + len(before_seq)
                    file_lines = file_lines[:start_idx] + after_seq + file_lines[end_idx:]
                    changed_any = True
                    hunk_reports.append(f"- ✅ Hunk applied at lines {start_idx}:{end_idx} (−{len(before_seq)} → +{len(after_seq)})")

                if not changed_any:
                    any_errors.append(f"Update produced no applied hunks for {rel}.")
                    continue

                new_text = "\n".join(file_lines)
                if new_text and not new_text.endswith("\n"):
                    new_text += "\n"
                abs_path.write_text(new_text, encoding="utf-8")

                final_read_path = abs_path
                if move_to:
                    new_abs = Path.cwd() / move_to
                    new_abs.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(abs_path), str(new_abs))
                    final_read_path = new_abs

                final_text = final_read_path.read_text(encoding="utf-8")
                final_lines = final_text.splitlines()
                diff_text, added, removed = _diff_and_stats(
                    old_lines, final_lines, from_name=str(rel), to_name=str(move_to if move_to else rel)
                )
                net = added - removed
                summary_ops.append(f"Updated {rel}{moved_to_text} (+{added}/-{removed}, net {net:+d})")

                block = (
                    f"### Updated: `{rel}`{moved_to_text}\n"
                    + "\n".join(hunk_reports) + "\n"
                    f"- Lines added: **{added}**, removed: **{removed}**, net: **{net:+d}**\n"
                    + _md_codeblock(diff_text if diff_text.strip() else "(no visible diff; whitespace-only change or metadata)", "diff")
                )
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
        detail = "\n\n".join(results_md) if results_md else "_(no details)_"

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
