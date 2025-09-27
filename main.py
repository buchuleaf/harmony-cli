# cli.py

import json
import requests
from tools import AVAILABLE_TOOLS

# --- Rich UI Imports ---
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
from rich.markdown import Markdown
from rich.syntax import Syntax

API_URL = "http://localhost:8080/v1/chat/completions"

# --- Rich UI Setup ---
console = Console()

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
        {"type": "function", "function": {"name": "read_file", "description": "Reads the entire content of a specified file and returns it as a string.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "The path to the file to read."}}, "required": ["file_path"]}}},
        {"type": "function", "function": {"name": "shell", "description": "Executes a shell command.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "The command to execute."}}, "required": ["command"]}}},
        {"type": "function", "function": {"name": "file_patch", "description": "Applies a patch to a file to add, remove, or modify its content. Requires a non-empty patch argument.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "The path to the file to patch."}, "patch": {"type": "string", "description": "The patch content in a diff-like format. Must not be empty."}}, "required": ["file_path", "patch"]}}}
    ]

    console.print(Panel("GPT-OSS API CLI with Rich UI", style="bold green", expand=False))

    while True:
        try:
            user_input = console.input("[bold cyan]You: ")
            if user_input.lower() == 'exit':
                break
        except (KeyboardInterrupt, EOFError):
            break
            
        conversation_history.append({"role": "user", "content": user_input})

        with Live(console=console, auto_refresh=False) as live:
            assistant_panel = Panel("", title="[bold yellow]Assistant[/bold yellow]", border_style="yellow")
            live.update(assistant_panel, refresh=True)

            while True:
                full_response_content = ""
                tool_calls_in_progress = []

                for chunk in stream_model_response(conversation_history, tools_definition):
                    if not chunk.get("choices"):
                        continue
                    
                    delta = chunk["choices"][0].get("delta", {})

                    if "content" in delta and delta["content"]:
                        full_response_content += delta["content"]
                        # --- UPDATED: Render assistant's response as Markdown ---
                        assistant_panel.renderable = Markdown(full_response_content)
                        live.update(assistant_panel, refresh=True)
                    
                    if "tool_calls" in delta and delta["tool_calls"]:
                        for tool_call_chunk in delta["tool_calls"]:
                            index = tool_call_chunk["index"]
                            if len(tool_calls_in_progress) <= index:
                                tool_calls_in_progress.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if "id" in tool_call_chunk:
                                tool_calls_in_progress[index]["id"] = tool_call_chunk["id"]
                            if "function" in tool_call_chunk:
                                if "name" in tool_call_chunk["function"]:
                                    tool_calls_in_progress[index]["function"]["name"] = tool_call_chunk["function"]["name"]
                                if "arguments" in tool_call_chunk["function"]:
                                    tool_calls_in_progress[index]["function"]["arguments"] += tool_call_chunk["function"]["arguments"]
                
                assistant_message = {"role": "assistant", "content": full_response_content or None}
                if tool_calls_in_progress:
                    assistant_message["tool_calls"] = tool_calls_in_progress
                conversation_history.append(assistant_message)

                if tool_calls_in_progress:
                    live.stop()
                    for tool_call in tool_calls_in_progress:
                        function_name = tool_call["function"]["name"]
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                            tool_call_panel = Panel(
                                Syntax(json.dumps(args, indent=2), "json", theme="monokai", line_numbers=True),
                                title=f"[bold blue]Tool Call: {function_name}[/bold blue]",
                                border_style="blue",
                                expand=False
                            )
                            console.print(tool_call_panel)

                            tool_function = AVAILABLE_TOOLS[function_name]
                            tool_output = tool_function(**args)
                            
                            result_panel = Panel(
                                Markdown(tool_output),
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
                    
                    live.start()
                    assistant_panel = Panel(Markdown("..."), title="[bold yellow]Assistant[/bold yellow]", border_style="yellow")
                    live.update(assistant_panel, refresh=True)
                    continue

                break

    console.print("[bold red]Exiting.[/bold red]")

if __name__ == "__main__":
    main()