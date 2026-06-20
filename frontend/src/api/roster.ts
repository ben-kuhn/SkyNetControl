import { apiFetch } from "./client";
import type { Roster, RosterTemplate } from "../types";

// --- Rosters ---

export async function fetchRosters(): Promise<Roster[]> {
  return apiFetch<Roster[]>("/roster/");
}

export async function updateRosterDraft(
  id: number,
  body: Partial<Pick<
    Roster,
    "content_subject" | "content_header" | "content_welcome" | "content_comments" | "content_footer"
  >>,
): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function approveRoster(id: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}/approve`, { method: "POST" });
}

export async function sendRoster(id: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}/send`, { method: "POST" });
}

export async function skipRoster(id: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}/skip`, { method: "POST" });
}

export async function regenerateRosterDraft(id: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/${id}/regenerate`, { method: "POST" });
}

export async function generateRosterDraft(sessionId: number): Promise<Roster> {
  return apiFetch<Roster>(`/roster/generate/${sessionId}`, { method: "POST" });
}

export async function previewRoster(id: number): Promise<{ text: string }> {
  return apiFetch<{ text: string }>(`/roster/${id}/preview`);
}

// --- Templates ---

export async function fetchRosterTemplates(): Promise<RosterTemplate[]> {
  return apiFetch<RosterTemplate[]>("/roster/templates");
}

export interface RosterTemplateInput {
  name: string;
  subject_template: string;
  header_template: string;
  welcome_template: string;
  comments_template: string;
  footer_template: string;
  lead_time_days: number;
  is_default: boolean;
}

export async function createRosterTemplate(input: RosterTemplateInput): Promise<RosterTemplate> {
  return apiFetch<RosterTemplate>("/roster/templates", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateRosterTemplate(
  id: number,
  input: Partial<RosterTemplateInput>,
): Promise<RosterTemplate> {
  return apiFetch<RosterTemplate>(`/roster/templates/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteRosterTemplate(id: number): Promise<void> {
  await apiFetch<void>(`/roster/templates/${id}`, { method: "DELETE" });
}

export interface RosterTemplateDefault {
  name: string;
  subject_template: string;
  header_template: string;
  welcome_template: string;
  comments_template: string;
  footer_template: string;
  lead_time_days: number;
}

export async function fetchRosterTemplateDefaults(): Promise<RosterTemplateDefault[]> {
  return apiFetch<RosterTemplateDefault[]>("/roster/template-defaults");
}
