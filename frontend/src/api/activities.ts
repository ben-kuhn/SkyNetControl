import { apiFetch } from "./client";
import type { Activity, ActivityTag, ChatMessage, ChatSession } from "../types";

export interface ActivityInput {
  title: string;
  description: string;
  instructions: string;
  tag_names: string[];
}

export async function fetchActivities(): Promise<Activity[]> {
  return apiFetch<Activity[]>("/activities/");
}

export async function fetchActivity(id: number): Promise<Activity> {
  return apiFetch<Activity>(`/activities/${id}`);
}

export async function createActivity(input: ActivityInput): Promise<Activity> {
  return apiFetch<Activity>("/activities/", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateActivity(
  id: number,
  input: Partial<ActivityInput>,
): Promise<Activity> {
  return apiFetch<Activity>(`/activities/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteActivity(id: number): Promise<void> {
  await apiFetch<void>(`/activities/${id}`, { method: "DELETE" });
}

export async function fetchActivityTags(): Promise<ActivityTag[]> {
  return apiFetch<ActivityTag[]>("/activities/tags");
}

// --- Chat ---

export async function startChatSession(): Promise<ChatSession> {
  return apiFetch<ChatSession>("/activities/chat/sessions", { method: "POST" });
}

export async function sendChatMessage(
  sessionId: number,
  content: string,
): Promise<{ user_message: ChatMessage; assistant_message: ChatMessage }> {
  return apiFetch(`/activities/chat/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function approveChatSession(
  sessionId: number,
  input: ActivityInput,
): Promise<Activity> {
  return apiFetch<Activity>(`/activities/chat/sessions/${sessionId}/approve`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}
