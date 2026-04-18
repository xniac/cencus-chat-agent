import React, { useState } from 'react';
import type { Message } from '../types';

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function renderMarkdown(text: string): string {
  let html = escapeHtml(text);

  // Code blocks (``` ... ```)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_match, _lang, code) => {
    return `<pre><code>${code.trim()}</code></pre>`;
  });

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Tables
  html = html.replace(
    /(?:^|\n)(\|.+\|)\n(\|[\s\-:|]+\|)\n((?:\|.+\|\n?)*)/g,
    (_match, header, _separator, body) => {
      const headers = header.split('|').filter((c: string) => c.trim());
      const rows = body.trim().split('\n');

      let table = '<table><thead><tr>';
      headers.forEach((h: string) => {
        table += `<th>${h.trim()}</th>`;
      });
      table += '</tr></thead><tbody>';

      rows.forEach((row: string) => {
        const cells = row.split('|').filter((c: string) => c.trim());
        table += '<tr>';
        cells.forEach((c: string) => {
          table += `<td>${c.trim()}</td>`;
        });
        table += '</tr>';
      });

      table += '</tbody></table>';
      return table;
    }
  );

  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // Italic
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  // Headers
  html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  // Horizontal rule
  html = html.replace(/^---$/gm, '<hr />');

  // Unordered lists
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
  html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');

  // Line breaks
  html = html.replace(/\n/g, '<br />');

  return html;
}

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

        <div
          className={`message-text ${message.isError ? 'error' : ''}`}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
        />

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
