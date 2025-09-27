# cli.py

import json
import requests
from tools import AVAILABLE_TOOLS

# --- Rich UI Imports ---
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.live import Live
from rich.console import Group

API_URL = "http://localhost:8080/v1/chat/completions"
MAX_TOOL_OUTPUT_LINES = 10

# --- Rich UI Setup ---
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

    while True:
        try:
            user_input = console.input("[bold cyan]You: ")
            if user_input.lower() == 'exit':
                break
        except (KeyboardInterrupt, EOFError):
            break
            
        conversation_history.append({"role": "user", "content": user_input})

        while True:
            full_response_content = ""
            tool_calls_in_progress = []
            
            console.rule("[bold yellow]Assistant[/bold yellow]", style="yellow")
            
            # Start with a single Markdown object for the text content
            renderables = [Markdown(full_response_content, style="white")]

            with Live(Group(*renderables), console=console, auto_refresh=False) as live:
                for chunk in stream_model_response(conversation_history, tools_definition):
                    if not chunk.get("choices"):
                        continue
                    
                    delta = chunk["choices"][0].get("delta", {})

                    if "content" in delta and delta["content"]:
                        content_chunk = delta["content"]
                        full_response_content += content_chunk
                        renderables[0] = Markdown(full_response_content, style="white")
                    
                    if "tool_calls" in delta and delta["tool_calls"]:
                        for tool_call_chunk in delta["tool_calls"]:
                            index = tool_call_chunk["index"]
                            
                            # --- Assemble the tool call object ---
                            if len(tool_calls_in_progress) <= index:
                                tool_calls_in_progress.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if "id" in tool_call_chunk:
                                tool_calls_in_progress[index]["id"] = tool_call_chunk["id"]
                            if "function" in tool_call_chunk:
                                if "name" in tool_call_chunk["function"]:
                                    tool_calls_in_progress[index]["function"]["name"] = tool_call_chunk["function"]["name"]
                                if "arguments" in tool_call_chunk["function"]:
                                    tool_calls_in_progress[index]["function"]["arguments"] += tool_call_chunk["function"]["arguments"]

                            # --- Update the renderable for this tool call ---
                            renderable_index = index + 1
                            if len(renderables) <= renderable_index:
                                renderables.append(Panel("", title="[bold blue]Tool Call[/bold blue]", border_style="blue"))
                            
                            tc = tool_calls_in_progress[index]
                            func_name = tc['function']['name']
                            func_args = tc['function']['arguments']
                            # Use Syntax for a nicely formatted tool call
                            tool_code = f"{func_name}({func_args})"
                            renderables[renderable_index] = Panel(
                                Syntax(tool_code, "python", theme="monokai", line_numbers=False),
                                title=f"[bold blue]Calling Tool: {func_name}[/bold blue]",
                                border_style="blue"
                            )

                    # Update the live display with the new group of renderables
                    live.update(Group(*renderables), refresh=True)

            assistant_message = {"role": "assistant", "content": full_response_content or None}
            if tool_calls_in_progress:
                assistant_message["tool_calls"] = tool_calls_in_progress
            conversation_history.append(assistant_message)

            if tool_calls_in_progress:
                for tool_call in tool_calls_in_progress:
                    function_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        
                        tool_function = AVAILABLE_TOOLS[function_name]
                        tool_output = tool_function(**args)
                        
                        display_output = truncate_for_display(tool_output, MAX_TOOL_OUTPUT_LINES)
                        
                        result_panel = Panel(
                            Markdown(display_output),
                            title=f"[bold green]Tool Result: {function_name}[/bold green]",
                            border_style="green",
                            expand=False
                        )
                        console.print(result_panel)

                        conversation_history.append({
                            "tool_call_id": tool_call["id"],
                            "role": "tool",
                            "name": function_name,
                            "content": tool_output,
                        })
                    except Exception as e:
                        console.print(Panel(f"Error executing tool {function_name}: {e}", title="[bold red]Error[/bold red]", border_style="red"))
                
                continue

            break

    console.print("[bold red]Exiting.[/bold red]")

if __name__ == "__main__":
    main()