// frontend/src/pages/events/MessageComposer.tsx
import { useState } from "react";
import { composeEventMessage } from "../../api/events";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import type { EventMessage } from "../../types";

interface MessageComposerProps {
  netSlug: string;
  eventId: number;
  open: boolean;
  onClose: () => void;
  replyTo?: EventMessage | null;
  onSent: () => Promise<void>;
  onError: (message: string) => void;
}

function replyDefaults(replyTo: EventMessage | null | undefined) {
  if (!replyTo) return { to: "", subject: "", body: "" };
  const subject = replyTo.subject.replace(/^(re:\s*)+/i, "");
  const quoted = replyTo.body.split("\n").map((l) => `> ${l}`).join("\n");
  return { to: replyTo.from_callsign, subject: `Re: ${subject}`, body: `\n\n${quoted}` };
}

export function MessageComposer({ netSlug, eventId, open, onClose, replyTo, onSent, onError }: MessageComposerProps) {
  const defaults = replyDefaults(replyTo);
  const [to, setTo] = useState(defaults.to);
  const [subject, setSubject] = useState(defaults.subject);
  const [body, setBody] = useState(defaults.body);
  const [busy, setBusy] = useState(false);

  // Re-seed fields when the reply target changes (modal re-opened for a different message).
  // Keyed remount from the parent (key={replyTo?.id ?? "new"}) makes this reliable.

  async function submit() {
    if (!to.trim() || busy) return;
    setBusy(true);
    try {
      const { delivered } = await composeEventMessage(
        eventId,
        { to_address: to.trim(), subject, body, reply_to_id: replyTo?.id ?? null },
        netSlug,
      );
      onClose();
      await onSent();
      if (!delivered) onError("Message saved but not delivered — check delivery settings / retry.");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Send failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={replyTo ? "Reply" : "New Winlink message"} size="lg">
      <div className="flex flex-col gap-3">
        <Input label="To" value={to} onChange={(e) => setTo(e.target.value)} placeholder="KE0XYZ or name@agency.org" mono />
        <Input label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
        <label className="text-sm text-text-secondary">
          Body
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={8}
            className="mt-1 block w-full rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <div className="flex gap-2 justify-end pt-1">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button loading={busy} disabled={!to.trim()} onClick={() => void submit()}>Send</Button>
        </div>
      </div>
    </Modal>
  );
}
