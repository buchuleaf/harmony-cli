# tools.py

import subprocess
import json
import os

def _format_shell_output(result: subprocess.CompletedProcess) -> str:
    """Formats the result of a subprocess command into a structured Markdown string."""
    if result.returncode == 0:
        output = "### Shell Command Successful\n"
        if result.stdout:
            output += f"#### STDOUT\n```\n{result.stdout.strip()}\n```\n"
        if result.stderr:
            # Some successful commands still print to stderr (e.g., warnings)
            output += f"#### STDERR\n```\n{result.stderr.strip()}\n```\n"
        # If there's no output at all, state that.
        if not result.stdout and not result.stderr:
            output += "The command produced no output.\n"
    else:
        output = f"### Shell Command FAILED (Exit Code: {result.returncode})\n"
        if result.stderr:
            output += f"#### STDERR\n```\n{result.stderr.strip()}\n```\n"
        if result.stdout:
            output += f"#### STDOUT\n```\n{result.stdout.strip()}\n```\n"
        if not result.stdout and not result.stderr:
            output += "The command produced no output.\n"
            
    return output

def shell(command: str) -> str:
    """
    Executes a shell command and returns a formatted Markdown string of the output.
    """
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=30
        )
        return _format_shell_output(result)
    except subprocess.TimeoutExpired:
        return "### Shell Command FAILED\nError: Command timed out after 30 seconds."
    except Exception as e:
        return f"### Shell Command FAILED\nError: An unexpected error occurred: {str(e)}"

def file_patch(file_path: str, patch: str) -> str:
    """
    Applies a patch to a file and returns a simple success or error message.
    """
    if not os.path.exists(file_path):
        return f"Error patching file: File not found at '{file_path}'."
    try:
        # NOTE: This remains a simplified patch logic for demonstration.
        # A real implementation should use a robust patching library.
        with open(file_path, 'r') as f:
            original_lines = f.readlines()

        patched_lines = []
        original_line_index = 0
        patch_lines = patch.splitlines()

        # Skip header lines
        patch_lines = [line for line in patch_lines if not (line.startswith('---') or line.startswith('+++') or line.startswith('@@'))]

        for line in patch_lines:
            if line.startswith('+'):
                patched_lines.append(line[1:] + '\n')
            elif line.startswith('-'):
                # This simple logic assumes the line to remove exists.
                original_line_index += 1
            else: # Context line
                if original_line_index < len(original_lines):
                    patched_lines.append(original_lines[original_line_index])
                    original_line_index += 1

        with open(file_path, 'w') as f:
            f.writelines(patched_lines)
            
        return f"File patched successfully: '{file_path}'."
    except Exception as e:
        return f"Error patching file '{file_path}': {str(e)}"

# Dictionary to map tool names to functions
AVAILABLE_TOOLS = {
    "shell": shell,
    "file_patch": file_patch,
}