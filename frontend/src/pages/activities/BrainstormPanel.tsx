import { useEffect, useRef, useState } from "react";
import {
  approveChatSession,
  sendChatMessage,
  startChatSession,
  type ActivityInput,
} from "../../api/activities";
import { useCurrentNet } from "../../hooks/useCurrentNet";
import type { Activity, ChatMessage } from "../../types";
import { useToast } from "../../context/ToastContext";

interface Props {
  onClose: () => void;
  onApproved: (a: Activity) => void;
  /** When true, render as a fullscreen modal (mobile). Otherwise inline pane. */
  modal: boolean;
}

export function BrainstormPanel({ onClose, onApproved, modal }: Props) {
  const { slug } = useCurrentNet();
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [composer, setComposer] = useState("");
  const [sending, setSending] = useState(false);
  const [apiKeyMissing, setApiKeyMissing] = useState(false);
  const [showApprove, setShowApprove] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [tagsText, setTagsText] = useState("");

  const transcriptRef = useRef<HTMLDivElement>(null);
  const { addToast } = useToast();

  useEffect(() => {
    let cancelled = false;
    startChatSession(slug)
      .then((s) => {
        if (!cancelled) setSessionId(s.id);
      })
      .catch((e: any) => {
        if (!cancelled) {
          addToast(e?.detail ?? e?.message ?? "Failed to start chat", "error");
          onClose();
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight });
  }, [messages.length]);

  const handleSend = async () => {
    if (!sessionId || !composer.trim() || sending) return;
    const text = composer;
    setComposer("");
    setSending(true);
    try {
      const { user_message, assistant_message } = await sendChatMessage(sessionId, text, slug);
      setMessages((prev) => [...prev, user_message, assistant_message]);
      setApiKeyMissing(false);
    } catch (e: any) {
      if (e?.status === 503) {
        setApiKeyMissing(true);
      } else {
        addToast(e?.detail ?? e?.message ?? "Failed to send", "error");
        setComposer(text);
      }
    } finally {
      setSending(false);
    }
  };

  const parseTags = (s: string): string[] => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const part of s.split(",")) {
      const t = part.trim();
      if (t && !seen.has(t.toLowerCase())) {
        seen.add(t.toLowerCase());
        out.push(t);
      }
    }
    return out;
  };

  const handleApprove = async () => {
    if (!sessionId) return;
    if (!title.trim() || !description.trim() || !instructions.trim()) {
      addToast("Title, description, and instructions are required.", "error");
      return;
    }
    setSubmitting(true);
    const input: ActivityInput = {
      title: title.trim(),
      description,
      instructions,
      tag_names: parseTags(tagsText),
    };
    try {
      const activity = await approveChatSession(sessionId, input, slug);
      addToast("Activity created from chat.", "success");
      onApproved(activity);
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Failed to save activity", "error");
    } finally {
      setSubmitting(false);
    }
  };

  const hasAssistant = messages.some((m) => m.role === "assistant");

  const containerCls = modal
    ? "fixed inset-0 z-50 bg-bg-base p-4 flex flex-col"
    : "border border-border rounded-lg bg-bg-surface flex flex-col h-[calc(100vh-8rem)] max-h-[800px]";

  return (
    <div className={containerCls}>
      <div className="flex items-center justify-between pb-3 mb-3 border-b border-border px-1">
        <h2 className="text-lg font-semibold text-text-primary">Brainstorm activity</h2>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary p-1 rounded"
          aria-label="Close brainstorm"
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {apiKeyMissing && (
        <div className="mb-3 px-3 py-2 rounded border border-warning/40 bg-warning/[0.08] text-warning text-sm">
          Claude API key not configured. Visit <a href="/config" className="underline">/config</a> to set it.
        </div>
      )}

      <div
        ref={transcriptRef}
        className="flex-1 overflow-auto border border-border rounded-lg p-3 bg-bg-elevated/30 mb-3 space-y-2"
      >
        {messages.length === 0 && (
          <p className="text-text-muted text-sm text-center py-8">
            Tell Claude what kind of activity you want to design.
          </p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] px-3 py-2 rounded-lg whitespace-pre-wrap text-sm ${
                m.role === "user"
                  ? "bg-accent/[0.15] text-text-primary"
                  : "bg-bg-elevated text-text-primary"
              }`}
            >
              {m.content}
            </div>
          </div>
        ))}
      </div>

      <div className="flex gap-2 mb-3">
        <textarea
          value={composer}
          onChange={(e) => setComposer(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          disabled={sending || apiKeyMissing}
          rows={2}
          placeholder="Type a message…"
          className="flex-1 px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary disabled:opacity-60"
        />
        <button
          onClick={handleSend}
          disabled={!composer.trim() || sending || apiKeyMissing}
          className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </div>

      {hasAssistant && !showApprove && (
        <div className="pt-3 border-t border-border">
          <button
            onClick={() => setShowApprove(true)}
            className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
          >
            Save as activity
          </button>
        </div>
      )}

      {showApprove && (
        <div className="pt-3 border-t border-border">
          <h3 className="text-sm font-semibold text-text-primary mb-2">Save chat as activity</h3>
          <div className="mb-2">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-2">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Tags</label>
            <input
              type="text"
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              placeholder="Comma-separated tags"
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-2">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-2">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Instructions</label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={6}
              className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleApprove}
              disabled={submitting}
              className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => setShowApprove(false)}
              className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
