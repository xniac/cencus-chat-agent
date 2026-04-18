import { useState, useRef, useCallback } from 'react';
import type { ChatState, Message, SSEEvent } from '../types';

const initialState: ChatState = {
  messages: [],
  isLoading: false,
  statusText: '',
  sessionId: null,
};

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

function parseSSEEvents(text: string): SSEEvent[] {
  const events: SSEEvent[] = [];
  // Normalize CR/LF to LF
  const normalized = text.replace(/\r\n/g, '\n');
  const blocks = normalized.split('\n\n');

  for (const block of blocks) {
    if (!block.trim()) continue;

    let event = '';
    const dataParts: string[] = [];

    const lines = block.split('\n');
    for (const line of lines) {
      if (line.startsWith('event:')) {
        // Event name: strip only leading space after "event:"
        event = line.slice(6).replace(/^ /, '');
      } else if (line.startsWith('data:')) {
        // Per SSE spec: strip only single leading space, preserve the rest
        dataParts.push(line.slice(5).replace(/^ /, ''));
      }
    }

    if (event) {
      events.push({
        event: event as SSEEvent['event'],
        data: dataParts.join('\n'),
      });
    }
  }

  return events;
}

export function useChat() {
  const [state, setState] = useState<ChatState>(initialState);
  const abortControllerRef = useRef<AbortController | null>(null);
  const bufferRef = useRef('');

  const sendMessage = useCallback(async (text: string) => {
    const userMessage: Message = {
      id: generateId(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    };

    const assistantMessage: Message = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isStreaming: true,
    };

    setState(prev => ({
      ...prev,
      messages: [...prev.messages, userMessage, assistantMessage],
      isLoading: true,
      statusText: 'Thinking...',
    }));

    const controller = new AbortController();
    abortControllerRef.current = controller;
    bufferRef.current = '';

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          session_id: state.sessionId,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        bufferRef.current += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');

        // Split on double newline for SSE
        const parts = bufferRef.current.split('\n\n');
        // Keep the last (potentially incomplete) part in the buffer
        bufferRef.current = parts.pop() || '';

        const events = parseSSEEvents(parts.join('\n\n'));

        for (const event of events) {
          switch (event.event) {
            case 'thinking':
              setState(prev => ({ ...prev, statusText: event.data }));
              break;

            case 'sql':
              setState(prev => ({
                ...prev,
                messages: prev.messages.map(m =>
                  m.id === assistantMessage.id ? { ...m, sql: event.data } : m
                ),
              }));
              break;

            case 'data':
              setState(prev => ({
                ...prev,
                messages: prev.messages.map(m =>
                  m.id === assistantMessage.id
                    ? { ...m, rowCount: parseInt(event.data, 10) }
                    : m
                ),
              }));
              break;

            case 'answer_token':
              setState(prev => ({
                ...prev,
                messages: prev.messages.map(m =>
                  m.id === assistantMessage.id
                    ? { ...m, content: m.content + event.data }
                    : m
                ),
              }));
              break;

            case 'answer':
              setState(prev => ({
                ...prev,
                messages: prev.messages.map(m =>
                  m.id === assistantMessage.id
                    ? { ...m, content: event.data, isStreaming: false }
                    : m
                ),
              }));
              break;

            case 'session_id':
              setState(prev => ({ ...prev, sessionId: event.data }));
              break;

            case 'error':
              setState(prev => ({
                ...prev,
                isLoading: false,
                statusText: '',
                messages: prev.messages.map(m =>
                  m.id === assistantMessage.id
                    ? { ...m, content: event.data, isStreaming: false, isError: true }
                    : m
                ),
              }));
              break;

            case 'done':
              setState(prev => ({
                ...prev,
                isLoading: false,
                statusText: '',
                messages: prev.messages.map(m =>
                  m.id === assistantMessage.id
                    ? { ...m, isStreaming: false }
                    : m
                ),
              }));
              break;
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        setState(prev => ({
          ...prev,
          isLoading: false,
          statusText: '',
          messages: prev.messages.map(m =>
            m.id === assistantMessage.id
              ? { ...m, content: 'Request cancelled.', isStreaming: false }
              : m
          ),
        }));
      } else {
        setState(prev => ({
          ...prev,
          isLoading: false,
          statusText: '',
          messages: prev.messages.map(m =>
            m.id === assistantMessage.id
              ? {
                  ...m,
                  content: 'Something went wrong. Please try again.',
                  isStreaming: false,
                  isError: true,
                }
              : m
          ),
        }));
      }
    }
  }, [state.sessionId]);

  const cancelRequest = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
  }, []);

  const clearChat = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setState({ ...initialState });
  }, []);

  return {
    messages: state.messages,
    isLoading: state.isLoading,
    statusText: state.statusText,
    sendMessage,
    cancelRequest,
    clearChat,
  };
}
