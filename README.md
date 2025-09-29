# Quickstart

> Make sure `llama-server` from `llama.cpp` is running on port `8080`. Override with `HARMONY_CLI_API_URL` if your endpoint lives elsewhere.

## Install locally

```bash
pip install .
```

## Install globally with pipx

```bash
pipx install .
```

## Run without installing

```bash
python -m harmony_cli
```

Once installed, launch the console with:

```bash
harmony
```

---

### 🔌 Requirements

- A local model endpoint compatible with `llama.cpp` **chat completions** streaming.
- Default target: `http://localhost:8080/v1/chat/completions`; override with `HARMONY_CLI_API_URL`.
- Transcripts export to `~/.harmony-cli/transcripts` by default; point `HARMONY_CLI_HOME` elsewhere if needed.

> **Tip:** Start `llama-server` from `llama.cpp` on port **8080** before running the console.

### 🚀 Jacking In

```bash
uv venv
source .venv/bin/activate
pip install .

# Launch the console
harmony
```

### 📦 Distribution Builds

- Build a wheel + source distribution: `python -m build`.
- Create a standalone binary (uses PyInstaller):
  ```bash
  pip install pyinstaller
  scripts/build_exe.sh
  # Result: dist/harmony (single-file executable)
  ```

### 💻 Operating

Once inside, the system listens. It executes.

```text
┌─────────────────────────────────────────┐
│ GPT-OSS API CLI (Stable UI)             │
└─────────────────────────────────────────┘

You: Read the first 5 lines of src/harmony_cli/tools.py, then run `python --version`.

Assistant: Accessing...
Calling Tool: read_file({"file_path": "src/harmony_cli/tools.py", "end_line": 5})
Calling Tool: shell({"command": "python --version"})
```

After tools finish, the results are displayed in green panels. Long outputs are truncated with an inline “(output truncated …)” footer; full content is still passed back to the model.

### 🧰 The Arsenal (Built‑ins)

#### `shell`

Execute any command your terminal understands. Output and errors are formatted into separate **STDOUT/STDERR** sections and time out safely.

**Example**

> You: what processes are running?
>
> Assistant: `shell("ps aux")`

#### `read_file`

Read entire files or just a slice — with **1‑based** line numbers and an auto‑generated header showing the window.

**Example**

> You: show me lines 1–40 of `src/harmony_cli/cli.py`
>
> Assistant: `read_file("src/harmony_cli/cli.py", start_line=1, end_line=40)`

#### `file_patch`

Apply a **diff‑style** patch inline — add (`+`), remove (`-`), or keep context (` `). Ideal for surgical edits without opening an editor.

**Example**

> You: in `src/harmony_cli/cli.py`, change the API port from 8080 → 9000
>
> Assistant:
>
> ```
> file_patch("src/harmony_cli/cli.py", "-API_URL = \"http://localhost:8080/v1/chat/completions\"\n+API_URL = \"http://localhost:9000/v1/chat/completions\"")
> ```

**Patch rules (quick)**

* Start each changed line with `+` (add) or `-` (remove). Lines without a prefix are treated as context.
* File is rewritten from the original with your directives; keep enough context to avoid accidental deletions.

### 🧩 Becoming the Architect

Extend the console with your own tools in three steps.

1. **Forge the Tool** — add a Python function in `src/harmony_cli/tools.py`:

```python
# src/harmony_cli/tools.py
def get_system_load() -> str:
    import os
    l1, l5, l15 = os.getloadavg()
    return f"Load Average: {l1}, {l5}, {l15}"
```

2. **Register It** — add to `AVAILABLE_TOOLS`:

```python
AVAILABLE_TOOLS = {
    # ... existing tools
    "get_system_load": get_system_load,
}
```

3. **Brief the Model** — declare a tool schema in `src/harmony_cli/cli.py` `tools_definition`:

```python
{
  "type": "function",
  "function": {
    "name": "get_system_load",
    "description": "Checks the current system load average.",
    "parameters": {"type": "object", "properties": {}}
  }
}
```

That’s it. Your function is now invocable via tool calls, displayed inline as the stream arrives, and its result is fed back into the conversation.

### 🔧 Tuning & UX Notes

* **Streaming:** Uses chunked lines (`data: {json}`) and prints **delta content** immediately.
* **Inline Tool Visualization:** The console prints `Calling Tool: name({...})` as arguments stream in, then panels the result.
* **Truncation:** Long tool outputs are truncated for readability (default **10 lines**) with an omitted‑lines footer; adjust the limit in code.
* **Safety:** Shell commands have a 30‑second timeout and structured error reporting. File reads validate ranges and return friendly errors.

### 🧪 Example Session

```text
You: Open README.md, show me 1–20, then count files in this directory.

Assistant: Accessing...
Calling Tool: read_file({"file_path": "README.md", "start_line": 1, "end_line": 20})
Calling Tool: shell({"command": "ls -1 | wc -l"})

────────── Tool Results ──────────
[Tool Result: read_file]
### Showing lines 1-20 of `README.md` (Total: …)
```
