import sys

# Check for --python-tool FIRST, before any other imports
if len(sys.argv) >= 2 and sys.argv[1] == "--python-tool":
    # Only import what we need for the tool execution
    from pathlib import Path
    import traceback
    
    if len(sys.argv) < 3:
        print("--python-tool requires a path argument", file=sys.stderr)
        sys.exit(2)
    
    path = Path(sys.argv[2])
    try:
        code = path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"Failed to read python tool input: {exc}", file=sys.stderr)
        sys.exit(1)
    
    namespace = {"__name__": "__main__"}
    try:
        exec(compile(code, str(path), "exec"), namespace, namespace)
        sys.exit(0)
    except SystemExit as exc:
        code = exc.code
        sys.exit(int(code) if isinstance(code, int) else 1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)

# Normal CLI path
from .cli import main

if __name__ == "__main__":
    main()