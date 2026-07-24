import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchEventReport } from "../../api/events";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { useEvent } from "../../context/EventProvider";
import type { EventReportParticipant } from "../../types";

function csvEscape(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

function toCsv(rows: (string | number | null)[][]): string {
  return rows.map((r) => r.map((c) => csvEscape(c == null ? "" : String(c))).join(",")).join("\n");
}

function downloadCsv(filename: string, csv: string) {
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function fmtHours(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}`;
}

function fmt(dt: string | null): string {
  return dt ? new Date(dt).toLocaleString() : "";
}

export function EventReportPage() {
  const { event: snapshot } = useEvent();
  const { event, log } = snapshot;
  const [report, setReport] = useState<EventReportParticipant[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEventReport(event.id)
      .then((rep) => setReport(rep.participants))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load report"));
  }, [event.id]);

  if (error) return <p className="p-6 text-danger text-sm">{error}</p>;
  if (!report) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  function participantsCsv() {
    downloadCsv(
      `event-${event.id}-participants.csv`,
      toCsv([
        ["callsign", "name", "post", "location", "stints", "total_hours"],
        ...report!.map((p) => [
          p.callsign,
          p.name,
          p.post,
          p.location,
          p.stints.map((s) => `${fmt(s.start)} - ${s.end ? fmt(s.end) : "open"}`).join("; "),
          fmtHours(p.total_seconds),
        ]),
      ]),
    );
  }

  function logCsv() {
    downloadCsv(
      `event-${event.id}-log.csv`,
      toCsv([
        ["seq", "time", "type", "callsign", "actor", "message"],
        ...log.map((e) => [e.seq, fmt(e.created_at), e.entry_type, e.callsign, e.actor, e.message]),
      ]),
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-4xl">
      <div className="flex items-center gap-3 mb-1 print:hidden">
        <Link to={`/events/${event.id}`} className="text-text-muted hover:text-accent text-sm">
          ← Dashboard
        </Link>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="secondary" onClick={participantsCsv}>Participants CSV</Button>
          <Button size="sm" variant="secondary" onClick={logCsv}>Log CSV</Button>
          <Button size="sm" onClick={() => window.print()}>Print</Button>
        </div>
      </div>

      <h1 className="text-xl font-semibold text-text-primary">{event.name} — After-action report</h1>
      <p className="text-sm text-text-muted mb-6">
        {event.event_type === "public_service" ? "Public service" : "Emergency"} event
        {event.activated_at && ` · activated ${fmt(event.activated_at)}`}
        {event.closed_at && ` · closed ${fmt(event.closed_at)}`}
      </p>

      <h2 className="text-base font-semibold text-text-primary mb-2">Participants</h2>
      <table className="w-full text-sm mb-8">
        <thead>
          <tr className="text-left text-text-muted border-b border-border">
            <th className="py-1.5 pr-3">Callsign</th>
            <th className="py-1.5 pr-3">Name</th>
            <th className="py-1.5 pr-3">Post / location</th>
            <th className="py-1.5 pr-3">Stints</th>
            <th className="py-1.5">Total (h:mm)</th>
          </tr>
        </thead>
        <tbody>
          {report.map((p) => (
            <tr key={p.callsign} className="border-b border-border align-top">
              <td className="py-1.5 pr-3 font-mono">{p.callsign}</td>
              <td className="py-1.5 pr-3">{p.name ?? "—"}</td>
              <td className="py-1.5 pr-3">{p.post ?? p.location ?? "—"}</td>
              <td className="py-1.5 pr-3">
                {p.stints.map((s, i) => (
                  <div key={i}>
                    {fmt(s.start)} → {s.end ? fmt(s.end) : "(open)"}
                  </div>
                ))}
              </td>
              <td className="py-1.5">{fmtHours(p.total_seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className="text-base font-semibold text-text-primary mb-2">Event log</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-text-muted border-b border-border">
            <th className="py-1.5 pr-3">Time</th>
            <th className="py-1.5 pr-3">Type</th>
            <th className="py-1.5 pr-3">Callsign</th>
            <th className="py-1.5 pr-3">By</th>
            <th className="py-1.5">Entry</th>
          </tr>
        </thead>
        <tbody>
          {log.map((e) => (
            <tr key={e.seq} className="border-b border-border">
              <td className="py-1.5 pr-3 whitespace-nowrap text-text-muted">{fmt(e.created_at)}</td>
              <td className="py-1.5 pr-3 text-text-muted">{e.entry_type}</td>
              <td className="py-1.5 pr-3 font-mono">{e.callsign ?? ""}</td>
              <td className="py-1.5 pr-3 font-mono">{e.actor}</td>
              <td className="py-1.5">{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
