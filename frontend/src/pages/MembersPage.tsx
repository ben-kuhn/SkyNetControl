import { useEffect, useMemo, useState } from "react";
import { fetchMembers } from "../api/members";
import type { Member } from "../types";

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
              {filteredSorted.map((m) => (
                <tr
                  key={m.callsign}
                  className="border-b border-border last:border-b-0 hover:bg-bg-elevated/50"
                >
                  <td className="px-3 py-2.5 font-mono font-semibold text-text-primary">{m.callsign}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{m.name}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{formatDate(m.first_check_in_date)}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{formatDate(m.last_check_in_date)}</td>
                  <td className="px-3 py-2.5 text-right text-text-secondary">{m.total_check_ins}</td>
                </tr>
              ))}
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
      )}
    </div>
  );
}
