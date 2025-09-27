# harmony.py

import json
from datetime import datetime

# This is a Python implementation of the `render_typescript_type` Jinja macro.
def _json_schema_to_ts_type(prop_spec: dict, required_params: list) -> str:
    """Converts a JSON schema property to a TypeScript-like type string."""
    prop_type = prop_spec.get("type")
    
    if prop_type == "string":
        if "enum" in prop_spec:
            return '"' + '" | "'.join(prop_spec["enum"]) + '"'
        return "string"
    if prop_type in ["number", "integer"]:
        return "number"
    if prop_type == "boolean":
        return "boolean"
    if prop_type == "array":
        items_spec = prop_spec.get("items", {"type": "any"})
        item_type = _json_schema_to_ts_type(items_spec, [])
        return f"{item_type}[]"
    if prop_type == "object":
        # Simplified for clarity, as deep objects are complex.
        return "object"
        
    return "any"

# This is a Python implementation of the `render_tool_namespace` Jinja macro.
def convert_tools_to_harmony_format(tools_definition: list) -> str:
    """Converts OpenAI-style tool definitions into the precise Harmony TypeScript format."""
    
    lines = ["## functions", "namespace functions {"]
    
    for tool in tools_definition:
        func = tool.get("function", {})
        name = func.get("name")
        description = func.get("description", "")
        
        lines.append(f"// {description}")
        
        params = func.get("parameters", {})
        props = params.get("properties", {})
        
        if not props:
            lines.append(f"type {name} = () => any;")
        else:
            param_lines = []
            required_props = params.get("required", [])
            for param_name, param_spec in props.items():
                if param_spec.get("description"):
                    param_lines.append(f"// {param_spec['description']}")
                
                optional_marker = "" if param_name in required_props else "?"
                ts_type = _json_schema_to_ts_type(param_spec, required_props)
                
                line = f"{param_name}{optional_marker}: {ts_type}"
                
                if "default" in param_spec:
                    line += f", // default: {json.dumps(param_spec['default'])}"
                
                param_lines.append(line)

            # Indent property lines
            indented_props = ",\n".join([f"  {line}" for line in param_lines])
            lines.append(f"type {name} = (_: {{\n{indented_props}\n}}) => any;")
        lines.append("") # Add a blank line after each function

    lines.append("} // namespace functions")
    return "\n".join(lines)


def create_system_message(tools_exist: bool) -> str:
    """Creates the standard Harmony system message, mirroring the Jinja template."""
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    lines = [
        "You are ChatGPT, a large language model trained by OpenAI.",
        "Knowledge cutoff: 2024-06",
        f"Current date: {current_date}",
        "Reasoning: high",
        "# Valid channels: analysis, commentary, final. Channel must be included for every message."
    ]
    
    if tools_exist:
        lines.append("Calls to these tools must go to the commentary channel: 'functions'.")
        
    return "\n".join(lines)


def create_developer_message(instructions: str, tools_definition: list) -> str:
    """Creates the Harmony developer message, including instructions and formatted tools."""
    tools_str = convert_tools_to_harmony_format(tools_definition)
    
    return f"""# Instructions
{instructions}

# Tools
{tools_str}"""