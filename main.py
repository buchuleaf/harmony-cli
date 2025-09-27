# cli.py

import json
import requests
from tools import AVAILABLE_TOOLS
from rich.console import Console
from rich.panel import Panel

# --- Constants and Global Setup ---
API_URL = "http://localhost:8080/v1/chat/completions"
MAX_TOOL_OUTPUT_LINES = 10
console = Console()


def truncate_for_display(output: str, max_lines: int) -> str:
    """
    Truncates a string to a maximum number of lines for cleaner display.
    """
    lines = output.splitlines()
    if len(lines) > max_lines:
        truncated_content = "\n".join(lines[:max_lines])
        omitted_lines = len(lines) - max_lines
        truncation_message = f"\n... (output truncated, {omitted_lines} more lines hidden) ..."
        return truncated_content + truncation_message
    return output


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
    conversation_history = []
    
    tools_definition = [
        {"type": "function", "function": {"name": "read_file", "description": "Reads the content of a file. Can read the entire file or a specific range of lines.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "The path to the file to read."}, "start_line": {"type": "integer", "description": "Optional. The 1-based line number to start reading from."}, "end_line": {"type": "integer", "description": "Optional. The 1-based line number to stop reading at (inclusive)."}}, "required": ["file_path"]}}},
        {"type": "function", "function": {"name": "shell", "description": "Executes a shell command.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "The command to execute."}}, "required": ["command"]}}},
        {"type": "function", "function": {"name": "file_patch", "description": "Applies a patch to a file to add, remove, or modify its content using a diff-like format. This is useful for making specific changes to a file without rewriting the entire file.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "The path to the file to patch."}, "patch": {"type": "string", "description": "The patch content. Each line should start with a `+` for additions, `-` for removals, or a space for context. For example, to replace the line 'old_line' with 'new_line', the patch would be '-old_line\\n+new_line'."}}, "required": ["file_path", "patch"]}}}
    ]

    console.print(Panel("GPT-OSS API CLI (Stable UI)", style="bold green", expand=False))

    # --- Main Conversation Loop ---
    while True:
        try:
            user_input = console.input("\n[bold cyan]You: ")
            if user_input.lower() == 'exit':
                break
        except (KeyboardInterrupt, EOFError):
            break
            
        conversation_history.append({"role": "user", "content": user_input})

        # --- Model Response Loop ---
        # This loop continues until the model provides a text response without calling a tool.
        while True:
            # --- State Initialization for each response ---
            full_response_content = ""
            tool_calls_in_progress = []
            # Tracks which parts of a tool call have been printed to avoid duplicates.
            tool_print_state = {}  # {index: {"name_printed": bool}}

            console.print("\n[bold yellow]Assistant:[/bold yellow]", end=" ")
            
            # --- Stream and process the model's response chunk by chunk ---
            for chunk in stream_model_response(conversation_history, tools_definition):
                if not chunk.get("choices"):
                    continue
                
                delta = chunk["choices"][0].get("delta", {})

                # --- Stream Text Content ---
                if "content" in delta and delta["content"]:
                    content_chunk = delta["content"]
                    full_response_content += content_chunk
                    console.print(content_chunk, end="", style="white", highlight=False)
                
                # --- Assemble and Stream Tool Calls ---
                if "tool_calls" in delta and delta["tool_calls"]:
                    for tool_call_chunk in delta["tool_calls"]:
                        index = tool_call_chunk["index"]
                        
                        # Initialize state for a new tool call
                        if len(tool_calls_in_progress) <= index:
                            tool_calls_in_progress.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if index not in tool_print_state:
                            tool_print_state[index] = {"name_printed": False}

                        # Update the tool call object with the new data
                        if "id" in tool_call_chunk:
                            tool_calls_in_progress[index]["id"] = tool_call_chunk["id"]
                        if "function" in tool_call_chunk:
                            if "name" in tool_call_chunk["function"]:
                                tool_calls_in_progress[index]["function"]["name"] = tool_call_chunk["function"]["name"]
                            if "arguments" in tool_call_chunk["function"]:
                                tool_calls_in_progress[index]["function"]["arguments"] += tool_call_chunk["function"]["arguments"]

                        # --- Stream the tool call's visual representation ---
                        # Print the function name and opening parenthesis once
                        if not tool_print_state[index]["name_printed"] and tool_calls_in_progress[index]["function"]["name"]:
                            console.print(f"\n\n[bold blue]Calling Tool:[/bold blue] {tool_calls_in_progress[index]['function']['name']}(", end="", highlight=False)
                            tool_print_state[index]["name_printed"] = True

                        # Stream the arguments as they arrive
                        if "function" in tool_call_chunk and "arguments" in tool_call_chunk["function"]:
                            console.print(tool_call_chunk["function"]["arguments"], end="", style="blue", highlight=False)

            # --- Finalize Streaming Output ---
            # Close any open tool call parentheses
            for index in sorted(tool_print_state.keys()):
                if tool_print_state[index]["name_printed"]:
                    console.print(")", end="", style="blue")
            
            console.print()  # Print a final newline for clean separation

            # --- Prepare and store the assistant's message ---
            assistant_message = {"role": "assistant", "content": full_response_content or None}
            if tool_calls_in_progress:
                assistant_message["tool_calls"] = tool_calls_in_progress
            conversation_history.append(assistant_message)

            # --- Execute Tools if any were called ---
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
                        tool_output = tool_function(**args)
                        display_output = truncate_for_display(tool_output, MAX_TOOL_OUTPUT_LINES)

                        console.print(Panel(display_output, title=f"\n[bold green]Tool Result: {function_name}[/bold green]", border_style="green", expand=False))

                        conversation_history.append({
                            "tool_call_id": tool_call["id"],
                            "role": "tool",
                            "name": function_name,
                            "content": tool_output,
                        })
                    except json.JSONDecodeError as e:
                        console.print(Panel(f"Error decoding arguments for {function_name}: {e}\nArguments received: {args_str}", title="[bold red]Argument Error[/bold red]", border_style="red"))
                    except Exception as e:
                        console.print(Panel(f"Error executing tool {function_name}: {e}", title="[bold red]Execution Error[/bold red]", border_style="red"))
                
                # Loop back to the model with the tool results
                continue

            # If no tools were called, break the inner loop and wait for user input
            break

    console.print("\n[bold red]Exiting.[/bold red]")


if __name__ == "__main__":
    main()