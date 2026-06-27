import { useEffect, useMemo, useRef, useState } from "react";
import { fetchActivities } from "../api/activities";
import { useCurrentNet } from "../hooks/useCurrentNet";
import type { Activity } from "../types";
import { ActivityDetailPanel } from "./activities/ActivityDetailPanel";
import { BrainstormPanel } from "./activities/BrainstormPanel";

type SortKey = "title" | "last_used_at";
type SortDir = "asc" | "desc";
type RightPane =
  | { kind: "empty" }
  | { kind: "detail"; activityId: number | null; mode: "view" | "edit" | "create" }
  | { kind: "brainstorm" };

function formatShortDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function ActivitiesPage() {
  const { slug } = useCurrentNet();
  const [activities, setActivities] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("title");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [pane, setPane] = useState<RightPane>({ kind: "empty" });

  const detailUnsavedRef = useRef(false);

  useEffect(() => {
    setLoading(true);
    setActivities([]);
    fetchActivities(slug)
      .then((data) => setActivities(data))
      .catch((e: any) => setError(e?.message ?? "Failed to load activities"))
      .finally(() => setLoading(false));
  }, [slug]);

  const sorted = useMemo(() => {
    const cmp = (a: Activity, b: Activity): number => {
      if (sortKey === "title") {
        const r = a.title.localeCompare(b.title);
        return sortDir === "asc" ? r : -r;
      }
      const av = a.last_used_at ?? "";
      const bv = b.last_used_at ?? "";
      if (av === bv) return 0;
      const r = av < bv ? -1 : 1;
      return sortDir === "asc" ? r : -r;
    };
    return [...activities].sort(cmp);
  }, [activities, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("asc");
    }
  };
  const sortIndicator = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  const selectedActivity =
    pane.kind === "detail" && pane.activityId !== null
      ? activities.find((a) => a.id === pane.activityId) ?? null
      : null;

  const openDetail = (a: Activity) => {
    if (pane.kind === "detail" && pane.activityId === a.id) {
      setPane({ kind: "empty" });
    } else {
      setPane({ kind: "detail", activityId: a.id, mode: "view" });
    }
  };

  const openCreate = () => {
    setPane({ kind: "detail", activityId: null, mode: "create" });
  };

  const openBrainstorm = () => {
    if (
      pane.kind === "detail" &&
      detailUnsavedRef.current &&
      !confirm("Discard your unsaved edits and start a brainstorm?")
    ) {
      return;
    }
    setPane({ kind: "brainstorm" });
  };

  const handleSaved = (saved: Activity) => {
    setActivities((prev) => {
      const idx = prev.findIndex((a) => a.id === saved.id);
      if (idx === -1) return [saved, ...prev];
      return prev.map((a) => (a.id === saved.id ? saved : a));
    });
    setPane({ kind: "detail", activityId: saved.id, mode: "view" });
  };

  const handleDeleted = (id: number) => {
    setActivities((prev) => prev.filter((a) => a.id !== id));
    setPane({ kind: "empty" });
  };

  const isMobile = typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-text-primary">Activities</h1>
        <div className="flex gap-2">
          <button
            onClick={openCreate}
            className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
          >
            + New activity
          </button>
          <button
            onClick={openBrainstorm}
            className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90"
          >
            + Brainstorm new activity
          </button>
        </div>
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
                    <th
                      onClick={() => toggleSort("title")}
                      className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border cursor-pointer select-none hover:text-text-primary"
                    >
                      Title{sortIndicator("title")}
                    </th>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">
                      Tags
                    </th>
                    <th
                      onClick={() => toggleSort("last_used_at")}
                      className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border cursor-pointer select-none hover:text-text-primary"
                    >
                      Last used{sortIndicator("last_used_at")}
                    </th>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">
                      Default
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((a) => {
                    const isSelected =
                      pane.kind === "detail" && pane.activityId === a.id;
                    return (
                      <tr
                        key={a.id}
                        onClick={() => openDetail(a)}
                        className={`border-b border-border last:border-b-0 cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-accent/[0.08] border-l-2 border-l-accent"
                            : "hover:bg-bg-elevated/50"
                        }`}
                      >
                        <td className="px-3 py-2.5 font-semibold text-text-primary">
                          {a.title}
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex flex-wrap gap-1">
                            {a.tags.map((t) => (
                              <span
                                key={t.id}
                                className="inline-block text-[0.6875rem] px-1.5 py-0.5 rounded-full font-medium bg-bg-elevated text-text-secondary"
                              >
                                {t.name}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 text-text-secondary tabular-nums">
                          {formatShortDate(a.last_used_at)}
                        </td>
                        <td className="px-3 py-2.5">
                          {a.is_default && (
                            <span className="inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium bg-accent/[0.12] text-accent">
                              default
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {sorted.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-3 py-8 text-center text-text-muted text-sm">
                        No activities yet. Create one or start a brainstorm.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {pane.kind === "detail" && (
            <div className="flex-1 min-w-0">
              <ActivityDetailPanel
                activity={selectedActivity}
                initialMode={pane.mode}
                onClose={() => setPane({ kind: "empty" })}
                onSaved={handleSaved}
                onDeleted={handleDeleted}
                hasUnsavedRef={detailUnsavedRef}
              />
            </div>
          )}

          {pane.kind === "brainstorm" && !isMobile && (
            <div className="flex-1 min-w-0">
              <BrainstormPanel
                modal={false}
                onClose={() => setPane({ kind: "empty" })}
                onApproved={(a) => {
                  setActivities((prev) => [a, ...prev]);
                  setPane({ kind: "detail", activityId: a.id, mode: "view" });
                }}
              />
            </div>
          )}

          {pane.kind === "brainstorm" && isMobile && (
            <BrainstormPanel
              modal={true}
              onClose={() => setPane({ kind: "empty" })}
              onApproved={(a) => {
                setActivities((prev) => [a, ...prev]);
                setPane({ kind: "detail", activityId: a.id, mode: "view" });
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
