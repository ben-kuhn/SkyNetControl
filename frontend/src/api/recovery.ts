import { apiFetch } from "./client";

export interface RecoveryStatus {
  outstanding: boolean;
}

export function getRecoveryStatus(): Promise<RecoveryStatus> {
  return apiFetch<RecoveryStatus>("/recovery/status");
}

export function claimRecoveryToken(token: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>("/recovery/claim", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

// Asks the server to expire the recovery cookie. The cookie is HttpOnly so
// JavaScript can't clear it directly — the server has to send a Set-Cookie
// header with max-age=0.
export function clearRecoveryCookie(): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>("/recovery/logout", { method: "POST" });
}
