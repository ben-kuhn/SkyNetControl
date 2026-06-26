import type { User } from "../types";
import { apiFetch } from "./client";

export async function fetchUsers(): Promise<User[]> {
  return apiFetch<User[]>("/auth/users");
}

/** Update a user's admin/pending/deleted status flags. */
export async function updateUserStatus(
  callsign: string,
  patch: { is_admin?: boolean; is_pending?: boolean; is_deleted?: boolean },
): Promise<void> {
  await apiFetch<unknown>(`/auth/users/${encodeURIComponent(callsign)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** @deprecated Use updateUserStatus instead. Left for TS compatibility during migration. */
export async function updateUserRole(
  callsign: string,
  role: string,
): Promise<void> {
  // Map old role strings to the new flag-based API
  const patch: { is_admin?: boolean; is_pending?: boolean } = {};
  if (role === "admin") {
    patch.is_admin = true;
    patch.is_pending = false;
  } else if (role === "pending") {
    patch.is_admin = false;
    patch.is_pending = true;
  } else {
    // viewer, net_control — clear admin/pending flags; net membership managed separately
    patch.is_admin = false;
    patch.is_pending = false;
  }
  await apiFetch<unknown>(`/auth/users/${encodeURIComponent(callsign)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function approveCallsign(callsign: string): Promise<void> {
  await apiFetch<unknown>(
    `/auth/users/${encodeURIComponent(callsign)}/approve-callsign`,
    { method: "POST" },
  );
}

export async function rejectCallsign(callsign: string): Promise<void> {
  await apiFetch<unknown>(
    `/auth/users/${encodeURIComponent(callsign)}/pending-callsign`,
    { method: "DELETE" },
  );
}
