import json
import os
import sys
import time
import platform
import traceback
import requests
from math import ceil
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from typing import Optional

try:
    from .harmony import create_system_message, create_developer_message
    from .tools import ToolExecutor
except ImportError:  # pragma: no cover - support frozen entrypoints
    from harmony_cli.harmony import create_system_message, create_developer_message
    from harmony_cli.tools import ToolExecutor

# --- Constants and Global Setup ---
API_URL = os.environ.get("HARMONY_CLI_API_URL", "http://localhost:8080/v1/chat/completions")
APP_STATE_DIR = Path(os.environ.get("HARMONY_CLI_HOME", Path.home() / ".harmony-cli"))
TRANSCRIPTS_DIR = APP_STATE_DIR / "transcripts"


def _detect_program_root() -> Path:
    """Best-effort detection of the launch directory, even in frozen builds."""
    env_priority = (
        "HARMONY_CLI_ROOT",
        "PYINSTALLER_ORIGINAL_WORKING_DIR",
        "PWD",
    )

    for name in env_priority:
        value = os.environ.get(name)
        if not value:
            continue
        try:
            candidate = Path(value).expanduser()
        except Exception:
            continue
        if name == "HARMONY_CLI_ROOT" or candidate.exists():
            return candidate.resolve()

    try:
        return Path.cwd().resolve()
    except OSError:
        return Path(__file__).resolve().parent

def _run_python_tool_from_file(path: Path) -> int:
    try:
        code = path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"Failed to read python tool input: {exc}", file=sys.stderr)
        return 1

    # Capture stdout/stderr for the exec'd code
    import io
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    namespace = {"__name__": "__main__"}
    exit_code = 0
    try:
        exec(compile(code, str(path), "exec"), namespace, namespace)
    except SystemExit as exc:
        code = exc.code
        exit_code = int(code) if isinstance(code, int) else 1
    except Exception:
        traceback.print_exc()
        exit_code = 1
    finally:
        # Write captured output to actual stdout/stderr
        captured_out = sys.stdout.getvalue()
        captured_err = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        if captured_out:
            sys.stdout.write(captured_out)
        if captured_err:
            sys.stderr.write(captured_err)
    
    return exit_code

def _maybe_run_python_tool_via_argv(argv: list[str]) -> Optional[int]:
    """Checks for the --python-tool flag. If present, runs the tool and returns an exit code. Otherwise, returns None."""
    if len(argv) >= 2 and argv[1] == "--python-tool":
        if len(argv) < 3:
            print("--python-tool requires a path argument", file=sys.stderr)
            return 2
        else:
            path = Path(argv[2])
            return _run_python_tool_from_file(path)
    return None


APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

def approx_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return ceil(len(text) / 4)

def approx_tokens_from_messages_and_tools(messages, tools) -> int:
    payload = {"model": "gpt-oss", "messages": messages, "tools": tools}
    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return approx_tokens_from_text(s)


def _render_markdown(console: Console, content: str) -> None:
    """Render markdown content for tool outputs with a graceful fallback."""
    if not content:
        console.print("(no output)", markup=False)
        return
    try:
        console.print(Markdown(content))
    except Exception:
        console.print(content, markup=False)

