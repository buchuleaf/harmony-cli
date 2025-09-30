import { ToolDefinition } from './types.js';

function jsonSchemaToTsType(propSpec: any, requiredParams: string[]): string {
  const propType = propSpec.type;
  
  if (propType === 'string') {
    if (propSpec.enum) {
      return '"' + propSpec.enum.join('" | "') + '"';
    }
    return 'string';
  }
  
  if (propType === 'number' || propType === 'integer') {
    return 'number';
  }
  
  if (propType === 'boolean') {
    return 'boolean';
  }
  
  if (propType === 'array') {
    const itemsSpec = propSpec.items || { type: 'any' };
    const itemType = jsonSchemaToTsType(itemsSpec, []);
    return `${itemType}[]`;
  }
  
  if (propType === 'object') {
    return 'object';
  }
  
  return 'any';
}

function convertToolsToHarmonyFormat(toolsDefinition: ToolDefinition[]): string {
  const lines: string[] = ['## functions', 'namespace functions {'];
  
  for (const tool of toolsDefinition) {
    const func = tool.function;
    const name = func.name;
    const description = func.description || '';
    
    lines.push(`// ${description}`);
    
    const params = func.parameters;
    const props = params.properties || {};
    
    if (Object.keys(props).length === 0) {
      lines.push(`type ${name} = () => any;`);
    } else {
      const paramLines: string[] = [];
      const requiredProps = params.required || [];
      
      for (const [paramName, paramSpec] of Object.entries(props)) {
        const spec = paramSpec as any;
        if (spec.description) {
          paramLines.push(`// ${spec.description}`);
        }
        
        const optionalMarker = requiredProps.includes(paramName) ? '' : '?';
        const tsType = jsonSchemaToTsType(spec, requiredProps);
        let line = `${paramName}${optionalMarker}: ${tsType}`;
        
        if (spec.default !== undefined) {
          line += `, // default: ${JSON.stringify(spec.default)}`;
        }
        
        paramLines.push(line);
      }
      
      const indentedProps = paramLines.map(l => `  ${l}`).join(',\n');
      lines.push(`type ${name} = (_: {\n${indentedProps}\n}) => any;`);
    }
    
    lines.push('');
  }
  
  lines.push('} // namespace functions');
  return lines.join('\n');
}

export function createSystemMessage(toolsExist: boolean): string {
  const currentDate = new Date().toISOString().split('T')[0];
  const lines = [
    'You are ChatGPT, a large language model trained by OpenAI.',
    'Knowledge cutoff: 2024-06',
    `Current date: ${currentDate}`,
    'Reasoning: high',
    '# Valid channels: analysis, commentary, final. Channel must be included for every message.',
  ];
  
  if (toolsExist) {
    lines.push("Calls to these tools must go to the commentary channel: 'functions'.");
  }
  
  return lines.join('\n');
}

export function createDeveloperMessage(instructions: string, toolsDefinition: ToolDefinition[]): string {
  const toolsStr = convertToolsToHarmonyFormat(toolsDefinition);
  return `# Instructions
${instructions}

# Tools
${toolsStr}`;
}