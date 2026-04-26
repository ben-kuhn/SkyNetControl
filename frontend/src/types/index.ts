export type UserRole = "pending" | "viewer" | "net_control" | "admin";

export interface User {
  callsign: string;
  name: string;
  role: UserRole;
  email: string | null;
  pending_callsign: string | null;
}

export interface Provider {
  name: string;
  label: string;
}

export interface Session {
  id: number;
  season_id: number | null;
  start_date: string;
  end_date: string | null;
  grace_period_hours: number;
  session_type: string;
  status: string;
  activity_id: number | null;
  net_control_callsign: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

export interface Token {
  id: number;
  name: string;
  token_prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  is_expired: boolean;
  is_revoked: boolean;
}

export interface TokenCreate {
  name: string;
  scopes: string[];
  expires_at?: string;
}

export interface TokenWithSecret extends Token {
  token: string;
}

export const SCOPES: Record<string, { description: string; minRole: UserRole }> = {
  "schedule:read":  { description: "View sessions",             minRole: "viewer" },
  "schedule:write": { description: "Create/edit/delete sessions", minRole: "net_control" },
  "checkins:read":  { description: "View check-in data",        minRole: "viewer" },
  "checkins:write": { description: "Submit/manage check-ins",   minRole: "net_control" },
  "roster:read":    { description: "View roster data",          minRole: "net_control" },
  "map:read":       { description: "View map/GeoJSON data",     minRole: "viewer" },
  "users:read":     { description: "List users",                minRole: "admin" },
  "users:write":    { description: "Manage users/roles",        minRole: "admin" },
  "config:read":    { description: "View app configuration",    minRole: "admin" },
  "config:write":   { description: "Modify app configuration",  minRole: "admin" },
};
