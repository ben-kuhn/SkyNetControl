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
