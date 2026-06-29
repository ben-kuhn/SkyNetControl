import { useState } from "react";
import { Button } from "./Button";

export interface ConfigField {
  key: string;
  label: string;
  type?: "text" | "boolean" | "multiselect";
  placeholder?: string;
  helpText: string;
  mono?: boolean;
  secret?: boolean;
  options?: { value: string; label: string }[];
  visibleWhen?: (values: Record<string, string>) => boolean;
}

interface SettingsSectionProps {
  title: string;
  fields: ConfigField[];
  values: Record<string, string>;
  savedValues: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onSave: (keys: string[]) => Promise<void>;
  saving: boolean;
  children?: React.ReactNode;
}

function parseStringArray(raw: string): string[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}

function FieldRow({
  field,
  value,
  onChange,
}: {
  field: ConfigField;
  value: string;
  onChange: (value: string) => void;
}) {
  const [showSecret, setShowSecret] = useState(false);
  const type = field.type ?? "text";

  let input: React.ReactNode;
  if (type === "boolean") {
    const checked = value === "true";
    input = (
      <label className="inline-flex items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked ? "true" : "false")}
          className="accent-accent"
        />
        <span className="text-text-secondary">{checked ? "Enabled" : "Disabled"}</span>
      </label>
    );
  } else if (type === "multiselect") {
    const selected = parseStringArray(value);
    const toggle = (v: string) => {
      const next = selected.includes(v)
        ? selected.filter((s) => s !== v)
        : [...selected, v];
      onChange(JSON.stringify(next));
    };
    input = (
      <div className="flex flex-col gap-1">
        {(field.options ?? []).map((opt) => (
          <label key={opt.value} className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={selected.includes(opt.value)}
              onChange={() => toggle(opt.value)}
              className="accent-accent"
            />
            <span className="text-text-secondary">{opt.label}</span>
          </label>
        ))}
      </div>
    );
  } else {
    input = (
      <div className="relative max-w-md">
        <input
          type={field.secret && !showSecret ? "password" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          className={`w-full bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted ${
            field.mono ? "font-mono" : ""
          } ${field.secret ? "pr-10" : ""}`}
        />
        {field.secret && (
          <button
            type="button"
            onClick={() => setShowSecret(!showSecret)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary text-xs px-1"
            title={showSecret ? "Hide" : "Show"}
          >
            {showSecret ? "Hide" : "Show"}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mb-4 last:mb-0">
      <label className="block text-sm text-text-secondary mb-1">{field.label}</label>
      {input}
      <div className="text-xs text-text-muted mt-1">{field.helpText}</div>
    </div>
  );
}

export function SettingsSection({
  title,
  fields,
  values,
  savedValues,
  onChange,
  onSave,
  saving,
  children,
}: SettingsSectionProps) {
  const visibleFields = fields.filter(
    (f) => !f.visibleWhen || f.visibleWhen(values),
  );
  if (visibleFields.length === 0 && !children) return null;

  const visibleKeys = visibleFields.map((f) => f.key);
  const dirty = visibleKeys.some(
    (k) => (values[k] ?? "") !== (savedValues[k] ?? ""),
  );

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
      <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
        {title}
      </h2>
      {visibleFields.map((field) => (
        <FieldRow
          key={field.key}
          field={field}
          value={values[field.key] ?? ""}
          onChange={(v) => onChange(field.key, v)}
        />
      ))}
      <div className="flex items-center gap-2 mt-2">
        <div className="flex-1">{children}</div>
        <Button
          size="sm"
          variant={dirty ? "primary" : "secondary"}
          onClick={() => onSave(visibleKeys)}
          loading={saving}
          disabled={!dirty}
        >
          Save
        </Button>
      </div>
    </div>
  );
}
