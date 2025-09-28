import json
import platform
import requests
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from harmony import create_system_message, create_developer_message
from tools import ToolExecutor

# --- Constants and Global Setup ---
API_URL = "http://localhost:8080/v1/chat/completions"

def stream_model_response(messages, tools):
    """
    Send the conversation to the API and yield each streaming chunk (SSE).
    """
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": "gpt-oss",
        "messages": messages,
        "tools": tools,
        "stream": True,
    }

    try:
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
                try:
                    yield json.loads(body)
                except json.JSONDecodeError:
                    print(f"\n[Bad JSON chunk ignored] {body}")
    except requests.exceptions.RequestException as e:
        print(f"\n[Error connecting to the model API: {e}]")


def main():
    console = Console()
    conversation_history = []
    tool_executor = ToolExecutor()

    if platform.system() == "Windows":
        shell_name = "Command Prompt"
        shell_example = "Example: `dir`"
    else:
        shell_name = "bash"
        shell_example = "Example: `ls -l`"

    # ---- Exactly three tools, concise & explicit schemas ----
    tools_definition = [
        {
            "type": "function",
            "function": {
                "name": "python",
                "description": "Execute Python code. Large outputs are cached automatically.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code":    {"type": "string",  "description": "Python source to run."},
                        "timeout": {"type": "integer", "description": "Seconds before kill.", "default": 30}
                    },
                    "required": ["code"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": f"Execute a {shell_name} command. Large outputs are cached automatically. {shell_example}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string",  "description": "Command to run."},
                        "timeout": {"type": "integer", "description": "Seconds before kill.", "default": 30}
                    },
                    "required": ["command"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "cache",
                "description": "View, inspect, or drop cached outputs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["view", "info", "drop"],
                            "description": "Operation to perform."
                        },
                        "cache_id": {"type": "string", "description": "Cache entry ID."},

                        # For action='view'
                        "mode": {
                            "type": "string",
                            "enum": ["lines", "chars", "info"],
                            "description": "How to view cached output.",
                            "default": "lines"
                        },
                        "start": {"type": "integer", "description": "Start index (0-based)."},
                        "count": {"type": "integer", "description": "Number of lines/chars to return."},
                        "context_before": {"type": "integer", "description": "Lines mode: extra lines before.", "default": 0},
                        "context_after":  {"type": "integer", "description": "Lines mode: extra lines after.",  "default": 0},
                        "page": {"type": "integer", "description": "Lines mode: page index (0-based)."},
                        "page_size": {"type": "integer", "description": "Lines mode: page size."}
                    },
                    "required": ["action", "cache_id"]
                }
            }
        }
    ]

    instructions = "You are a helpful assistant that can execute code."
    system_message = create_system_message(tools_exist=True)
    developer_message = create_developer_message(instructions, tools_definition)

    # System + developer go in as user-visible content per Harmony format integration
    conversation_history.append({"role": "system", "content": system_message})
    conversation_history.append({"role": "user", "content": developer_message})

    console.print(Panel("[bold green]Harmony CLI Initialized[/bold green]\n\n[dim]System and developer messages have been pre-loaded.[/dim]\n[dim]Enter your first prompt or type 'exit' to quit.[/dim]"))

    while True:
        try:
            user_input = console.input("\n[bold cyan]You: [/bold cyan]")
            if user_input.lower() == "exit":
                break
        except (KeyboardInterrupt, EOFError):
            break

        conversation_history.append({"role": "user", "content": user_input})

        # Stream assistant, capture tool calls, then execute them, loop until final assistant text
        while True:
            full_response_content = ""
            tool_calls_in_progress = []
            tool_print_state = {}

            console.print("\n[bold yellow]Assistant:[/bold yellow] ", end="")

            for chunk in stream_model_response(conversation_history, tools_definition):
                if not chunk.get("choices"):
                    continue
                delta = chunk["choices"][0].get("delta", {})

                # Stream text
                if (txt := delta.get("content")):
                    full_response_content += txt
                    console.print(txt, end="", style="white", highlight=False)

                # Stream tool call assembly
                if "tool_calls" in delta and delta["tool_calls"]:
                    for tc in delta["tool_calls"]:
                        idx = tc["index"]
                        if len(tool_calls_in_progress) <= idx:
                            tool_calls_in_progress.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if idx not in tool_print_state:
                            tool_print_state[idx] = {"name_printed": False}

                        if "id" in tc:
                            tool_calls_in_progress[idx]["id"] = tc["id"]
                        if "function" in tc:
                            f = tool_calls_in_progress[idx]["function"]
                            if "name" in tc["function"]:
                                f["name"] = tc["function"]["name"]
                            if "arguments" in tc["function"]:
                                f["arguments"] += tc["function"]["arguments"]

                        if not tool_print_state[idx]["name_printed"] and tool_calls_in_progress[idx]["function"]["name"]:
                            console.print(f"\n\n[bold blue]Calling Tool:[/bold blue] {tool_calls_in_progress[idx]['function']['name']}(", end="", highlight=False)
                            tool_print_state[idx]["name_printed"] = True
                        if "function" in tc and "arguments" in tc["function"]:
                            console.print(tc["function"]["arguments"], end="", style="blue", highlight=False)

            for idx in sorted(tool_print_state.keys()):
                if tool_print_state[idx]["name_printed"]:
                    console.print(")", end="", style="blue")
            console.print()

            assistant_msg = {"role": "assistant", "content": full_response_content or None}
            if tool_calls_in_progress:
                assistant_msg["tool_calls"] = tool_calls_in_progress
            conversation_history.append(assistant_msg)

            if not tool_calls_in_progress:
                break  # final answer delivered

            # Execute tools and feed results
            console.rule("\n[bold blue]Tool Results[/bold blue]", style="blue")
            tool_results = []
            for tc in tool_calls_in_progress:
                fname = tc["function"]["name"]
                tcall_id = tc["id"]
                args_str = tc["function"]["arguments"] or ""
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError as e:
                    err = f"Error decoding arguments for {fname}: {e}\nArguments received: {args_str}"
                    console.print(Panel(err, title="[bold red]Argument Error[/bold red]", border_style="red"))
                    tool_results.append({"tool_call_id": tcall_id, "role": "tool", "name": fname, "content": err})
                    continue

                try:
                    tool_output = tool_executor.execute_tool(fname, **args)
                    console.print(Panel(Markdown(tool_output), title=f"[bold green]Tool Result: {fname}[/bold green]", border_style="green", expand=False))
                    tool_results.append({"tool_call_id": tcall_id, "role": "tool", "name": fname, "content": tool_output})
                except Exception as e:
                    err = f"Error executing tool {fname}: {e}"
                    console.print(Panel(err, title="[bold red]Execution Error[/bold red]", border_style="red"))
                    tool_results.append({"tool_call_id": tcall_id, "role": "tool", "name": fname, "content": err})

            conversation_history.extend(tool_results)
            # Loop again so the model can react to tool outputs

    console.print("\n[bold red]Exiting.[/bold red]")


if __name__ == "__main__":
    main()
