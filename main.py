# cli.py

import json
import requests
import platform
import uuid
from tools import AVAILABLE_TOOLS, _truncate_output, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

# --- Constants and Global Setup ---
API_URL = "http://localhost:8080/v1/chat/completions"
MAX_TOOL_OUTPUT_CHARS = 8000
console = Console()

# A simple in-memory cache for the current session
TOOL_OUTPUT_CACHE = {}


def stream_model_response(messages, tools):
    """
    Sends the conversation to the API and yields each chunk of the streaming response.
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
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        json_str = decoded_line[6:]
                        if json_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(json_str)
                            yield chunk
                        except json.JSONDecodeError:
                            console.print(f"\n[Error decoding JSON chunk: {json_str}]", style="bold red")
    except requests.exceptions.RequestException as e:
        console.print(f"\n[Error connecting to the model API: {e}]", style="bold red")


def main():
    # --- System Information and Dynamic Tool Configuration ---
    system = platform.system()
    if system == "Windows":
        os_name = "Windows"
        shell_name = "PowerShell"
        shell_examples = "e.g., `Get-ChildItem` to list files, `Select-String -Path path/to/file.txt -Pattern 'hello'` to search text."
    elif system == "Darwin":
        os_name = "macOS"
        shell_name = "bash"
        shell_examples = "e.g., `ls -l` to list files, `grep 'hello' path/to/file.txt` to search text."
    else:
        os_name = "Linux"
        shell_name = "bash"
        shell_examples = "e.g., `ls -l` to list files, `grep 'hello' path/to/file.txt` to search text."
    
    # --- Tool Definitions for the Model ---
    tools_definition = [
        {
            "type": "function",
            "function": {
                "name": "python",
                "description": "Executes Python code to perform tasks like file I/O, data manipulation, and complex logic. This is the primary tool for most operations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "The Python code to execute."}
                    },
                    "required": ["code"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": f"Executes a {shell_name} command as a fallback for tasks impossible in Python, like running external programs (`git`, `curl`), or managing system processes. {shell_examples}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The shell command to execute."}
                    },
                    "required": ["command"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "view_cached_output",
                "description": "Retrieves and displays a specific portion (a 'page') of a large tool output that was previously cached. Use this to browse or search through large results from `python` or `shell` commands.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cache_id": {"type": "string", "description": "The unique ID of the cached output to view."},
                        "start_line": {"type": "integer", "description": "The starting line number to retrieve (0-indexed). Defaults to 0."},
                        "line_count": {"type": "integer", "description": "The number of lines to retrieve. Defaults to 100."}
                    },
                    "required": ["cache_id"]
                }
            }
        }
    ]

    # --- System Prompt and Conversation History ---
    # --- CRITICAL FIX: OS-AWARE SYSTEM PROMPT ---
    if os_name == "Windows":
        system_prompt = f"""You are a terminal assistant in a {os_name} environment with a {shell_name} shell. You have three tools: `python`, `shell`, and `view_cached_output`.

FILE PATH RULES:
1. For the `python` tool, ALWAYS use forward slashes (`/`) for file paths, like `path/to/file.txt`.
2. For the `shell` tool, you MUST use backslashes (`\\`) for file paths, like `path\\to\\file.exe`. This is critical for Windows commands.

LARGE OUTPUT WORKFLOW:
- If a command's output is too large, it will be cached and you will receive a `cache_id`.
- You MUST use the `view_cached_output` tool with the `cache_id` to inspect the output in chunks."""
    else: # For Linux and macOS
        system_prompt = f"""You are a terminal assistant in a {os_name} environment with a {shell_name} shell. You have three tools: `python`, `shell`, and `view_cached_output`.

FILE PATH RULE:
- For ALL tools (`python` and `shell`), you MUST use forward slashes (`/`) for file paths, like `path/to/file.txt`.

