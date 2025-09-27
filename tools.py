# tools.py

import subprocess
import json
import os

def read_file(file_path: str, start_line: int = None, end_line: int = None) -> str:
    """
    Reads the content of a file, optionally from a specific start to end line.
    Line numbers in the output are 1-based and correspond to the original file.
    """
    if not os.path.exists(file_path):
        return f"### Error Reading File\nFile not found at '{file_path}'."
    
    # --- Input Validation ---
    if start_line is not None and start_line <= 0:
        return "### Error Reading File\n`start_line` must be a positive number."
    if end_line is not None and end_line <= 0:
        return "### Error Reading File\n`end_line` must be a positive number."
    if start_line and end_line and start_line > end_line:
        return f"### Error Reading File\n`start_line` ({start_line}) cannot be greater than `end_line` ({end_line})."

    try:
        with open(file_path, 'r') as f:
            all_lines = f.readlines()
        
        total_lines = len(all_lines)
        
        # Determine the slice range (0-based for Python lists)
        start_index = (start_line - 1) if start_line else 0
        end_index = end_line if end_line else total_lines
        
        # Clamp the range to the actual file size to prevent errors
        start_index = max(0, start_index)
        end_index = min(total_lines, end_index)

        lines_to_show = all_lines[start_index:end_index]

        if not lines_to_show:
            return f"### Content of `{file_path}`\n(No content in the specified range: {start_line}-{end_line})"

        max_line_number_width = len(str(end_index))
        
        formatted_lines = []
        # Enumerate starting from the actual start line number for correct display
        for i, line in enumerate(lines_to_show, start=start_index + 1):
            line_number = str(i).rjust(max_line_number_width)
            formatted_lines.append(f"{line_number}: {line.rstrip()}")
        
        content_with_lines = "\n".join(formatted_lines)
        
        # Create a dynamic header
        if start_line or end_line:
            header = f"### Showing lines {start_index + 1}-{end_index} of `{file_path}` (Total: {total_lines} lines)"
        else:
            header = f"### Content of `{file_path}` (Total: {total_lines} lines)"

        return f"{header}\n```\n{content_with_lines}\n```"

    except Exception as e:
        return f"### Error Reading File\nAn unexpected error occurred: {str(e)}"

# ... (the rest of tools.py remains the same) ...
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
    """
    Applies a patch to a file to add, remove, or modify its content.

    The patch format is a simplified diff-like format where each line starts
    with '+', '-', or ' ' to indicate addition, removal, or context, respectively.

    Args:
        file_path: The path to the file to be patched.
        patch: A string representing the patch to apply.
               For example, to replace 'old_line' with 'new_line', the patch
               string would be '-old_line\\n+new_line'.

    Returns:
        A success message if the patch is applied, or an error message otherwise.
    """
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