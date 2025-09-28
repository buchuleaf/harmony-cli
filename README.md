# Example Quickstart:

> Have `llama-server` from `llama.cpp` running on port 8080.

```bash
# Create the environment
uv venv

# Install the dependencies
uv pip install -r requirements.txt

# Run the program
uv run python main.py
```

---

# ⚠️ **Warning**: AI slop below:

# 🤖 GPT-OSS: The Operator’s Console

Your terminal isn’t just a shell — it’s **the gateway**.

This console is a live, streaming interface into your **local** Large Language Model. No filler, no drag. Just signal. Just command.

Built for operators who bend systems, patch reality, and ship code at the speed of thought.

---

*(GIF Placeholder: Neon-lit console streaming character-by-character output; tool calls appear inline, then results fold in below.)*

## ✨ The Construct

- **Hyper‑Stream** — Watch text and code materialize **character‑by‑character** as tokens arrive. Model deltas render live; tool calls surface inline.
- **System Control** — Speak naturally, act in machine logic. Invoke `shell`, `read_file`, and `file_patch` without leaving the flow.
- **Visual Clarity** — A `rich` UI with panels and rules that keep long outputs readable.
- **Signal Integrity** — Clean, flicker‑free streaming; tool results are **paneled** and long outputs are smart‑truncated.
- **Architect Mode** — Extend with plain Python. Register your function and brief the model; the console does the rest.

## 🔌 Requirements

- A local model endpoint compatible with `llama.cpp` **chat completions** streaming.
- Default target: `http://localhost:8080/v1/chat/completions` (configurable in the code).

> **Tip:** Start `llama-server` from `llama.cpp` on port **8080** before running the console.

## 🚀 Jacking In

```bash
# Create the environment
uv venv

# Install dependencies
uv pip install -r requirements.txt

# Run the console
uv run python cli.py
````

## 💻 Operating

Once inside, the system listens. It executes.

```text
┌─────────────────────────────────────────┐
│ GPT-OSS API CLI (Stable UI)             │
└─────────────────────────────────────────┘

You: Read the first 5 lines of tools.py, then run `python --version`.

Assistant: Accessing...
Calling Tool: read_file({"file_path": "tools.py", "end_line": 5})
Calling Tool: shell({"command": "python --version"})
```

After tools finish, the results are displayed in green panels. Long outputs are truncated with an inline “(output truncated …)” footer; full content is still passed back to the model.

## 🧰 The Arsenal (Built‑ins)

### `shell`

Execute any command your terminal understands. Output and errors are formatted into separate **STDOUT/STDERR** sections and time out safely.

**Example**

> You: what processes are running?
>
> Assistant: `shell("ps aux")`

### `read_file`

Read entire files or just a slice — with **1‑based** line numbers and an auto‑generated header showing the window.

**Example**

> You: show me lines 1–40 of `cli.py`
>
> Assistant: `read_file("cli.py", start_line=1, end_line=40)`

### `file_patch`

Apply a **diff‑style** patch inline — add (`+`), remove (`-`), or keep context (` `). Ideal for surgical edits without opening an editor.

**Example**

> You: in `cli.py`, change the API port from 8080 → 9000
>
> Assistant:
>
> ```
> file_patch("cli.py", "-API_URL = \"http://localhost:8080/v1/chat/completions\"\n+API_URL = \"http://localhost:9000/v1/chat/completions\"")
> ```

**Patch rules (quick)**

* Start each changed line with `+` (add) or `-` (remove). Lines without a prefix are treated as context.
* File is rewritten from the original with your directives; keep enough context to avoid accidental deletions.

## 🧩 Becoming the Architect

Extend the console with your own tools in three steps.

1. **Forge the Tool** — add a Python function in `tools.py`:

```python
# tools.py
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

3. **Brief the Model** — declare a tool schema in `cli.py` `tools_definition`:

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

## 🔧 Tuning & UX Notes

* **Streaming:** Uses chunked lines (`data: {json}`) and prints **delta content** immediately.
* **Inline Tool Visualization:** The console prints `Calling Tool: name({...})` as arguments stream in, then panels the result.
* **Truncation:** Long tool outputs are truncated for readability (default **10 lines**) with an omitted‑lines footer; adjust the limit in code.
* **Safety:** Shell commands have a 30‑second timeout and structured error reporting. File reads validate ranges and return friendly errors.

## 🧪 Example Session

```text
You: Open README.md, show me 1–20, then count files in this directory.

Assistant: Accessing...
Calling Tool: read_file({"file_path": "README.md", "start_line": 1, "end_line": 20})
Calling Tool: shell({"command": "ls -1 | wc -l"})

────────── Tool Results ──────────
[Tool Result: read_file]
### Showing lines 1-20 of `README.md` (Total: …)
```

1: # Project
2: …
...

```

[Tool Result: shell]
### Shell Command Successful
#### STDOUT
```

---

Welcome to the machine. Welcome to the real world.

