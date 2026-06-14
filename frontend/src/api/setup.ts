import { apiFetch } from "./client";
import type { SmtpUpsert } from "./smtp";

export interface SetupStatus {
  setup_completed: boolean;
}

export interface SetupClaimStart {
  default_net_control: string;
  net_address: string;
  app_base_url: string;
  oauth_slug: string;
  oauth_name: string;
  oauth_client_id: string;
  oauth_client_secret: string;
  oauth_issuer_url: string;
  smtp: SmtpUpsert | null;
}

export interface SetupClaimStartResponse {
  authorize_url: string;
}

export function getSetupStatus(): Promise<SetupStatus> {
  return apiFetch<SetupStatus>("/setup/status");
}

export function startSetupClaim(body: SetupClaimStart): Promise<SetupClaimStartResponse> {
  return apiFetch<SetupClaimStartResponse>("/setup/claim/start", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
