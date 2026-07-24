import { useCallback, useState } from "react";
import { previewForm, sendFormMessage } from "../api/events";
import type { FormPreview } from "../types";

type Step = "catalog" | "fill" | "preview";

function nowStamp(): string {
  // "YYYY/MM/DD HH:MM" UTC — stamped ONCE at compose, reused for send.
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}/${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

export function useFormCompose(eventId: number) {
  const [step, setStep] = useState<Step>("catalog");
  const [templatePath, setTemplatePath] = useState("");
  const [inputFormPath, setInputFormPath] = useState("");
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [prefill, setPrefill] = useState<Record<string, string>>({});
  const [replyToId, setReplyToId] = useState<number | null>(null);
  // stamp is initialised to "" and set fresh at the START of each compose session
  // (in pickForm / startReply) so it reflects when the operator actually began
  // composing, not when the panel was mounted.  It stays fixed through
  // acceptVariables → send so preview == send byte-for-byte.
  const [stamp, setStamp] = useState("");
  const [preview, setPreview] = useState<FormPreview | null>(null);

  const pickForm = useCallback((template: string, inputForm: string) => {
    // Reset all session state so a new compose never carries stale reply context.
    setTemplatePath(template);
    setInputFormPath(inputForm);
    setPrefill({});
    setReplyToId(null);
    setVariables({});
    setPreview(null);
    setStamp(nowStamp());
    setStep("fill");
  }, []);

  const acceptVariables = useCallback(async (vars: Record<string, string>, onError: (m: string) => void) => {
    setVariables(vars);
    try {
      const p = await previewForm(eventId, { template_path: templatePath, variables: vars, datetime_stamp: stamp });
      setPreview(p); setStep("preview");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Build failed");
    }
  }, [eventId, templatePath, stamp]);

  const send = useCallback(async (onDone: () => Promise<void>, onError: (m: string) => void) => {
    try {
      const { delivered } = await sendFormMessage(
        eventId, { template_path: templatePath, variables, datetime_stamp: stamp, reply_to_id: replyToId },
      );
      await onDone();
      if (!delivered) onError("Form saved but not delivered — check delivery / retry.");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Send failed");
    }
  }, [eventId, templatePath, variables, stamp, replyToId]);

  // reset() returns the panel to the catalog step with all session state cleared.
  // Use this instead of bare setStep("catalog") so stale prefill / replyToId
  // from a previous reply-with-form session are not carried into a new compose.
  const reset = useCallback(() => {
    setTemplatePath("");
    setInputFormPath("");
    setPrefill({});
    setReplyToId(null);
    setVariables({});
    setPreview(null);
    setStamp("");
    setStep("catalog");
  }, []);

  return {
    step, setStep, templatePath, inputFormPath, prefill, preview, replyToId,
    pickForm, acceptVariables, send, reset,
    startReply: (template: string, inputForm: string, pf: Record<string, string>, rid: number) => {
      // Like pickForm but keeps prefill + replyToId from the inbound message.
      setTemplatePath(template);
      setInputFormPath(inputForm);
      setPrefill(pf);
      setReplyToId(rid);
      setVariables({});
      setPreview(null);
      setStamp(nowStamp());
      setStep("fill");
    },
  };
}
