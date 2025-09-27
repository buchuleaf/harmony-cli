# cli.py

import json
import re
import requests
from tools import AVAILABLE_TOOLS

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
                            print(f"\n[Error decoding JSON chunk: {json_str}]")
    except requests.exceptions.RequestException as e:
        print(f"\n[Error connecting to the model API: {e}]")

def main():
    conversation_history = []
    
    tools_definition = [
        {"type": "function", "function": {"name": "shell", "description": "Executes a shell command.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "The command to execute."}}, "required": ["command"]}}},
        {"type": "function", "function": {"name": "file_patch", "description": "Applies a patch to a file.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "The path to the file to patch."}, "patch": {"type": "string", "description": "The patch content in diff format."}}, "required": ["file_path", "patch"]}}}
    ]

    print("GPT-OSS API CLI (Unfiltered Streaming)")
    print("Enter 'exit' to quit.")

    while True:
        user_input = input("You: ")
        if user_input.lower() == 'exit':
            break
            
        conversation_history.append({"role": "user", "content": user_input})
        print("Assistant: ", end="", flush=True)

        while True:
            full_response_content = ""
            tool_calls_in_progress = []

            for chunk in stream_model_response(conversation_history, tools_definition):
                # Gracefully handle chunks with an empty 'choices' list to prevent crashing
                if not chunk.get("choices"):
                    continue
                
                delta = chunk["choices"][0].get("delta", {})

                # Print every content chunk as it arrives, showing the raw Harmony format
                if "content" in delta and delta["content"]:
                    content_chunk = delta["content"]
                    full_response_content += content_chunk
                    print(content_chunk, end="", flush=True)
                
                # Assemble tool calls from chunks
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

            print() # Newline after streaming is complete

            # Add the raw, unfiltered assistant message to the conversation history
            assistant_message = {"role": "assistant", "content": full_response_content}
            if tool_calls_in_progress:
                assistant_message["tool_calls"] = tool_calls_in_progress
            conversation_history.append(assistant_message)

            # If tools were called, execute them
            if tool_calls_in_progress:
                print("Assistant: Executing tools...")
                for tool_call in tool_calls_in_progress:
                    function_name = tool_call["function"]["name"]
                    if function_name in AVAILABLE_TOOLS:
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                            print(f"  - Calling `{function_name}` with args: {args}")
                            tool_function = AVAILABLE_TOOLS[function_name]
                            tool_output = tool_function(**args)
                            
                            conversation_history.append({
                                "tool_call_id": tool_call["id"],
                                "role": "tool",
                                "name": function_name,
                                "content": tool_output,
                            })
                        except Exception as e:
                            print(f"Error executing tool {function_name}: {e}")
                    else:
                        print(f"Error: Model tried to call unknown tool '{function_name}'")
                
                print("Assistant: ", end="", flush=True) # Prepare for the next streaming response
                continue # Loop back to send tool results to the model

            # If no tools were called, the turn is over
            break

if __name__ == "__main__":
    main()