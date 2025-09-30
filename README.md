## Example Quickstart

- Must provide Chat Completions endpoint with gpt-oss loaded at the address: `http://localhost:8080/v1/chat/completions`. (e.g. llama-server from llama.cpp)

  ### WSL2
  
  ```bash
  uv pip install -r requirements.txt
  uv run src/harmony-cli/cli.py
  ```
  
  ![](assets/image.png)
