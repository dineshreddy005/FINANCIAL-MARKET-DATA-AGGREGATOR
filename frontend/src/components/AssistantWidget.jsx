import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext.jsx';

export default function AssistantWidget() {
  const { apiFetch } = useAuth();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([
    { id: 0, who: 'bot', text: 'Ask me about a live price, your account balance, or circuit-breaker status. I only answer from real data pulled for this request.' },
  ]);
  const [input, setInput] = useState('');
  const [source, setSource] = useState('');
  const [sending, setSending] = useState(false);
  const bodyRef = useRef(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text) return;
    setMessages((m) => [...m, { id: Date.now(), who: 'user', text }]);
    setInput('');
    setSending(true);
    try {
      const res = await apiFetch('/api/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      const data = await res.json();
      setMessages((m) => [...m, { id: Date.now() + 1, who: 'bot', text: data.reply || 'No response.' }]);
      setSource(data.source || '');
    } catch (e) {
      setMessages((m) => [...m, { id: Date.now() + 1, who: 'bot', text: 'Network error reaching the assistant.' }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <button className="assistant-toggle" onClick={() => setOpen((o) => !o)} aria-label="Toggle AI assistant">
        {open ? '×' : '◆'}
      </button>
      {open && (
        <div className="assistant-panel">
          <div className="assistant-panel__head">
            <span>Desk assistant</span>
            {source && <span className={`source-tag source-tag--${source}`}>{source}</span>}
          </div>
          <div className="assistant-panel__body" ref={bodyRef}>
            {messages.map((m) => (
              <div key={m.id} className={`chat-msg chat-msg--${m.who}`}>
                {m.text}
              </div>
            ))}
          </div>
          <form
            className="assistant-panel__input"
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about AAPL, your balance…" />
            <button type="submit" disabled={sending}>
              Send
            </button>
          </form>
        </div>
      )}
    </>
  );
}
