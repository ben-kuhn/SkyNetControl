import { useEffect, useState } from "react";
import {
  createActivity,
  deleteActivity,
  updateActivity,
  type ActivityInput,
} from "../../api/activities";
import type { Activity } from "../../types";
import { useToast } from "../../context/ToastContext";
import { useAuth } from "../../hooks/useAuth";

function formatLongDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

type Mode = "view" | "edit" | "create";

interface Props {
  activity: Activity | null;
  initialMode: Mode;
  onClose: () => void;
  onSaved: (a: Activity) => void;
  onDeleted: (id: number) => void;
  hasUnsavedRef?: { current: boolean };
}

export function ActivityDetailPanel({
  activity,
  initialMode,
  onClose,
  onSaved,
  onDeleted,
  hasUnsavedRef,
}: Props) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [title, setTitle] = useState(activity?.title ?? "");
  const [description, setDescription] = useState(activity?.description ?? "");
  const [instructions, setInstructions] = useState(activity?.instructions ?? "");
  const [tagsText, setTagsText] = useState(
    activity?.tags.map((t) => t.name).join(", ") ?? "",
  );
  const [submitting, setSubmitting] = useState(false);

  const { user } = useAuth();
  const { addToast } = useToast();

  useEffect(() => {
    setMode(initialMode);
    setTitle(activity?.title ?? "");
    setDescription(activity?.description ?? "");
    setInstructions(activity?.instructions ?? "");
    setTagsText(activity?.tags.map((t) => t.name).join(", ") ?? "");
  }, [activity?.id, initialMode]);

  useEffect(() => {
    if (!hasUnsavedRef) return;
    if (mode === "view") {
      hasUnsavedRef.current = false;
      return;
    }
    const dirty =
      title !== (activity?.title ?? "") ||
      description !== (activity?.description ?? "") ||
      instructions !== (activity?.instructions ?? "") ||
      tagsText !== (activity?.tags.map((t) => t.name).join(", ") ?? "");
    hasUnsavedRef.current = dirty;
  }, [mode, title, description, instructions, tagsText, activity, hasUnsavedRef]);

  const canDelete = user?.role === "admin" && activity != null && !activity.is_default;
  const canEdit =
    activity == null ||
    user?.role === "admin" ||
    user?.role === "net_control";

  const parseTags = (s: string): string[] => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const part of s.split(",")) {
      const t = part.trim();
      if (t && !seen.has(t.toLowerCase())) {
        seen.add(t.toLowerCase());
        out.push(t);
      }
    }
    return out;
  };

  const handleSave = async () => {
    if (!title.trim() || !description.trim() || !instructions.trim()) {
      addToast("Title, description, and instructions are required.", "error");
      return;
    }
    setSubmitting(true);
    const input: ActivityInput = {
      title: title.trim(),
      description,
      instructions,
      tag_names: parseTags(tagsText),
    };
    try {
      const saved = activity
        ? await updateActivity(activity.id, input)
        : await createActivity(input);
      addToast(activity ? "Activity updated." : "Activity created.", "success");
      onSaved(saved);
      setMode("view");
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Save failed", "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!activity || activity.is_default) return;
    if (!confirm(`Delete activity "${activity.title}"?`)) return;
    try {
      await deleteActivity(activity.id);
      addToast("Activity deleted.", "success");
      onDeleted(activity.id);
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Delete failed", "error");
    }
  };

  return (
    <div className="border border-border rounded-lg p-4 bg-bg-surface">
      <div className="flex items-start justify-between mb-3 pb-3 border-b border-border">
        <div>
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            {mode === "create" ? "New activity" : activity?.title ?? ""}
            {activity?.is_default && (
              <span className="inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium bg-accent/[0.12] text-accent">
                default
              </span>
            )}
          </h2>
          {activity && mode === "view" && (
            <p className="text-xs text-text-muted mt-0.5">
              Created {formatLongDate(activity.created_at)}
              {activity.last_used_at && ` · Last used ${formatLongDate(activity.last_used_at)}`}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary p-1 rounded"
          aria-label="Close detail panel"
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {mode === "view" && activity && (
        <>
          {activity.tags.length > 0 && (
            <div className="mb-3 flex flex-wrap gap-1">
              {activity.tags.map((t) => (
                <span
                  key={t.id}
                  className="inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium bg-bg-elevated text-text-secondary"
                >
                  {t.name}
                </span>
              ))}
            </div>
          )}
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Description</label>
            <p className="text-sm text-text-primary whitespace-pre-wrap">{activity.description}</p>
          </div>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Instructions</label>
            <pre className="text-[0.8125rem] text-text-primary whitespace-pre-wrap font-mono bg-bg-elevated/50 p-3 rounded">
              {activity.instructions}
            </pre>
          </div>
          <div className="flex gap-2 pt-3 border-t border-border">
            {canEdit && (
              <button
                onClick={() => setMode("edit")}
                className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
              >
                Edit
              </button>
            )}
            <button
              onClick={handleDelete}
              disabled={!canDelete}
              className="px-3 py-1.5 text-sm border border-warning/40 rounded-md text-warning hover:bg-warning/[0.08] disabled:opacity-40 disabled:hover:bg-transparent"
              title={
                activity.is_default
                  ? "Cannot delete the default activity"
                  : user?.role !== "admin"
                    ? "Only admins can delete activities"
                    : "Delete"
              }
            >
              Delete
            </button>
          </div>
        </>
      )}

      {(mode === "edit" || mode === "create") && (
        <>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Tags</label>
            <input
              type="text"
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              placeholder="Comma-separated tags"
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={4}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="mb-3">
            <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Instructions</label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={10}
              className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
            />
          </div>
          <div className="flex gap-2 pt-3 border-t border-border">
            <button
              onClick={handleSave}
              disabled={submitting}
              className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => (mode === "create" ? onClose() : setMode("view"))}
              className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated"
            >
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  );
}