LARGE OUTPUT WORKFLOW:
- If a command's output is too large, it will be cached and you will receive a `cache_id`.
- You MUST use the `view_cached_output` tool with the `cache_id` to inspect the output in chunks."""


    conversation_history = [{"role": "system", "content": system_prompt}]
    
    console.print(Panel("GPT-OSS API CLI", style="bold green", expand=False))

    # --- Main Conversation Loop ---
    while True:
        try:
            user_input = console.input("\n[bold cyan]You: ")
            if user_input.lower() == 'exit':
                break
        except (KeyboardInterrupt, EOFError):
            break
            
        conversation_history.append({"role": "user", "content": user_input})

        while True:
            full_response_content = ""
            tool_calls_in_progress = []
            tool_print_state = {}

            console.print("\n[bold yellow]Assistant:[/bold yellow]", end=" ")
            
            for chunk in stream_model_response(conversation_history, tools_definition):
                if not chunk.get("choices"): continue
                delta = chunk["choices"][0].get("delta", {})

                if "content" in delta and delta["content"]:
                    content_chunk = delta["content"]
                    full_response_content += content_chunk
                    console.print(content_chunk, end="", style="white", highlight=False)
                
                if "tool_calls" in delta and delta["tool_calls"]:
                    for tool_call_chunk in delta["tool_calls"]:
                        index = tool_call_chunk["index"]
                        
                        if len(tool_calls_in_progress) <= index:
                            tool_calls_in_progress.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if index not in tool_print_state:
                            tool_print_state[index] = {"name_printed": False}

                        if "id" in tool_call_chunk: tool_calls_in_progress[index]["id"] = tool_call_chunk["id"]
                        if "function" in tool_call_chunk:
                            if "name" in tool_call_chunk["function"]: tool_calls_in_progress[index]["function"]["name"] = tool_call_chunk["function"]["name"]
                            if "arguments" in tool_call_chunk["function"]: tool_calls_in_progress[index]["function"]["arguments"] += tool_call_chunk["function"]["arguments"]

                        if not tool_print_state[index]["name_printed"] and tool_calls_in_progress[index]["function"]["name"]:
                            console.print(f"\n\n[bold blue]Calling Tool:[/bold blue] {tool_calls_in_progress[index]['function']['name']}(", end="", highlight=False)
                            tool_print_state[index]["name_printed"] = True

                        if "function" in tool_call_chunk and "arguments" in tool_call_chunk["function"]:
                            console.print(tool_call_chunk["function"]["arguments"], end="", style="blue", highlight=False)

            for index in sorted(tool_print_state.keys()):
                if tool_print_state[index]["name_printed"]:
                    console.print(")", end="", style="blue")
            
            console.print()

            assistant_message = {"role": "assistant", "content": full_response_content or None}
            if tool_calls_in_progress:
                assistant_message["tool_calls"] = tool_calls_in_progress
            conversation_history.append(assistant_message)

            if tool_calls_in_progress:
                console.rule("\n[bold blue]Tool Results[/bold blue]", style="blue")
                for tool_call in tool_calls_in_progress:
                    function_name = tool_call["function"]["name"]
                    try:
                        args_str = tool_call["function"]["arguments"]
                        if not args_str:
                             console.print(Panel(f"Error executing tool {function_name}: Arguments are empty.", title="[bold red]Error[/bold red]", border_style="red"))
                             continue
                        
                        args = json.loads(args_str)
                        tool_function = AVAILABLE_TOOLS[function_name]
                        
                        if function_name == "view_cached_output":
                            tool_output = tool_function(cache=TOOL_OUTPUT_CACHE, **args)
                        else:
                            tool_output = tool_function(**args)
                        
                        model_content = tool_output
                        display_output = tool_output

                        if function_name in ["python", "shell"] and len(tool_output) > MAX_TOOL_OUTPUT_CHARS:
                            cache_id = str(uuid.uuid4())
                            TOOL_OUTPUT_CACHE[cache_id] = tool_output
                            
                            lines = tool_output.splitlines()
                            total_lines = len(lines)
                            
                            model_content = (
                                f"## Command Successful, Output Too Large\n"
                                f"The full output is {total_lines} lines long and has been cached.\n"
                                f"Cache ID: '{cache_id}'\n"
                                f"Instruct the user that the output is large and use the `view_cached_output` tool with this ID to inspect it."
                            )
                            
                            display_output = (
                                f"Output is too large ({total_lines} lines) and has been cached for the model to browse.\n"
                                f"Cache ID: {cache_id}\n\n"
                                f"--- Start of Output ---\n" +
                                _truncate_output(tool_output, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH)
                            )

                        if display_output is tool_output:
                             display_output = _truncate_output(tool_output, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH)

                        markdown_output = Markdown(display_output)
                        console.print(Panel(markdown_output, title=f"[bold green]Tool Result: {function_name}[/bold green]", border_style="green", expand=False))

                        conversation_history.append({
                            "tool_call_id": tool_call["id"],
                            "role": "tool",
                            "name": function_name,
                            "content": model_content,
                        })
                    except json.JSONDecodeError as e:
                        console.print(Panel(f"Error decoding arguments for {function_name}: {e}\nArguments received: {args_str}", title="[bold red]Argument Error[/bold red]", border_style="red"))
                    except Exception as e:
                        console.print(Panel(f"Error executing tool {function_name}: {e}", title="[bold red]Execution Error[/bold red]", border_style="red"))
                
                continue

            break

    console.print("\n[bold red]Exiting.[/bold red]")


if __name__ == "__main__":
    main()