export type UserRole = "pending" | "viewer" | "net_control" | "admin" | "deleted";

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

export interface AuditEntry {
  id: number;
  actor_callsign: string;
  action: string;
  target_callsign: string | null;
  details: Record<string, string> | null;
  created_at: string;
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

export interface CheckIn {
  id: number;
  session_id: number;
  raw_message_id: number | null;
  callsign: string;
  name: string;
  city: string | null;
  county: string | null;
  state: string | null;
  mode: string;
  comments: string | null;
  latitude: number | null;
  longitude: number | null;
  parse_status: "auto" | "manual_review" | "manually_entered";
  timing_status: "on_time" | "early" | "late";
  is_new_member: boolean;
}

export interface CallbookResult {
  callsign: string;
  name: string | null;
  city: string | null;
  county: string | null;
  state: string | null;
  country: string | null;
  latitude: number | null;
  longitude: number | null;
  source: string;
  cached: boolean;
}

export interface Member {
  callsign: string;
  name: string;
  first_check_in_date: string;
  last_check_in_date: string;
  total_check_ins: number;
}

export interface MemberCheckin extends CheckIn {
  session_date: string;
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

export type ReminderStatus = "draft" | "approved" | "sent" | "skipped";
export type ReminderTemplateType = "regular_checkin" | "activity";

export interface Reminder {
  id: number;
  session_id: number;
  template_id: number | null;
  status: ReminderStatus;
  content_subject: string;
  content_body: string;
  drafted_at: string;
  approved_at: string | null;
  sent_at: string | null;
  approved_by: string | null;
}

export interface ReminderTemplate {
  id: number;
  name: string;
  template_type: ReminderTemplateType;
  subject_template: string;
  body_template: string;
  lead_time_days: number;
  is_default: boolean;
}

export type RosterStatus = "draft" | "approved" | "sent" | "skipped";

export interface Roster {
  id: number;
  session_id: number;
  template_id: number | null;
  status: RosterStatus;
  content_subject: string;
  content_header: string;
  content_welcome: string;
  content_comments: string;
  content_footer: string;
  session_url: string | null;
  drafted_at: string;
  approved_at: string | null;
  sent_at: string | null;
  approved_by: string | null;
}

export interface RosterTemplate {
  id: number;
  name: string;
  subject_template: string;
  header_template: string;
  welcome_template: string;
  comments_template: string;
  footer_template: string;
  lead_time_days: number;
  is_default: boolean;
}
