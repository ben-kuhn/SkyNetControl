import { useEvent } from "../context/EventProvider";

export function RequireEventRole({ min, children }: { min: "read" | "control"; children: React.ReactNode }) {
  const { isControl } = useEvent();
  if (min === "control" && !isControl) {
    return <div className="p-8 text-center text-text-muted">You don't have control of this event.</div>;
  }
  return <>{children}</>;
}
