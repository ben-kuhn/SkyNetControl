// frontend/src/pages/events/FormFillFrame.tsx
import { useEffect, useRef } from "react";
import { formRenderUrl } from "../../api/events";
import { Button } from "../../components/Button";

interface Props {
  eventId: number;
  inputFormPath: string;
  prefill?: Record<string, string>;
  onVariables: (vars: Record<string, string>) => void;
}

export function FormFillFrame({ eventId, inputFormPath, prefill, onVariables }: Props) {
  const ref = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      // Trust boundary: only accept from OUR iframe, only the expected shape.
      if (!ref.current || e.source !== ref.current.contentWindow) return;
      const data = e.data;
      if (!data || data.type !== "skynet-form-vars" || typeof data.variables !== "object") return;
      const vars: Record<string, string> = {};
      for (const [k, v] of Object.entries(data.variables)) vars[String(k)] = String(v ?? "");
      onVariables(vars);
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onVariables]);

  function collect() {
    ref.current?.contentWindow?.postMessage({ type: "skynet-collect" }, "*");
  }

  return (
    <div className="flex flex-col gap-2">
      <iframe
        ref={ref}
        title="Winlink form"
        sandbox="allow-scripts"
        src={formRenderUrl(eventId, inputFormPath, prefill)}
        className="w-full h-[480px] border border-border rounded-md bg-white"
      />
      <div className="flex justify-end">
        <Button onClick={collect}>Done — build message</Button>
      </div>
    </div>
  );
}
