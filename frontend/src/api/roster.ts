// TODO(Task 13): replace DEFAULT_NET_SLUG with useCurrentNet() hook once
// the CurrentNetContext is available. Task 14 will wire slug into the API calls.
const DEFAULT_NET_SLUG = "default-net";

import { apiFetch } from "./client";
import type { Roster, RosterTemplate } from "../types";

// --- Rosters ---

export async function fetchRosters(netSlug: string = DEFAULT_NET_SLUG): Promise<Roster[]> {
  return apiFetch<Roster[]>(`/nets/${netSlug}/roster/`);
}

export async function updateRosterDraft(
  id: number,
  body: Partial<Pick<
    Roster,
    "content_subject" | "content_header" | "content_welcome" | "content_comments" | "content_footer"
  >>,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<Roster> {
  return apiFetch<Roster>(`/nets/${netSlug}/roster/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function approveRoster(id: number, netSlug: string = DEFAULT_NET_SLUG): Promise<Roster> {
  return apiFetch<Roster>(`/nets/${netSlug}/roster/${id}/approve`, { method: "POST" });
}

export async function sendRoster(id: number, netSlug: string = DEFAULT_NET_SLUG): Promise<Roster> {
  return apiFetch<Roster>(`/nets/${netSlug}/roster/${id}/send`, { method: "POST" });
}

export async function skipRoster(id: number, netSlug: string = DEFAULT_NET_SLUG): Promise<Roster> {
  return apiFetch<Roster>(`/nets/${netSlug}/roster/${id}/skip`, { method: "POST" });
}

export async function regenerateRosterDraft(id: number, netSlug: string = DEFAULT_NET_SLUG): Promise<Roster> {
  return apiFetch<Roster>(`/nets/${netSlug}/roster/${id}/regenerate`, { method: "POST" });
}

export async function generateRosterDraft(sessionId: number, netSlug: string = DEFAULT_NET_SLUG): Promise<Roster> {
  return apiFetch<Roster>(`/nets/${netSlug}/roster/generate/${sessionId}`, { method: "POST" });
}

export async function previewRoster(id: number, netSlug: string = DEFAULT_NET_SLUG): Promise<{ text: string }> {
  return apiFetch<{ text: string }>(`/nets/${netSlug}/roster/${id}/preview`);
}

// --- Templates ---

export async function fetchRosterTemplates(netSlug: string = DEFAULT_NET_SLUG): Promise<RosterTemplate[]> {
  return apiFetch<RosterTemplate[]>(`/nets/${netSlug}/roster/templates`);
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

export async function createRosterTemplate(
  input: RosterTemplateInput,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<RosterTemplate> {
  return apiFetch<RosterTemplate>(`/nets/${netSlug}/roster/templates`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateRosterTemplate(
  id: number,
  input: Partial<RosterTemplateInput>,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<RosterTemplate> {
  return apiFetch<RosterTemplate>(`/nets/${netSlug}/roster/templates/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteRosterTemplate(id: number, netSlug: string = DEFAULT_NET_SLUG): Promise<void> {
  await apiFetch<void>(`/nets/${netSlug}/roster/templates/${id}`, { method: "DELETE" });
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

export async function fetchRosterTemplateDefaults(netSlug: string = DEFAULT_NET_SLUG): Promise<RosterTemplateDefault[]> {
  return apiFetch<RosterTemplateDefault[]>(`/nets/${netSlug}/roster/template-defaults`);
}
