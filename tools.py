# tools.py

import subprocess
import json
import os

def read_file(file_path: str) -> str:
    """
    Reads the content of a file and returns it with line numbers in a formatted Markdown string.
    """
    if not os.path.exists(file_path):
        return f"### Error Reading File\nFile not found at '{file_path}'."
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
        
        if not lines:
            return f"### Content of `{file_path}`\n(File is empty)"

        # Calculate the padding needed for the line numbers for clean alignment
        max_line_number_width = len(str(len(lines)))
        
        # Format each line with its number
        formatted_lines = []
        for i, line in enumerate(lines):
            # Format: "  1: content of line 1"
            # rstrip() removes the original newline character from the read line
            line_number = str(i + 1).rjust(max_line_number_width)
            formatted_lines.append(f"{line_number}: {line.rstrip()}")
        
        content_with_lines = "\n".join(formatted_lines)
        
        return f"### Content of `{file_path}`\n```\n{content_with_lines}\n```"
    except Exception as e:
        return f"### Error Reading File\nAn unexpected error occurred: {str(e)}"

def _format_shell_output(result: subprocess.CompletedProcess) -> str:
    """Formats the result of a subprocess command into a structured Markdown string."""
    if result.returncode == 0:
        output = "### Shell Command Successful\n"
        if result.stdout:
            output += f"#### STDOUT\n```\n{result.stdout.strip()}\n```\n"
        if result.stderr:
            output += f"#### STDERR\n```\n{result.stderr.strip()}\n```\n"
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
    """Executes a shell command and returns a formatted Markdown string of the output."""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        return _format_shell_output(result)
    except subprocess.TimeoutExpired:
        return "### Shell Command FAILED\nError: Command timed out after 30 seconds."
    except Exception as e:
        return f"### Shell Command FAILED\nError: An unexpected error occurred: {str(e)}"

def file_patch(file_path: str, patch: str) -> str:
    """Applies a patch to a file and returns a simple success or error message."""
    if not patch or not patch.strip():
        return "### Error Patching File\nThe 'patch' argument cannot be empty. No changes were made."

    if not os.path.exists(file_path):
        return f"### Error Patching File\nFile not found at '{file_path}'."
    try:
        with open(file_path, 'r') as f:
            original_lines = f.readlines()

        patched_lines = []
        original_line_index = 0
        patch_lines = patch.splitlines()
        patch_lines = [line for line in patch_lines if not (line.startswith('---') or line.startswith('+++') or line.startswith('@@'))]

        for line in patch_lines:
            if line.startswith('+'):
                patched_lines.append(line[1:] + '\n')
            elif line.startswith('-'):
                original_line_index += 1
            else: 
                if original_line_index < len(original_lines):
                    patched_lines.append(original_lines[original_line_index])
                    original_line_index += 1

        with open(file_path, 'w') as f:
            f.writelines(patched_lines)
            
        return f"File patched successfully: '{file_path}'."
    except Exception as e:
        return f"### Error Patching File\nAn unexpected error occurred while patching '{file_path}': {str(e)}"

AVAILABLE_TOOLS = {
    "read_file": read_file,
    "shell": shell,
    "file_patch": file_patch,
}