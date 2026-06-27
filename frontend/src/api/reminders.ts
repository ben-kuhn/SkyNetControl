import { apiFetch } from "./client";
import type { Reminder, ReminderTemplate, ReminderTemplateType } from "../types";

// --- Reminders ---

export async function fetchReminders(netSlug: string): Promise<Reminder[]> {
  return apiFetch<Reminder[]>(`/nets/${netSlug}/reminders/`);
}

export async function updateReminderDraft(
  id: number,
  body: { content_subject?: string; content_body?: string },
  netSlug: string,
): Promise<Reminder> {
  return apiFetch<Reminder>(`/nets/${netSlug}/reminders/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function approveReminder(id: number, netSlug: string): Promise<Reminder> {
  return apiFetch<Reminder>(`/nets/${netSlug}/reminders/${id}/approve`, { method: "POST" });
}

export async function sendReminder(id: number, netSlug: string): Promise<Reminder> {
  return apiFetch<Reminder>(`/nets/${netSlug}/reminders/${id}/send`, { method: "POST" });
}

export async function skipReminder(id: number, netSlug: string): Promise<Reminder> {
  return apiFetch<Reminder>(`/nets/${netSlug}/reminders/${id}/skip`, { method: "POST" });
}

export async function regenerateReminderDraft(id: number, netSlug: string): Promise<Reminder> {
  return apiFetch<Reminder>(`/nets/${netSlug}/reminders/${id}/regenerate`, { method: "POST" });
}

export async function generateReminderDraft(sessionId: number, netSlug: string): Promise<Reminder> {
  return apiFetch<Reminder>(`/nets/${netSlug}/reminders/generate/${sessionId}`, { method: "POST" });
}

// --- Templates ---

export async function fetchReminderTemplates(netSlug: string): Promise<ReminderTemplate[]> {
  return apiFetch<ReminderTemplate[]>(`/nets/${netSlug}/reminders/templates`);
}

export interface TemplateInput {
  name: string;
  template_type: ReminderTemplateType;
  subject_template: string;
  body_template: string;
  lead_time_days: number;
  is_default: boolean;
}

export async function createReminderTemplate(
  input: TemplateInput,
  netSlug: string,
): Promise<ReminderTemplate> {
  return apiFetch<ReminderTemplate>(`/nets/${netSlug}/reminders/templates`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateReminderTemplate(
  id: number,
  input: Partial<TemplateInput>,
  netSlug: string,
): Promise<ReminderTemplate> {
  return apiFetch<ReminderTemplate>(`/nets/${netSlug}/reminders/templates/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteReminderTemplate(id: number, netSlug: string): Promise<void> {
  await apiFetch<void>(`/nets/${netSlug}/reminders/templates/${id}`, { method: "DELETE" });
}

export interface ReminderTemplateDefault {
  name: string;
  template_type: ReminderTemplateType;
  subject_template: string;
  body_template: string;
  lead_time_days: number;
}

export async function fetchReminderTemplateDefaults(
  netSlug: string,
): Promise<ReminderTemplateDefault[]> {
  return apiFetch<ReminderTemplateDefault[]>(`/nets/${netSlug}/reminders/template-defaults`);
}
