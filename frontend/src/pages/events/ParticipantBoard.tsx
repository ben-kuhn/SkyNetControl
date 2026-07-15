import type { ReactNode } from "react";
import type { EventParticipant, EventPost, ParticipantStatus } from "../../types";

export const STATUS_LABEL: Record<ParticipantStatus, string> = {
  checked_in: "Checked in",
  at_post: "At post",
  en_route: "En route",
  out_of_service: "Out of service",
  checked_out: "Checked out",
};

export const STATUS_BADGE: Record<ParticipantStatus, string> = {
  checked_in: "bg-success/15 text-success",
  at_post: "bg-accent/15 text-accent",
  en_route: "bg-warning/15 text-warning",
  out_of_service: "bg-danger/15 text-danger",
  checked_out: "bg-bg-elevated text-text-muted",
};

interface ParticipantBoardProps {
  participants: EventParticipant[];
  posts: EventPost[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  /** Per-row NCS action cell; Task 9 supplies this. Null = read-only. */
  actions?: (p: EventParticipant) => ReactNode;
}

export function ParticipantBoard({ participants, posts, selectedId, onSelect, actions }: ParticipantBoardProps) {
  const postName = (id: number | null) => posts.find((p) => p.id === id)?.name ?? null;

  if (participants.length === 0) {
    return <p className="text-text-muted text-sm py-6">No participants checked in yet.</p>;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-text-muted border-b border-border">
          <th className="py-2 pr-3">Callsign</th>
          <th className="py-2 pr-3">Name</th>
          <th className="py-2 pr-3">Status</th>
          <th className="py-2 pr-3">Post / location</th>
          <th className="py-2 pr-3">In since</th>
          {actions && <th className="py-2">Actions</th>}
        </tr>
      </thead>
      <tbody>
        {participants.map((p) => (
          <tr
            key={p.id}
            onClick={() => onSelect(p.id)}
            className={`border-b border-border cursor-pointer ${
              selectedId === p.id ? "bg-accent/5" : "hover:bg-bg-elevated"
            }`}
          >
            <td className="py-2 pr-3 font-mono text-text-primary">{p.callsign}</td>
            <td className="py-2 pr-3 text-text-secondary">{p.name ?? "—"}</td>
            <td className="py-2 pr-3">
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[p.current_status]}`}>
                {STATUS_LABEL[p.current_status]}
              </span>
            </td>
            <td className="py-2 pr-3 text-text-secondary">
              {postName(p.post_id) ?? p.location ?? "—"}
            </td>
            <td className="py-2 pr-3 text-text-muted">
              {p.current_status === "checked_out"
                ? "—"
                : new Date(p.checked_in_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </td>
            {actions && <td className="py-2" onClick={(e) => e.stopPropagation()}>{actions(p)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
