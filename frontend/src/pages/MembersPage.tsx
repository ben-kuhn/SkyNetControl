import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchMembers, fetchMemberHistory } from "../api/members";
import type { Member, MemberCheckin } from "../types";

type SortKey = "callsign" | "name" | "first_check_in_date" | "last_check_in_date" | "total_check_ins";
type SortDir = "asc" | "desc";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function MembersPage() {
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("callsign");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [selectedCallsign, setSelectedCallsign] = useState<string | null>(null);

  useEffect(() => {
    fetchMembers()
      .then((data) => {
        setMembers(data);
        setError(null);
      })
      .catch((e) => setError(e?.message ?? "Failed to load members"))
      .finally(() => setLoading(false));
  }, []);

  const filteredSorted = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? members.filter(
          (m) => m.callsign.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
        )
      : members;
    const sorted = [...filtered].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [members, search, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const sortIndicator = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  const selectedMember = selectedCallsign
    ? members.find((m) => m.callsign === selectedCallsign) ?? null
    : null;

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-text-primary mb-4">Members</h1>

      <div className="mb-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search callsign or name…"
          className="w-full max-w-sm px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent"
        />
      </div>

      {loading && <p className="text-text-muted text-sm py-4">Loading…</p>}
      {error && <p className="text-error text-sm py-4">{error}</p>}

      {!loading && !error && (
        <div className="flex flex-col lg:flex-row gap-4">
          <div className="flex-1 min-w-0">
            <div className="border border-border rounded-lg overflow-auto">
              <table className="w-full text-[0.8125rem] border-collapse">
                <thead className="bg-bg-elevated">
                  <tr>
                    {([
                      ["callsign", "Callsign"],
                      ["name", "Name"],
                      ["first_check_in_date", "First check-in"],
                      ["last_check_in_date", "Last check-in"],
                      ["total_check_ins", "Total"],
                    ] as [SortKey, string][]).map(([key, label]) => (
                      <th
                        key={key}
                        onClick={() => toggleSort(key)}
                        className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border cursor-pointer select-none hover:text-text-primary"
                      >
                        {label}{sortIndicator(key)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredSorted.map((m) => {
                    const isSelected = m.callsign === selectedCallsign;
                    return (
                      <tr
                        key={m.callsign}
                        onClick={() => setSelectedCallsign(isSelected ? null : m.callsign)}
                        className={`border-b border-border last:border-b-0 cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-accent/[0.08] border-l-2 border-l-accent"
                            : "hover:bg-bg-elevated/50"
                        }`}
                      >
                        <td className="px-3 py-2.5 font-mono font-semibold text-text-primary">{m.callsign}</td>
                        <td className="px-3 py-2.5 text-text-secondary">{m.name}</td>
                        <td className="px-3 py-2.5 text-text-secondary">{formatDate(m.first_check_in_date)}</td>
                        <td className="px-3 py-2.5 text-text-secondary">{formatDate(m.last_check_in_date)}</td>
                        <td className="px-3 py-2.5 text-right text-text-secondary">{m.total_check_ins}</td>
                      </tr>
                    );
                  })}
                  {filteredSorted.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-8 text-center text-text-muted text-sm">
                        {members.length === 0
                          ? "No members yet. Members are added automatically when their check-ins are approved."
                          : "No members match your search."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {selectedMember && (
            <div className="flex-1 min-w-0">
              <MemberDetailPanel
                member={selectedMember}
                onClose={() => setSelectedCallsign(null)}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MemberDetailPanel({ member, onClose }: { member: Member; onClose: () => void }) {
  const navigate = useNavigate();
  const [history, setHistory] = useState<MemberCheckin[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);

  useEffect(() => {
    setHistoryLoading(true);
    setHistoryError(null);
    setHistory(null);
    fetchMemberHistory(member.callsign)
      .then(setHistory)
      .catch((e) => setHistoryError(e?.message ?? "Failed to load history"))
      .finally(() => setHistoryLoading(false));
  }, [member.callsign]);

  return (
    <div className="border border-border rounded-lg p-4 bg-bg-surface">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h2 className="text-lg font-mono font-semibold text-text-primary">{member.callsign}</h2>
          <p className="text-sm text-text-secondary">{member.name}</p>
        </div>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary p-1 rounded"
          aria-label="Close detail panel"
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="grid grid-cols-3 gap-3 text-xs mb-4 border-b border-border pb-3">
        <div>
          <div className="text-text-muted uppercase tracking-wider">First</div>
          <div className="text-text-primary">{formatDate(member.first_check_in_date)}</div>
        </div>
        <div>
          <div className="text-text-muted uppercase tracking-wider">Last</div>
          <div className="text-text-primary">{formatDate(member.last_check_in_date)}</div>
        </div>
        <div>
          <div className="text-text-muted uppercase tracking-wider">Total</div>
          <div className="text-text-primary">{member.total_check_ins}</div>
        </div>
      </div>

      <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">History</h3>

      {historyLoading && <p className="text-text-muted text-sm">Loading…</p>}
      {historyError && <p className="text-error text-sm">{historyError}</p>}
      {history && history.length === 0 && (
        <p className="text-text-muted text-sm italic">No check-ins recorded for this callsign.</p>
      )}
      {history && history.length > 0 && (
        <ul className="space-y-1.5">
          {history.map((c) => (
            <li
              key={c.id}
              onClick={() => navigate(`/checkins?session=${c.session_id}`)}
              className="px-2.5 py-1.5 rounded cursor-pointer hover:bg-bg-elevated/50 text-sm flex items-baseline gap-3"
              title={c.comments ?? ""}
            >
              <span className="text-text-primary font-medium w-28">{formatDate(c.session_date)}</span>
              <span className="text-text-secondary">{c.mode}</span>
              {c.comments && (
                <span className="text-text-muted text-xs italic truncate">{c.comments}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
