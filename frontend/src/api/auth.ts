import type { Provider, User } from "../types";
import { apiFetch } from "./client";

export async function fetchMe(): Promise<User | null> {
  try {
    return await apiFetch<User>("/auth/me");
  } catch {
    return null;
  }
}

export async function fetchProviders(): Promise<Provider[]> {
  return apiFetch<Provider[]>("/auth/providers");
}

export async function register(callsign: string): Promise<User> {
  return apiFetch<User>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ callsign }),
  });
}

export async function updateCallsign(callsign: string): Promise<User> {
  return apiFetch<User>("/auth/me", {
    method: "PATCH",
    body: JSON.stringify({ callsign }),
  });
}

export async function logout(): Promise<void> {
  await apiFetch<void>("/auth/logout", { method: "POST" });
}
