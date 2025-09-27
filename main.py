# cli.py

import json
import requests
import platform
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

# --- Constants and Global Setup ---
API_URL = "http://localhost:8080/v1/chat/completions"

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
    # --- Tool Definitions for the Model ---
    # This remains as JSON because it's passed directly to the OpenAI-compatible API.
    # Our harmony.py library will convert this into the text format for the developer message.
    tools_definition = [
        {
            "type": "function", "function": {
                "name": "python",
                "description": "Executes Python code for file I/O, data manipulation, and logic. NOTE: If the output is very large, it will be cached and a cache_id will be returned.",
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
            "type": "function", "function": {
                "name": "shell",
                "description": f"Executes a {shell_name} command for tasks like running external programs. NOTE: If the output is very large, it will be cached and a cache_id will be returned. {shell_examples}",
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
            "type": "function", "function": {
                "name": "view_cached_output",
                "description": "Retrieve a portion of large, cached output from a previous `python` or `shell` call. Choose exactly one mode: line mode OR char mode.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cache_id":   {"type": "string",  "description": "Unique ID of the cached output."},

                        # Line-mode params
                        "start_line":   {"type": "integer", "description": "Start line (0-indexed). Use with line_count only."},
                        "line_count":   {"type": "integer", "description": "Number of lines to retrieve (>0)."},
                        "before_lines": {"type": "integer", "description": "Optional extra context lines to include BEFORE the window (>=0)."},
                        "after_lines":  {"type": "integer", "description": "Optional extra context lines to include AFTER the window (>=0)."},

                        # Char-mode params
                        "start_char": {"type": "integer", "description": "Start character offset (0-indexed). Use with char_count only."},
                        "char_count": {"type": "integer", "description": "Number of characters to retrieve (>0)."}
                    },
                    "required": ["cache_id"]
                }
            }
        },
        {
            "type": "function", "function": {
                "name": "get_cache_info",
                "description": "Inspect a cached output: returns total lines, total chars, and a short preview of the first 10 lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cache_id": {"type": "string", "description": "Unique ID of the cached output."}
                    },
                    "required": ["cache_id"]
                }
            }
        },
        {
            "type": "function", "function": {
                "name": "drop_cache",
                "description": "Delete a cached output to free memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cache_id": {"type": "string", "description": "Unique ID of the cached output."}
                    },
                    "required": ["cache_id"]
                }
            }
        }
    ]

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
            
            # The API call uses the full history, which now includes our Harmony messages.
            for chunk in stream_model_response(conversation_history, tools_definition):
                if not chunk.get("choices"):
                    continue
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

                        if "id" in tool_call_chunk:
                            tool_calls_in_progress[index]["id"] = tool_call_chunk["id"]
                        if "function" in tool_call_chunk:
                            if "name" in tool_call_chunk["function"]:
                                tool_calls_in_progress[index]["function"]["name"] = tool_call_chunk["function"]["name"]
                            if "arguments" in tool_call_chunk["function"]:
                                tool_calls_in_progress[index]["function"]["arguments"] += tool_call_chunk["function"]["arguments"]

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

                        if function_name in ["python", "shell"] and len(tool_output) > MAX_TOOL_OUTPUT_CHARS:
                            cache_id = str(uuid.uuid4())
                            TOOL_OUTPUT_CACHE[cache_id] = tool_output
                            
                            total_lines = len(tool_output.splitlines())
                            
                            model_content = (
                                f"## Command Successful, Output Too Large\n"
                                f"The full output is {total_lines} lines long and has been cached.\n"
                                f"Cache ID: '{cache_id}'\n"
                                f"You MUST now use the `view_cached_output` tool with this ID to inspect the output."
                            )
                            
                            display_output = (
                                f"Output is too large ({total_lines} lines) and has been cached for the model to browse.\n"
                                f"Cache ID: {cache_id}\n\n"
                                f"--- Start of Output ---\n" +
                                _truncate_output(tool_output, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH)
                            )
                        else:
                            display_output = _truncate_output(tool_output, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH)

                        markdown_output = Markdown(display_output)
                        console.print(Panel(markdown_output, title=f"[bold green]Tool Result: {function_name}[/bold green]", border_style="green", expand=False))
                        
                        # The content of a tool message must be a JSON-encoded string.
                        conversation_history.append({
                            "tool_call_id": tool_call["id"],
                            "role": "tool",
                            "name": function_name,
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
