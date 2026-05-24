import { useState } from "react";
import { DraftsTab } from "./roster/DraftsTab";
import { TemplatesTab } from "./roster/TemplatesTab";

type TopTab = "drafts" | "templates";

export function RosterPage() {
  const [tab, setTab] = useState<TopTab>("drafts");

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-text-primary mb-4">Roster</h1>

      <div className="flex border-b border-border mb-4">
        <TabButton active={tab === "drafts"} onClick={() => setTab("drafts")}>Drafts</TabButton>
        <TabButton active={tab === "templates"} onClick={() => setTab("templates")}>Templates</TabButton>
      </div>

      {tab === "drafts" ? <DraftsTab /> : <TemplatesTab />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm border-b-2 transition-colors ${
        active
          ? "border-accent text-text-primary font-medium"
          : "border-transparent text-text-muted hover:text-text-primary"
      }`}
    >
      {children}
    </button>
  );
}
