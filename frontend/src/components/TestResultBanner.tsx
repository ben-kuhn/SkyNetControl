import { useEffect } from "react";

export interface TestResult {
  ok: boolean;
  message: string;
}

interface TestResultBannerProps {
  result: TestResult | null;
  onDismiss: () => void;
}

export function TestResultBanner({ result, onDismiss }: TestResultBannerProps) {
  useEffect(() => {
    if (!result) return;
    const timer = setTimeout(onDismiss, 8000);
    return () => clearTimeout(timer);
  }, [result, onDismiss]);

  if (!result) return null;

  return (
    <div
      className={`flex items-center justify-between gap-3 rounded-md px-4 py-2 text-sm ${
        result.ok
          ? "bg-green-900/30 border border-green-700 text-green-300"
          : "bg-danger/20 border border-danger text-danger"
      }`}
    >
      <span>{result.message}</span>
      <button
        type="button"
        onClick={onDismiss}
        className="shrink-0 opacity-70 hover:opacity-100 transition-opacity"
        aria-label="Dismiss"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}
