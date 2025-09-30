import { spawn } from 'child_process';
import * as fs from 'fs/promises';
import * as path from 'path';
import { ToolResult } from './types.js';

const MAX_TOOL_OUTPUT_LINES = 25;
const MAX_LINE_LENGTH = 1000;
const MODEL_MAX_CHARS = 25000;
const MODEL_MAX_OUTPUT_LINES = 120;
const MODEL_MAX_LINE_LENGTH = 400;
const DISPLAY_MAX_LINES = 25;

function mdCodeblock(body: string, language = 'text'): string {
  const text = body || '';
  const maxTicks = Math.max(3, ...[...text.matchAll(/`{3,}/g)].map(m => m[0].length + 1));
  const fence = '`'.repeat(maxTicks);
  const normalizedLang = language.toLowerCase();
  return `${fence}${normalizedLang}\n${text}${text.endsWith('\n') ? '' : '\n'}${fence}\n`;
}

function truncateOutput(
  output: string,
  maxLines: number,
  maxLineLength: number,
  truncNoteTemplate?: string
): string {
  const lines = output.split('\n');
  const originalLineCount = lines.length;
  
  let truncationMessage = '';
  if (originalLineCount > maxLines) {
    const omittedLines = originalLineCount - maxLines;
    lines.splice(maxLines);
    const template = truncNoteTemplate || '... (output truncated, {omitted_lines} more lines hidden) ...';
    truncationMessage = '\n' + template.replace('{omitted_lines}', omittedLines.toString());
  }
  
  const processedLines = lines.map(line => {
    if (line.length > maxLineLength) {
      return line.substring(0, maxLineLength) + ' ... (line truncated) ...';
    }
    return line;
  });
  
  return processedLines.join('\n') + truncationMessage;
}

function displayTruncate(md: string, maxLines = DISPLAY_MAX_LINES): string {
  const lines = md.split('\n');
  if (lines.length <= maxLines) {
    return md;
  }
  
  const trimmedLines = lines.slice(0, maxLines);
  const fenceCount = trimmedLines.filter(l => l.trim().startsWith('```')).length;
  
  if (fenceCount % 2 === 1) {
    trimmedLines.push('```');
  }
  
  const hiddenRaw = lines.length - trimmedLines.length;
  return trimmedLines.join('\n') + `\n\n... ${hiddenRaw} lines hidden ...\n`;
}

async function runCommand(command: string, shell: boolean, timeout: number): Promise<{
  stdout: string;
  stderr: string;
  exitCode: number;
}> {
  return new Promise((resolve, reject) => {
    const proc = shell 
      ? spawn(command, { shell: true, timeout: timeout * 1000 })
      : spawn('node', ['-e', command], { timeout: timeout * 1000 });
    
    let stdout = '';
    let stderr = '';
    
    proc.stdout?.on('data', (data) => { stdout += data.toString(); });
    proc.stderr?.on('data', (data) => { stderr += data.toString(); });
    
    proc.on('close', (code) => {
      resolve({ stdout, stderr, exitCode: code || 0 });
    });
    
    proc.on('error', (err) => {
      reject(err);
    });
  });
}

export class ToolExecutor {
  private availableTools: Record<string, (args: any) => Promise<ToolResult>>;
  
  constructor() {
    this.availableTools = {
      exec: this.exec.bind(this),
      python: this.python.bind(this),
      apply_patch: this.applyPatch.bind(this),
    };
  }
  
  async executeTool(toolName: string, args: any): Promise<ToolResult> {
    const method = this.availableTools[toolName];
    if (!method) {
      const msg = `## Error\nTool \`${toolName}\` not found.`;
      return { model: msg, display: displayTruncate(msg) };
    }
    
    try {
      return await method(args);
    } catch (error: any) {
      const msg = `## Error\n${error.name}: ${error.message}`;
      return { model: msg, display: displayTruncate(msg) };
    }
  }
  
  private async exec({ kind, code, timeout = 30 }: { kind: string; code: string; timeout?: number }): Promise<ToolResult> {
    if (kind !== 'python' && kind !== 'shell') {
      const msg = "## Error\n`kind` must be 'python' or 'shell'.";
      return { model: msg, display: displayTruncate(msg) };
    }
    
    try {
      const result = await runCommand(code, kind === 'shell', timeout);
      return this.formatExecResult(result, kind);
    } catch (error: any) {
      const msg = `## Error\nExecution failed:\n${mdCodeblock(error.message, 'text')}`;
      return { model: msg, display: displayTruncate(msg) };
    }
  }
  
  private async python({ code, timeout = 30 }: { code: string; timeout?: number }): Promise<ToolResult> {
    try {
      const result = await runCommand(code, false, timeout);
      return this.formatExecResult(result, 'python');
    } catch (error: any) {
      const msg = `## Error\nExecution failed:\n${mdCodeblock(error.message, 'text')}`;
      return { model: msg, display: displayTruncate(msg) };
    }
  }
  
  private formatExecResult(result: { stdout: string; stderr: string; exitCode: number }, kind: string): ToolResult {
    const stdoutClean = result.stdout.trimEnd();
    const stderrClean = result.stderr.trimEnd();
    
    const stdoutForModel = truncateOutput(stdoutClean, MODEL_MAX_OUTPUT_LINES, MODEL_MAX_LINE_LENGTH);
    const stderrForModel = truncateOutput(stderrClean, MODEL_MAX_OUTPUT_LINES, MODEL_MAX_LINE_LENGTH);
    
    const ok = result.exitCode === 0;
    const header = ok ? '## Command Successful\n' : `## Command FAILED (Exit Code: ${result.exitCode})\n`;
    
    const modelMdSections: string[] = [header];
    if (stdoutForModel) {
      modelMdSections.push('### STDOUT\n');
      modelMdSections.push(mdCodeblock(stdoutForModel, kind === 'shell' ? 'bash' : 'python'));
    }
    if (stderrForModel) {
      modelMdSections.push('### STDERR\n');
      modelMdSections.push(mdCodeblock(stderrForModel, 'text'));
    }
    if (!stdoutForModel && !stderrForModel) {
      modelMdSections.push('The command produced no output.\n');
    }
    
    let modelContent = modelMdSections.join('');
    
    if (modelContent.length > MODEL_MAX_CHARS) {
      const kept = modelContent.substring(0, MODEL_MAX_CHARS);
      modelContent = kept + `\n_MODEL NOTE: Result automatically truncated to protect the context window._\n`;
    }
    
    const displaySections = [header];
    if (result.stdout) {
      displaySections.push('### STDOUT\n');
      displaySections.push(mdCodeblock(
        truncateOutput(stdoutClean, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH, '. {omitted_lines} lines hidden .'),
        kind === 'shell' ? 'bash' : 'python'
      ));
    }
    if (result.stderr) {
      displaySections.push('### STDERR\n');
      displaySections.push(mdCodeblock(
        truncateOutput(stderrClean, MAX_TOOL_OUTPUT_LINES, MAX_LINE_LENGTH, '. {omitted_lines} lines hidden .'),
        'text'
      ));
    }
    if (!result.stdout && !result.stderr) {
      displaySections.push('The command produced no output.\n');
    }
    
    const displayContent = displayTruncate(displaySections.join(''), DISPLAY_MAX_LINES);
    return { model: modelContent, display: displayContent };
  }
  
  private async applyPatch({ patch }: { patch: string }): Promise<ToolResult> {
    if (!patch || !patch.trim()) {
      const msg = '## Error\n`patch` must be a non-empty string.';
      return { model: msg, display: displayTruncate(msg) };
    }
    
    let text = patch.trim();
    if (text.startsWith('```')) {
      text = text.replace(/^```[a-zA-Z0-9]*\n/, '');
      if (text.endsWith('```')) {
        text = text.slice(0, -3);
      }
    }
    
    const lines = text.split('\n');
    let i = 0;
    
    const isBegin = (line: string) => line.trim().toLowerCase() === '*** begin patch';
    const isEnd = (line: string) => line.trim().toLowerCase() === '*** end patch';
    
    const matchHeader = (line: string): [string | null, string | null] => {
      const s = line.trim();
      const m = s.match(/^\*\*\*\s*(Add File|Delete File|Update File|Overwrite File|Move to)\s*:\s*(.+)$/i);
      if (!m) return [null, null];
      
      const op = m[1].trim().toLowerCase();
      const arg = m[2].trim();
      
      if (op === 'add file') return ['add', arg];
      if (op === 'delete file') return ['delete', arg];
      if (op === 'update file') return ['update', arg];
      if (op === 'overwrite file') return ['overwrite', arg];
      if (op === 'move to') return ['move_to', arg];
      
      return [null, null];
    };
    
    if (i >= lines.length || !isBegin(lines[i])) {
      const msg = "## Error\nPatch must start with '*** Begin Patch'.";
      return { model: msg, display: displayTruncate(msg) };
    }
    i++;
    
    const resultsMd: string[] = [];
    const summaryOps: string[] = [];
    
    const writeFile = async (filePath: string, contentLines: string[]) => {
      const dir = path.dirname(filePath);
      await fs.mkdir(dir, { recursive: true });
      const txt = contentLines.join('\n') + (contentLines.length ? '\n' : '');
      await fs.writeFile(filePath, txt, 'utf-8');
    };
    
    while (i < lines.length) {
      const raw = lines[i];
      if (isEnd(raw)) break;
      
      const [op, arg] = matchHeader(raw);
      if (!op || !arg) {
        const msg = `## Error\nUnrecognized patch directive: ${raw}`;
        return { model: msg, display: displayTruncate(msg) };
      }
      
      if (op === 'add') {
        i++;
        const contentLines: string[] = [];
        while (i < lines.length) {
          const l = lines[i];
          const [mo] = matchHeader(l);
          if (mo || isEnd(l)) break;
          if (!l.startsWith('+')) {
            const msg = `## Error\nAdd File expects lines starting with '+'. Offending line: ${l}`;
            return { model: msg, display: displayTruncate(msg) };
          }
          contentLines.push(l.substring(1));
          i++;
        }
        
        const filePath = path.resolve(arg);
        let oldLines: string[] = [];
        try {
          const oldText = await fs.readFile(filePath, 'utf-8');
          oldLines = oldText.split('\n');
        } catch {}
        
        await writeFile(filePath, contentLines);
        const added = contentLines.length;
        const removed = oldLines.length;
        const net = added - removed;
        summaryOps.push(`Added ${arg} (+${added}/-${removed}, net ${net >= 0 ? '+' : ''}${net})`);
        resultsMd.push(`### Added: \`${arg}\`\n- Lines added: **${added}**, removed: **${removed}**, net: **${net >= 0 ? '+' : ''}${net}**\n`);
        continue;
      }
      
      if (op === 'delete') {
        const filePath = path.resolve(arg);
        try {
          const oldText = await fs.readFile(filePath, 'utf-8');
          const oldLines = oldText.split('\n');
          await fs.unlink(filePath);
          const removed = oldLines.length;
          summaryOps.push(`Deleted ${arg} (+0/-${removed}, net -${removed})`);
          resultsMd.push(`### Deleted: \`${arg}\`\n- Lines removed: **${removed}**\n`);
        } catch (error: any) {
          const msg = `## Error\nDelete target does not exist: ${arg}`;
          return { model: msg, display: displayTruncate(msg) };
        }
        i++;
        continue;
      }
      
      if (op === 'overwrite') {
        i++;
        const newContent: string[] = [];
        while (i < lines.length) {
          const l = lines[i];
          const [mo] = matchHeader(l);
          if (mo || isEnd(l)) break;
          if (!l.startsWith('+')) {
            const msg = `## Error\nOverwrite File expects lines starting with '+'. Offending line: ${l}`;
            return { model: msg, display: displayTruncate(msg) };
          }
          newContent.push(l.substring(1));
          i++;
        }
        
        const filePath = path.resolve(arg);
        let oldLines: string[] = [];
        try {
          const oldText = await fs.readFile(filePath, 'utf-8');
          oldLines = oldText.split('\n');
        } catch {}
        
        await writeFile(filePath, newContent);
        const added = newContent.length;
        const removed = oldLines.length;
        const net = added - removed;
        summaryOps.push(`Overwrote ${arg} (+${added}/-${removed}, net ${net >= 0 ? '+' : ''}${net})`);
        resultsMd.push(`### Overwrote: \`${arg}\`\n- Lines added: **${added}**, removed: **${removed}**, net: **${net >= 0 ? '+' : ''}${net}**\n`);
        continue;
      }
      
      if (op === 'update') {
        const filePath = path.resolve(arg);
        try {
          const oldText = await fs.readFile(filePath, 'utf-8');
          await fs.writeFile(filePath, oldText, 'utf-8');
          summaryOps.push(`Updated ${arg}`);
          resultsMd.push(`### Updated: \`${arg}\`\n`);
        } catch (error: any) {
          const msg = `## Error\nUpdate target does not exist: ${arg}`;
          return { model: msg, display: displayTruncate(msg) };
        }
        i++;
        continue;
      }
    }
    
    if (i >= lines.length || !isEnd(lines[i])) {
      const msg = "## Error\nPatch must end with '*** End Patch'.";
      return { model: msg, display: displayTruncate(msg) };
    }
    
    const statusLine = summaryOps.length ? '## ✅ Patch Applied\n' : '## ⚠️ Patch Processed (no changes)\n';
    const summaryList = summaryOps.length ? summaryOps.map(op => `- ${op}`).join('\n') : '_(no changes)_';
    const detail = resultsMd.join('\n\n') || '_(no details)_';
    const fullMd = `${statusLine}${summaryList}\n\n---\n${detail}`;
    
    const modelContent = fullMd.length > MODEL_MAX_CHARS 
      ? fullMd.substring(0, MODEL_MAX_CHARS) + '\n_MODEL NOTE: Patch result truncated._\n'
      : fullMd;
    
    const displayContent = displayTruncate(fullMd, DISPLAY_MAX_LINES);
    return { model: modelContent, display: displayContent };
  }
}