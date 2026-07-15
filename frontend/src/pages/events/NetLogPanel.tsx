import type { ReactNode } from "react";
import type { EventLogEntry } from "../../types";

interface NetLogPanelProps {
  log: EventLogEntry[];
  /** Composer element (textarea + submit) supplied by Task 9 for NCS. */
  composer?: ReactNode;
}

export function NetLogPanel({ log, composer }: NetLogPanelProps) {
  const reversed = [...log].sort((a, b) => b.seq - a.seq);

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-sm font-semibold text-text-primary mb-2">Net log</h2>
      {composer}
      <div className="flex-1 overflow-y-auto flex flex-col gap-1 mt-2">
        {reversed.length === 0 && <p className="text-text-muted text-sm">No log entries yet.</p>}
        {reversed.map((entry) => (
          <div
            key={entry.seq}
            className={`rounded px-2 py-1.5 text-sm ${
              entry.entry_type === "system"
                ? "text-text-muted"
                : "bg-bg-elevated text-text-primary"
            }`}
          >
            <span className="text-xs text-text-muted font-mono mr-2">
              {new Date(entry.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
            {entry.entry_type !== "system" && (
              <span className="text-xs text-accent font-mono mr-2">{entry.actor}</span>
            )}
            {entry.pinned && <span className="mr-1" title="Pinned">📌</span>}
            {entry.message}
          </div>
        ))}
      </div>
    </div>
  );
}