def stream_model_response(messages, tools):
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": "gpt-oss",
        "messages": messages,
        "tools": tools,
        "stream": True,
    }
    with requests.post(API_URL, headers=headers, data=json.dumps(payload), stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue
            body = decoded[6:]
            if body == "[DONE]":
                break
            yield json.loads(body)

# ---------- Export helpers ----------

def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")

def export_chat_json(history, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def export_chat_md(history, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append(f"# Chat Transcript ({datetime.now().isoformat(timespec='seconds')})\n")
    for msg in history:
        role = msg.get("role", "").upper()
        content = msg.get("content") or ""
        tool_name = msg.get("name")
        if role == "SYSTEM":
            lines.append("## System\n")
            lines.append("```text")
            lines.append(content)
            lines.append("```\n")
        elif role == "USER":
            lines.append("## You\n")
            lines.append(content if content.strip() else "_(empty)_")
            lines.append("")
        elif role == "ASSISTANT":
            lines.append("## Assistant\n")
            lines.append(content if content else "_(tool call only)_")
            lines.append("")
            if "tool_calls" in msg and msg["tool_calls"]:
                lines.append("<details><summary>Tool Calls (raw)</summary>\n\n```json")
                lines.append(json.dumps(msg["tool_calls"], ensure_ascii=False, indent=2))
                lines.append("```\n</details>\n")
        elif role == "TOOL":
            title = f"Tool Result: {tool_name}" if tool_name else "Tool Result"
            lines.append(f"### {title}\n")
            lines.append("```markdown")
            lines.append(content)
            lines.append("```\n")
        else:
            lines.append(f"## {role or 'UNKNOWN'}\n")
            lines.append("```text")
            lines.append(content)
            lines.append("```\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")

def parse_export_command(cmd: str):
    parts = cmd.strip().split()
    if len(parts) < 2:
        raise ValueError("Usage: /export md|json [optional/path]")
    fmt = parts[1].lower()
    if fmt not in ("md", "json"):
        raise ValueError("Format must be 'md' or 'json'.")
    custom = Path(parts[2]) if len(parts) >= 3 else None
    return fmt, custom

def default_export_path(fmt: str) -> Path:
    fname = f"chat-{_timestamp()}.{fmt}"
    return TRANSCRIPTS_DIR / fname

# ---------- Input Helpers ----------

def prompt_user(console: Console) -> str:
    """Prompt the user for input; end lines with '\\' to continue typing."""
    prompt_label = "\n[bold cyan]You:[/bold cyan] "
    continuation_label = "... "
    parts: list[str] = []

    while True:
        try:
            text = console.input(prompt_label if not parts else continuation_label)
        except (KeyboardInterrupt, EOFError):
            raise

        if text.endswith("\\"):
            parts.append(text[:-1])
            continue

        parts.append(text)
        return "\n".join(parts)

# ---------- Main CLI ----------

def main():
    # Check for --python-tool FIRST before any other setup
    exit_code = _maybe_run_python_tool_via_argv(sys.argv)
    if exit_code is not None:
        sys.exit(exit_code)
    
    console = Console()

    launch_root = _detect_program_root()
    try:
        os.chdir(launch_root)
    except OSError:
        pass
    program_root = Path.cwd()

    conversation_history = []
    tool_executor = ToolExecutor()

    if platform.system() == "Windows":
        shell_name = "Command Prompt"
        shell_example = "Example: `dir`"
    else:
        shell_name = "bash"
        shell_example = "Example: `ls -l`"

    # ---- Three tools: exec, python, apply_patch ----
    tools_definition = [
        {
            "type": "function",
            "function": {
                "name": "exec",
                "description": f"Execute code via Python or {shell_name}. Large outputs are automatically truncated with a note. Prefer the dedicated `python` tool when you only need Python execution.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind":   {"type": "string", "enum": ["python", "shell"], "description": "Execution mode."},
                        "code":   {"type": "string", "description": "Python source or shell command string."},
                        "timeout":{"type": "integer", "description": "Seconds before kill.", "default": 30}
                    },
                    "required": ["kind", "code"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "python",
                "description": "Execute Python code with the same output handling as exec(kind='python'). Large outputs are automatically truncated with a note.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code":   {"type": "string", "description": "Python source string."},
                        "timeout":{"type": "integer", "description": "Seconds before kill.", "default": 30}
                    },
                    "required": ["code"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": "Edit files by providing a patch document. Always wrap your changes between `*** Begin Patch` and `*** End Patch`. Use one of:\n- `*** Add File: path`\n- `*** Update File: path`\n- `*** Overwrite File: path` (replace file with provided `+` lines)\n- `*** Delete File: path`\n\nInside updates, prefix new lines with `+`, removed lines with `-`, unchanged context with a leading space. You may include `*** Move to: newpath` after an update header to rename. Fenced code blocks are accepted and stripped. Large results auto-truncate.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "patch": {"type": "string", "description": "The patch text to apply."}
                    },
                    "required": ["patch"]
                }
            }
        }
    ]

    instructions = (
        "You are a helpful terminal assistant that can execute code and edit files with the provided tools."
        f"\n\nRoot directory: {program_root}"
    )
    system_message = create_system_message(tools_exist=True)
    developer_message = create_developer_message(instructions, tools_definition)

    # System + developer
    conversation_history.append({"role": "system", "content": system_message})
    conversation_history.append({"role": "user", "content": developer_message})

    console.print(Panel(
        "[bold green]Harmony CLI[/bold green]\n\n"
        "[dim]Commands: /export md [path], /export json [path][/dim]\n"
        "[dim]Ctrl+C to interrupt the current response, Ctrl+D to exit.[/dim]\n"
    ))

    while True:
        try:
            user_input = prompt_user(console)
        except EOFError:
            console.print("\n[bold red]Exiting.[/bold red]")
            break
        except KeyboardInterrupt:
            console.print("\n[dim]Input interrupted. Press Ctrl+D or type 'exit' to quit.[/dim]")
            continue

        if user_input.strip().lower() == "exit":
            console.print("\n[bold red]Exiting.[/bold red]")
            break

        # Handle export commands
        if user_input.strip().startswith("/export"):
            try:
                fmt, custom = parse_export_command(user_input)
                out_path = custom if custom else default_export_path(fmt)
                if fmt == "json":
                    export_chat_json(conversation_history, out_path)
                else:
                    export_chat_md(conversation_history, out_path)
                console.print(Panel(f"Saved transcript to [bold]{out_path}[/bold]", border_style="green"))
            except Exception as e:
                console.print(Panel(f"[bold red]Export error:[/bold red] {e}", border_style="red"))
            continue

        # Normal user turn
        conversation_history.append({"role": "user", "content": user_input})

        # Stream assistant; capture tool calls; execute; loop until final assistant text
        while True:
            prompt_tok_est = approx_tokens_from_messages_and_tools(conversation_history, tools_definition)
            t0 = time.perf_counter()

            full_response_content = ""
            tool_calls_in_progress = []
            tool_print_state = {}
            was_interrupted = False

            console.print("\nAssistant:", markup=False)

            try:
                for chunk in stream_model_response(conversation_history, tools_definition):
                    if not chunk.get("choices"):
                        continue
                    delta = chunk["choices"][0].get("delta", {})

                    if (txt := delta.get("content")):
                        full_response_content += txt
                        console.print(txt, end="", markup=False, highlight=False, soft_wrap=False)

                    if "tool_calls" in delta and delta["tool_calls"]:
                        for tc in delta["tool_calls"]:
                            idx = tc["index"]
                            while len(tool_calls_in_progress) <= idx:
                                tool_calls_in_progress.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            state = tool_print_state.setdefault(idx, {"started": False, "closed": False})
                            call_entry = tool_calls_in_progress[idx]

                            if "id" in tc:
                                call_entry["id"] = tc["id"]

                            if "function" in tc:
                                func_payload = tc["function"]
                                call_fn = call_entry["function"]

                                if "name" in func_payload and func_payload["name"]:
                                    call_fn["name"] = func_payload["name"]

                                args_part = func_payload.get("arguments", "")

                                if not state["started"] and (call_fn["name"] or args_part):
                                    display_name = call_fn["name"] or "unknown"
                                    console.print(f"\nCalling Tool: {display_name}(", end="", markup=False)
                                    state["started"] = True

                                if args_part:
                                    call_fn["arguments"] += args_part
                                    console.print(args_part, end="", markup=False, highlight=False, soft_wrap=False)
                    console.file.flush()
            except KeyboardInterrupt:
                was_interrupted = True

            for idx, state in tool_print_state.items():
                if state["started"] and not state["closed"]:
                    console.print(")", markup=False)
                    state["closed"] = True

            if was_interrupted:
                console.print("— interrupted —", markup=False)

            console.print()

            # Timing + tokens
            dt = time.perf_counter() - t0
            completion_tok_est = approx_tokens_from_text(full_response_content)
            status = (
                f"⏱ {dt:.2f}s  |  in ≈ {prompt_tok_est} tok  |  out ≈ {completion_tok_est} tok  |  "
                f"{'(interrupted)' if was_interrupted else '(complete)'}"
            )
            console.print(status, markup=False)

            # History
            assistant_msg = {"role": "assistant", "content": (full_response_content or "").rstrip()}
            if was_interrupted:
                assistant_msg["content"] = (assistant_msg["content"] + "\n\n_[response interrupted by user]_").strip()

            if tool_calls_in_progress:
                assistant_msg["tool_calls"] = tool_calls_in_progress
            conversation_history.append(assistant_msg)

            if not tool_calls_in_progress or was_interrupted:
                break  # return to prompt

            # Execute tools and feed results
            section_title = "Tool Results"
            console.print(f"\n{section_title}", markup=False)
            console.print("-" * len(section_title), markup=False)
            tool_results = []
            for tc in tool_calls_in_progress:
                fname = tc["function"]["name"]
                tcall_id = tc["id"]
                args_str = tc["function"]["arguments"] or ""
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError as e:
                    err = f"Error decoding arguments for {fname}: {e}\nArguments received: {args_str}"
                    console.print(f"Argument Error: {err}", markup=False)
                    tool_results.append({"tool_call_id": tcall_id, "role": "tool", "name": fname, "content": err})
                    continue

                try:
                    t_tool0 = time.perf_counter()
                    result = tool_executor.execute_tool(fname, **args)
                    t_tool = time.perf_counter() - t_tool0

                    # result is a dict: {"model": "...", "display": "..."}
                    model_content = result.get("model", "")
                    display_content = result.get("display", model_content)

                    header = f"Tool Result: {fname} ({t_tool:.2f}s)"
                    console.print(header, markup=False)
                    _render_markdown(console, display_content)
                    console.print()

                    # Push the model content into conversation for the LLM
                    tool_results.append({"tool_call_id": tcall_id, "role": "tool", "name": fname, "content": model_content})
                except Exception as e:
                    err = f"Error executing tool {fname}: {e}"
                    console.print(f"Execution Error: {err}", markup=False)
                    tool_results.append({"tool_call_id": tcall_id, "role": "tool", "name": fname, "content": err})

            conversation_history.extend(tool_results)
            # Loop so the model can react to tool outputs

    console.print("\n[bold red]Exiting.[/bold red]")


if __name__ == "__main__":
    main()