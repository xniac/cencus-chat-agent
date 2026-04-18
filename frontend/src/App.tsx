import React from 'react';
import { useChat } from './hooks/useChat';
import ChatWindow from './components/ChatWindow';
import InputBar from './components/InputBar';
import type { ExampleQuestion } from './types';

const EXAMPLE_QUESTIONS: ExampleQuestion[] = [
  {
    text: 'What are the top 10 most populated states?',
    icon: '👥',
  },
  {
    text: 'What is the median household income by state?',
    icon: '💰',
  },
  {
    text: 'Which counties have the highest percentage of college graduates?',
    icon: '🎓',
  },
  {
    text: 'What is the racial diversity breakdown across the US?',
    icon: '🌍',
  },
];

const App: React.FC = () => {
  const { messages, isLoading, statusText, sendMessage, cancelRequest, clearChat } =
    useChat();

  const hasMessages = messages.length > 0;

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <div className="logo">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 3v18h18" />
              <path d="M7 16l4-8 4 4 4-6" />
            </svg>
          </div>
          <h1 className="title">US Census Chat Agent</h1>
        </div>
        {hasMessages && (
          <button className="new-chat-button" onClick={clearChat}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            New Chat
          </button>
        )}
      </header>

      <main className="main">
        {!hasMessages ? (
          <div className="welcome">
            <div className="welcome-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M3 3v18h18" />
                <path d="M7 16l4-8 4 4 4-6" />
              </svg>
            </div>
            <h2 className="welcome-title">Census Data Explorer</h2>
            <p className="welcome-subtitle">
              Ask questions about US population, demographics, housing, income,
              education, and more — powered by the US Open Census dataset.
            </p>
            <div className="example-grid">
              {EXAMPLE_QUESTIONS.map((q, i) => (
                <button
                  key={i}
                  className="example-button"
                  onClick={() => sendMessage(q.text)}
                  disabled={isLoading}
                >
                  <span className="example-icon">{q.icon}</span>
                  <span className="example-text">{q.text}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ChatWindow
            messages={messages}
            statusText={statusText}
            isLoading={isLoading}
          />
        )}
      </main>

      <footer className="footer">
        <InputBar
          onSend={sendMessage}
          onCancel={cancelRequest}
          isLoading={isLoading}
        />
      </footer>
    </div>
  );
};

export default App;
