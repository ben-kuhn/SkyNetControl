import { apiFetch } from "./client";
import type { Activity, ActivityTag, ChatMessage, ChatSession } from "../types";

export interface ActivityInput {
  title: string;
  description: string;
  instructions: string;
  tag_names: string[];
}

export async function fetchActivities(netSlug: string): Promise<Activity[]> {
  return apiFetch<Activity[]>(`/nets/${netSlug}/activities/`);
}

export async function fetchActivity(id: number, netSlug: string): Promise<Activity> {
  return apiFetch<Activity>(`/nets/${netSlug}/activities/${id}`);
}

export async function createActivity(input: ActivityInput, netSlug: string): Promise<Activity> {
  return apiFetch<Activity>(`/nets/${netSlug}/activities/`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateActivity(
  id: number,
  input: Partial<ActivityInput>,
  netSlug: string,
): Promise<Activity> {
  return apiFetch<Activity>(`/nets/${netSlug}/activities/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteActivity(id: number, netSlug: string): Promise<void> {
  await apiFetch<void>(`/nets/${netSlug}/activities/${id}`, { method: "DELETE" });
}

export async function fetchActivityTags(netSlug: string): Promise<ActivityTag[]> {
  return apiFetch<ActivityTag[]>(`/nets/${netSlug}/activities/tags`);
}

// --- Chat ---

export async function startChatSession(netSlug: string): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/nets/${netSlug}/activities/chat/sessions`, { method: "POST" });
}

export async function sendChatMessage(
  sessionId: number,
  content: string,
  netSlug: string,
): Promise<{ user_message: ChatMessage; assistant_message: ChatMessage }> {
  return apiFetch(`/nets/${netSlug}/activities/chat/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function approveChatSession(
  sessionId: number,
  input: ActivityInput,
  netSlug: string,
): Promise<Activity> {
  return apiFetch<Activity>(`/nets/${netSlug}/activities/chat/sessions/${sessionId}/approve`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}
