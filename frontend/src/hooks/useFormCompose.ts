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

export function useFormCompose(netSlug: string, eventId: number) {
  const [step, setStep] = useState<Step>("catalog");
  const [templatePath, setTemplatePath] = useState("");
  const [inputFormPath, setInputFormPath] = useState("");
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [prefill, setPrefill] = useState<Record<string, string>>({});
  const [replyToId, setReplyToId] = useState<number | null>(null);
  const [stamp] = useState(nowStamp());
  const [preview, setPreview] = useState<FormPreview | null>(null);

  const pickForm = useCallback((template: string, inputForm: string) => {
    setTemplatePath(template); setInputFormPath(inputForm); setStep("fill");
  }, []);

  const acceptVariables = useCallback(async (vars: Record<string, string>, onError: (m: string) => void) => {
    setVariables(vars);
    try {
      const p = await previewForm(eventId, { template_path: templatePath, variables: vars, datetime_stamp: stamp }, netSlug);
      setPreview(p); setStep("preview");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Build failed");
    }
  }, [eventId, netSlug, templatePath, stamp]);

  const send = useCallback(async (onDone: () => Promise<void>, onError: (m: string) => void) => {
    try {
      const { delivered } = await sendFormMessage(
        eventId, { template_path: templatePath, variables, datetime_stamp: stamp, reply_to_id: replyToId }, netSlug,
      );
      await onDone();
      if (!delivered) onError("Form saved but not delivered — check delivery / retry.");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Send failed");
    }
  }, [eventId, netSlug, templatePath, variables, stamp, replyToId]);

  return {
    step, setStep, templatePath, inputFormPath, prefill, preview, replyToId,
    pickForm, acceptVariables, send,
    startReply: (template: string, inputForm: string, pf: Record<string, string>, rid: number) => {
      setTemplatePath(template); setInputFormPath(inputForm); setPrefill(pf); setReplyToId(rid); setStep("fill");
    },
  };
}
