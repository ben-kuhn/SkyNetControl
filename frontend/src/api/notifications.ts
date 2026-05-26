import { apiFetch } from "./client";
import type { Notification } from "../types";

export async function fetchNotifications(includeRead = false): Promise<Notification[]> {
  const qs = includeRead ? "?all=1" : "";
  return apiFetch<Notification[]>(`/notifications/${qs}`);
}

export async function markNotificationRead(id: number): Promise<Notification> {
  return apiFetch<Notification>(`/notifications/${id}/read`, { method: "POST" });
}

export async function markAllNotificationsRead(): Promise<{ count: number }> {
  return apiFetch<{ count: number }>("/notifications/read-all", { method: "POST" });
}
