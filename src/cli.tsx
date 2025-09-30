import React, { useState, useEffect, useRef } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import TextInput from 'ink-text-input';
import { Message, ToolCall, ToolDefinition } from './types.js';
import { ToolExecutor } from './tools.js';
import { createSystemMessage, createDeveloperMessage } from './harmony.js';

const API_URL = process.env.HARMONY_CLI_API_URL || 'http://localhost:8080/v1/chat/completions';

interface CLIProps {
  programRoot: string;
}

const CLI: React.FC<CLIProps> = ({ programRoot }) => {
  const { exit } = useApp();
  const [conversationHistory, setConversationHistory] = useState<Message[]>([]);
  const [userInput, setUserInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentResponse, setCurrentResponse] = useState('');
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const toolExecutorRef = useRef(new ToolExecutor());
  
  const parseChannelContent = (content: string): { channel: string; text: string }[] => {
    const parts: { channel: string; text: string }[] = [];
    const regex = /<\|channel\|>([^<]+)<\|message\|>([\s\S]*?)(?:<\|end\|>|$)/g;
    let match;
    let lastIndex = 0;
    
    while ((match = regex.exec(content)) !== null) {
      // Add any text before this match as 'unknown' channel
      if (match.index > lastIndex) {
        const beforeText = content.substring(lastIndex, match.index).trim();
        if (beforeText) {
          parts.push({ channel: 'unknown', text: beforeText });
        }
      }
      
      parts.push({
        channel: match[1].trim(),
        text: match[2].trim()
      });
      
      lastIndex = match.index + match[0].length;
    }
    
    // Add remaining text
    if (lastIndex < content.length) {
      const remaining = content.substring(lastIndex).trim();
      if (remaining) {
        parts.push({ channel: 'unknown', text: remaining });
      }
    }
    
    return parts.length > 0 ? parts : [{ channel: 'final', text: content }];
  };
  
  useEffect(() => {
    const instructions = `You are a helpful terminal assistant that can execute code and edit files with the provided tools.\n\nRoot directory: ${programRoot}`;
    
    const toolsDefinition: ToolDefinition[] = [
      {
        type: 'function',
        function: {
          name: 'exec',
          description: 'Execute code via Python or shell. Large outputs are automatically truncated.',
          parameters: {
            type: 'object',
            properties: {
              kind: { type: 'string', enum: ['python', 'shell'], description: 'Execution mode.' },
              code: { type: 'string', description: 'Python source or shell command string.' },
              timeout: { type: 'integer', description: 'Seconds before kill.', default: 30 }
            },
            required: ['kind', 'code']
          }
        }
      },
      {
        type: 'function',
        function: {
          name: 'python',
          description: 'Execute Python code. Large outputs are automatically truncated.',
          parameters: {
            type: 'object',
            properties: {
              code: { type: 'string', description: 'Python source string.' },
              timeout: { type: 'integer', description: 'Seconds before kill.', default: 30 }
            },
            required: ['code']
          }
        }
      },
      {
        type: 'function',
        function: {
          name: 'apply_patch',
          description: 'Edit files by providing a patch document.',
          parameters: {
            type: 'object',
            properties: {
              patch: { type: 'string', description: 'The patch text to apply.' }
            },
            required: ['patch']
          }
        }
      }
    ];
    
    const systemMessage = createSystemMessage(true);
    const developerMessage = createDeveloperMessage(instructions, toolsDefinition);
    
    setConversationHistory([
      { role: 'system', content: systemMessage },
      { role: 'user', content: developerMessage }
    ]);
  }, [programRoot]);
  
  const handleSubmit = async () => {
    if (!userInput.trim()) return;
    
    const newHistory = [...conversationHistory, { role: 'user' as const, content: userInput }];
    setConversationHistory(newHistory);
    setUserInput('');
    setIsStreaming(true);
    setCurrentResponse('');
    setToolCalls([]);
    
    try {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'gpt-4',
          messages: newHistory,
          stream: true,
        }),
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let accumulatedResponse = '';
      let accumulatedToolCalls: ToolCall[] = [];

      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') continue;

            try {
              const parsed = JSON.parse(data);
              const delta = parsed.choices?.[0]?.delta;
              
              if (delta?.content) {
                accumulatedResponse += delta.content;
                setCurrentResponse(accumulatedResponse);
              }
              
              if (delta?.tool_calls) {
                // Handle tool calls
                for (const tc of delta.tool_calls) {
                  const existing = accumulatedToolCalls.find(t => t.index === tc.index);
                  if (existing) {
                    if (tc.function?.arguments) {
                      existing.function.arguments += tc.function.arguments;
                    }
                  } else if (tc.id && tc.function?.name) {
                    accumulatedToolCalls.push({
                      id: tc.id,
                      type: 'function',
                      index: tc.index,
                      function: {
                        name: tc.function.name,
                        arguments: tc.function.arguments || '',
                      },
                    });
                  }
                }
                setToolCalls([...accumulatedToolCalls]);
              }
            } catch (e) {
              // Skip invalid JSON
            }
          }
        }
      }

      setIsStreaming(false);

      // Add assistant's response to history
      const assistantMessage: Message = {
        role: 'assistant',
        content: accumulatedResponse,
      };
      
      if (accumulatedToolCalls.length > 0) {
        assistantMessage.tool_calls = accumulatedToolCalls;
      }

      let updatedHistory = [...newHistory, assistantMessage];
      setConversationHistory(updatedHistory);
      
      // Execute tools if present
      if (accumulatedToolCalls.length > 0) {
        setIsProcessing(true);
        
        for (const toolCall of accumulatedToolCalls) {
          try {
            const args = JSON.parse(toolCall.function.arguments);
            const result = await toolExecutorRef.current.executeTool(toolCall.function.name, args);
            
            // Add tool result to history
            const toolResultMessage: Message = {
              role: 'tool',
              name: toolCall.function.name,
              content: result.display,
            };
            
            updatedHistory = [...updatedHistory, toolResultMessage];
            setConversationHistory(updatedHistory);
          } catch (error: any) {
            const errorMessage: Message = {
              role: 'tool',
              name: toolCall.function.name,
              content: `Error executing tool: ${error.message}`,
            };
            updatedHistory = [...updatedHistory, errorMessage];
            setConversationHistory(updatedHistory);
          }
        }
        
        setIsProcessing(false);
        setToolCalls([]);
      }
      
    } catch (error: any) {
      setCurrentResponse(`Error: ${error.message}`);
      setIsStreaming(false);
    }
  };
  
  useInput((input: string, key: any) => {
    if (key.escape) {
      exit();
    }
  });
  
  return (
    <Box flexDirection="column" padding={1}>
      <Box marginBottom={1}>
        <Text bold color="green">Harmony CLI</Text>
      </Box>
      
      <Box marginBottom={1}>
        <Text dimColor>Commands: /export md [path], /export json [path]</Text>
      </Box>
      
      <Box marginBottom={1}>
        <Text dimColor>Ctrl+C to exit</Text>
      </Box>
      
      <Box flexDirection="column" marginBottom={1}>
        {conversationHistory.slice(2).map((msg, idx) => {
          if (msg.role === 'user') {
            return (
              <Box key={idx} marginTop={1}>
                <Text bold color="cyan">You: </Text>
                <Text>{msg.content}</Text>
              </Box>
            );
          } else if (msg.role === 'assistant') {
            const channelParts = parseChannelContent(msg.content);
            return (
              <Box key={idx} marginTop={1} flexDirection="column">
                <Text bold color="green">Assistant:</Text>
                {channelParts.map((part, i) => (
                  <Box key={i} flexDirection="column" marginLeft={2}>
                    <Text dimColor italic>[{part.channel}]</Text>
                    <Text>{part.text}</Text>
                  </Box>
                ))}
                {msg.tool_calls && msg.tool_calls.map((tc, i) => (
                  <Box key={`tool-${i}`} flexDirection="column" marginLeft={2} marginTop={1}>
                    <Text color="yellow">🔧 Tool Call: {tc.function.name}</Text>
                    <Text dimColor>{tc.function.arguments.substring(0, 100)}</Text>
                  </Box>
                ))}
              </Box>
            );
          } else if (msg.role === 'tool') {
            return (
              <Box key={idx} marginTop={1} flexDirection="column">
                <Text bold color="magenta">Tool Result ({msg.name}):</Text>
                <Text dimColor>{msg.content.substring(0, 300)}</Text>
              </Box>
            );
          }
          return null;
        })}
      </Box>
      
      {isStreaming && (
        <Box marginTop={1} flexDirection="column">
          <Text color="cyan">⏳ Streaming response...</Text>
          {currentResponse && (
            <Box flexDirection="column" marginLeft={2}>
              {parseChannelContent(currentResponse).map((part, i) => (
                <Box key={i} flexDirection="column">
                  <Text dimColor italic>[{part.channel}]</Text>
                  <Text>{part.text}</Text>
                </Box>
              ))}
            </Box>
          )}
          {toolCalls.length > 0 && (
            <Box flexDirection="column" marginLeft={2}>
              {toolCalls.map((tc, i) => (
                <Box key={i} flexDirection="column" marginTop={1}>
                  <Text color="yellow">🔧 {tc.function.name}(...)</Text>
                  <Text dimColor>{tc.function.arguments}</Text>
                </Box>
              ))}
            </Box>
          )}
        </Box>
      )}
      
      {isProcessing && (
        <Box marginTop={1}>
          <Text color="yellow">⚙️  Executing tools...</Text>
        </Box>
      )}
      
      <Box marginTop={1}>
        <Text bold color="cyan">You: </Text>
        <TextInput
          value={userInput}
          onChange={setUserInput}
          onSubmit={handleSubmit}
        />
      </Box>
    </Box>
  );
};

export default CLI;