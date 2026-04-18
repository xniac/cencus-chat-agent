import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Message } from '../types';

interface MessageBubbleProps {
  message: Message;
}

const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const [sqlExpanded, setSqlExpanded] = useState(false);

  return (
    <div className={`message ${message.role}`}>
      <div className="message-avatar">
        {message.role === 'user' ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <circle cx="9" cy="10" r="1.5" fill="currentColor" />
            <circle cx="15" cy="10" r="1.5" fill="currentColor" />
            <path d="M9 15h6" />
          </svg>
        )}
      </div>
      <div className="message-content">
        <div className="message-role">
          {message.role === 'user' ? 'You' : 'Census Agent'}
        </div>

        {message.sql && (
          <div className="sql-section">
            <button
              className="sql-toggle"
              onClick={() => setSqlExpanded(!sqlExpanded)}
            >
              <svg
                className={`chevron ${sqlExpanded ? 'expanded' : ''}`}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
              <span>SQL Query</span>
              {message.rowCount !== undefined && (
                <span className="row-count-badge">{message.rowCount} rows</span>
              )}
            </button>
            {sqlExpanded && (
              <pre className="sql-code">
                <code>{message.sql}</code>
              </pre>
            )}
          </div>
        )}

        <div className={`message-text ${message.isError ? 'error' : ''}`}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // Render external links with target=_blank for safety
              a: ({ href, children }) => (
                <a href={href} target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              ),
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>

        {message.isStreaming && !message.content && (
          <div className="streaming-cursor" />
        )}
        {message.isStreaming && message.content && (
          <span className="cursor-blink">|</span>
        )}
      </div>
    </div>
  );
};

export default MessageBubble;
