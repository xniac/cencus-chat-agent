export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  sql?: string;
  rowCount?: number;
  isStreaming?: boolean;
  isError?: boolean;
}

export interface ChatState {
  messages: Message[];
  isLoading: boolean;
  statusText: string;
  sessionId: string | null;
}

export type SSEEventType =
  | 'thinking'
  | 'sql'
  | 'data'
  | 'answer_token'
  | 'answer'
  | 'session_id'
  | 'error'
  | 'done';

export interface SSEEvent {
  event: SSEEventType;
  data: string;
}

export interface ExampleQuestion {
  text: string;
  icon: string;
}
