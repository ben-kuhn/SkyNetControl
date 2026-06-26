import { useCurrentNet } from "../hooks/useCurrentNet";

/**
 * Per-net settings page (name, visibility, templates). Stub — full UI in Task 15.
 */
export function NetSettingsPage() {
  const { net } = useCurrentNet();

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-bold text-text-primary mb-4">
        Net Settings{net ? `: ${net.name}` : ""}
      </h1>
      <p className="text-text-muted text-sm">
        Per-net settings UI coming soon.
      </p>
    </div>
  );
}
