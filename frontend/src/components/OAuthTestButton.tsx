import { Button } from "./Button";
import { getOAuthTestResult, startOAuthTest } from "../api/oauth";
import type { TestResult } from "./TestResultBanner";

interface OAuthTestButtonProps {
  slug: string;
  formValues: { client_id: string; client_secret: string; issuer_url: string; name: string };
  onResult: (r: TestResult) => void;
}

export function OAuthTestButton({ slug, formValues, onResult }: OAuthTestButtonProps) {
  const handleClick = async () => {
    try {
      const { test_session_id, authorize_url } = await startOAuthTest(slug, formValues);
      const popup = window.open(authorize_url, "_blank", "width=600,height=700");

      const onMessage = (e: MessageEvent) => {
        if (e.data?.type === "oauth_test" && e.data.test_session_id === test_session_id) {
          window.removeEventListener("message", onMessage);
          clearInterval(poll);
          onResult(
            e.data.status === "success"
              ? { ok: true, message: "Sign-in succeeded." }
              : { ok: false, message: e.data.error ?? "Sign-in failed." },
          );
        }
      };
      window.addEventListener("message", onMessage);

      const poll = window.setInterval(async () => {
        if (popup?.closed) {
          window.removeEventListener("message", onMessage);
          window.clearInterval(poll);
          try {
            const result = await getOAuthTestResult(test_session_id);
            if (result.status === "success") {
              onResult({ ok: true, message: "Sign-in succeeded." });
            } else if (result.status === "failed") {
              onResult({ ok: false, message: result.error ?? "Sign-in failed." });
            } else {
              onResult({ ok: false, message: "Popup closed before sign-in completed." });
            }
          } catch {
            onResult({ ok: false, message: "Could not retrieve test result." });
          }
        }
      }, 1000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to start test.";
      onResult({ ok: false, message: msg });
    }
  };

  return (
    <Button size="sm" variant="secondary" onClick={handleClick}>
      Test sign-in
    </Button>
  );
}
