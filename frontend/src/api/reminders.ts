import { apiFetch } from "./client";
import type { Reminder, ReminderTemplate, ReminderTemplateType } from "../types";

// --- Reminders ---

export async function fetchReminders(): Promise<Reminder[]> {
  return apiFetch<Reminder[]>("/reminders/");
}

export async function updateReminderDraft(
  id: number,
  body: { content_subject?: string; content_body?: string },
): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function approveReminder(id: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}/approve`, { method: "POST" });
}

export async function sendReminder(id: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}/send`, { method: "POST" });
}

export async function skipReminder(id: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}/skip`, { method: "POST" });
}

export async function regenerateReminderDraft(id: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/${id}/regenerate`, { method: "POST" });
}

export async function generateReminderDraft(sessionId: number): Promise<Reminder> {
  return apiFetch<Reminder>(`/reminders/generate/${sessionId}`, { method: "POST" });
}

// --- Templates ---

export async function fetchReminderTemplates(): Promise<ReminderTemplate[]> {
  return apiFetch<ReminderTemplate[]>("/reminders/templates");
}

export interface TemplateInput {
  name: string;
  template_type: ReminderTemplateType;
  subject_template: string;
  body_template: string;
  lead_time_days: number;
  is_default: boolean;
}

export async function createReminderTemplate(input: TemplateInput): Promise<ReminderTemplate> {
  return apiFetch<ReminderTemplate>("/reminders/templates", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateReminderTemplate(
  id: number,
  input: Partial<TemplateInput>,
): Promise<ReminderTemplate> {
  return apiFetch<ReminderTemplate>(`/reminders/templates/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteReminderTemplate(id: number): Promise<void> {
  await apiFetch<void>(`/reminders/templates/${id}`, { method: "DELETE" });
}
