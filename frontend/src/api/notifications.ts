import { apiFetch } from "./client";
import type { Notification } from "../types";

export async function fetchNotifications(
  includeRead = false,
  netSlug: string,
): Promise<Notification[]> {
  const qs = includeRead ? "?all=1" : "";
  return apiFetch<Notification[]>(`/nets/${netSlug}/notifications/${qs}`);
}

export async function markNotificationRead(
  id: number,
  netSlug: string,
): Promise<Notification> {
  return apiFetch<Notification>(`/nets/${netSlug}/notifications/${id}/read`, { method: "POST" });
}

export async function markAllNotificationsRead(
  netSlug: string,
): Promise<{ count: number }> {
  return apiFetch<{ count: number }>(`/nets/${netSlug}/notifications/read-all`, { method: "POST" });
}
