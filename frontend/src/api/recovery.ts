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

// Clears the recovery cookie by setting it to expire immediately. The browser
// won't send it on the next request.
export function clearRecoveryCookie(): void {
  document.cookie = "recovery_token=; path=/; max-age=0; samesite=lax";
}
