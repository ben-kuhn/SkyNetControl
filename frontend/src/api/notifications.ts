// TODO(Task 13): replace DEFAULT_NET_SLUG with useCurrentNet() hook once
// the CurrentNetContext is available. Task 14 will wire slug into the API calls.
const DEFAULT_NET_SLUG = "default-net";

import { apiFetch } from "./client";
import type { Notification } from "../types";

export async function fetchNotifications(
  includeRead = false,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<Notification[]> {
  const qs = includeRead ? "?all=1" : "";
  return apiFetch<Notification[]>(`/nets/${netSlug}/notifications/${qs}`);
}

export async function markNotificationRead(
  id: number,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<Notification> {
  return apiFetch<Notification>(`/nets/${netSlug}/notifications/${id}/read`, { method: "POST" });
}

export async function markAllNotificationsRead(
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<{ count: number }> {
  return apiFetch<{ count: number }>(`/nets/${netSlug}/notifications/read-all`, { method: "POST" });
}
