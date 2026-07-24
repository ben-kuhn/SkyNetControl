// frontend/src/pages/events/FormCompose.tsx
import { useCallback } from "react";
import { Button } from "../../components/Button";
import { Modal } from "../../components/Modal";
import { useFormCompose } from "../../hooks/useFormCompose";
import type { NetEvent } from "../../types";
import { FormCatalog } from "./FormCatalog";
import { FormFillFrame } from "./FormFillFrame";

interface Props {
  event: NetEvent;
  open: boolean;
  onClose: () => void;
  onSent: () => Promise<void>;
  onError: (m: string) => void;
  compose: ReturnType<typeof useFormCompose>;
}

export function FormCompose({ event, open, onClose, onSent, onError, compose }: Props) {
  const { step, inputFormPath, prefill, preview, pickForm, acceptVariables, send } = compose;
  const handleVariables = useCallback(
    (vars: Record<string, string>) => void acceptVariables(vars, onError),
    [acceptVariables, onError],
  );
  return (
    <Modal open={open} onClose={onClose} title="Winlink form" size="xl">
      {step === "catalog" && (
        <FormCatalog eventId={event.id} onPick={(e) => pickForm(e.template_path, e.input_form_path)} />
      )}
      {step === "fill" && (
        <FormFillFrame eventId={event.id} inputFormPath={inputFormPath} prefill={prefill}
          onVariables={handleVariables} />
      )}
      {step === "preview" && preview && (
        <div className="flex flex-col gap-2 text-sm">
          <div><span className="text-text-muted">To:</span> {preview.to}</div>
          <div><span className="text-text-muted">Subject:</span> {preview.subject}</div>
          <pre className="whitespace-pre-wrap font-sans bg-bg-elevated rounded p-2 max-h-72 overflow-y-auto">{preview.body}</pre>
          <div className="text-xs text-accent">📎 {preview.attachment_filename}</div>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={() => void send(async () => { onClose(); await onSent(); }, onError)}>Send</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
