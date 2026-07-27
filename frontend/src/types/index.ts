/** Per-net role. Replaces the old monolithic UserRole for per-net access gating. */
export type NetRole = "viewer" | "net_control";

export interface NetSummary {
  slug: string;
  name: string;
  is_public: boolean;
}

export interface NetMembershipSummary extends NetSummary {
  role: NetRole;
}

export interface User {
  callsign: string;
  name: string;
  is_admin: boolean;
  is_pending: boolean;
  email: string | null;
  pending_callsign: string | null;
  nets: NetMembershipSummary[];
}

export interface Provider {
  name: string;
  label: string;
}

export type SessionType = "regular_checkin" | "activity" | "real_event";
export type SessionStatus = "scheduled" | "completed" | "cancelled";

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
  roster_status: RosterStatus | null;
}

export interface Season {
  id: number;
  name: string;
  start_date: string;
  end_date: string;
  day_of_week: number | null;
  time: string | null;
  is_week_long: boolean;
  activity_cadence: number;
  sessions: Session[];
}

export interface SeasonCreate {
  name: string;
  start_date: string;
  end_date: string;
  day_of_week: number | null;
  time: string | null;
  is_week_long: boolean;
  activity_cadence: number;
  default_net_control_callsign: string | null;
}

export interface SessionCreate {
  start_date: string;
  end_date?: string | null;
  session_type: SessionType;
  season_id?: number | null;
  grace_period_hours?: number;
  net_control_callsign?: string | null;
  activity_id?: number | null;
}

export interface SessionUpdate {
  status?: SessionStatus;
  session_type?: SessionType;
  net_control_callsign?: string | null;
  activity_id?: number | null;
  grace_period_hours?: number;
  end_date?: string | null;
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
  raw_message: {
    subject: string;
    from_address: string;
    received_at: string;
    body: string;
    message_type: "form" | "plain_text" | "unknown" | "winlink_form";
  } | null;
  form_view_html: string | null;
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

/** Minimum access level required for a PAT scope.
 *  "net_control" and "viewer" map to NetRole; "admin" is the user-global flag. */
export type ScopeMinRole = NetRole | "admin";

export const SCOPES: Record<string, { description: string; minRole: ScopeMinRole }> = {
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
  net_id: number;
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
  net_id: number;
  name: string;
  subject_template: string;
  header_template: string;
  welcome_template: string;
  comments_template: string;
  footer_template: string;
  lead_time_days: number;
  is_default: boolean;
}

export type NotificationKind =
  | "reminder_draft"
  | "checkins_ready"
  | "roster_draft"
  | "delivery_failure";

export interface Notification {
  id: number;
  kind: NotificationKind;
  session_id: number | null;
  message: string;
  link_url: string | null;
  created_at: string;
  read_at: string | null;
}

export interface ActivityTag {
  id: number;
  name: string;
}

export interface Activity {
  id: number;
  net_id: number;
  title: string;
  description: string;
  instructions: string;
  is_default: boolean;
  created_at: string;
  last_used_at: string | null;
  tags: ActivityTag[];
}

export type ChatMessageRole = "user" | "assistant";

export interface ChatMessage {
  id: number;
  role: ChatMessageRole;
  content: string;
  created_at: string;
}

export interface ChatSession {
  id: number;
  activity_id: number | null;
  created_at: string;
  messages: ChatMessage[];
}

// --- Live events ---

export type EventType = "public_service" | "emergency";
export type EventStatus = "draft" | "active" | "closed";
export type ParticipantStatus =
  | "checked_in"
  | "at_post"
  | "en_route"
  | "out_of_service"
  | "checked_out";
export type EventLogType = "system" | "note" | "participant_note";

/** Named NetEvent to avoid clashing with the DOM Event type. */
export interface NetEvent {
  id: number;
  net_id: number;
  name: string;
  description: string | null;
  event_type: EventType;
  status: EventStatus;
  scheduled_start: string | null;
  activated_at: string | null;
  closed_at: string | null;
  created_by: string;
  created_at: string;
  aprs_other_stations: boolean;
  aprs_range_lat: number | null;
  aprs_range_lon: number | null;
  aprs_range_km: number | null;
  aprs_beacon_posts: boolean;
  weather_enabled: boolean;
  /** Present when the user is a control operator (owner or co-operator). */
  owner?: string;
  /** Callsigns of co-operators; present when is_control. */
  operators?: string[];
  /** Public share token; present when is_control. */
  public_token?: string;
  /** always present. */
  visibility?: "private" | "public";
}

export interface EventPost {
  id: number;
  event_id: number;
  name: string;
  description: string | null;
  lat: number | null;
  lon: number | null;
}

export interface EventParticipant {
  id: number;
  event_id: number;
  callsign: string;
  name: string | null;
  post_id: number | null;
  location: string | null;
  current_status: ParticipantStatus;
  checked_in_at: string;
  checked_out_at: string | null;
}

export interface EventLogEntry {
  id: number;
  seq: number;
  entry_type: EventLogType;
  callsign: string | null;
  actor: string;
  message: string;
  new_status: ParticipantStatus | null;
  pinned: boolean;
  created_at: string;
}

export interface EventSnapshot {
  event: NetEvent;
  posts: EventPost[];
  participants: EventParticipant[];
  log: EventLogEntry[];
  pinned_seqs: number[];
  is_control: boolean;
}

export interface EventUpdates extends EventSnapshot {
  latest_seq: number;
}

export interface EventStint {
  start: string;
  end: string | null;
}

export interface EventReportParticipant {
  callsign: string;
  name: string | null;
  post: string | null;
  location: string | null;
  stints: EventStint[];
  total_seconds: number;
}

// --- APRS live positions ---

export type StationKind = "participant" | "other";

export interface EventTrackPoint {
  lat: number;
  lon: number;
  ts: string;
  pos_seq: number;
}

export interface EventStation {
  station_id: string;
  kind: StationKind;
  callsign: string | null;
  symbol: string | null;
  comment: string | null;
  last_heard: string;
  points: EventTrackPoint[];
}

export interface BeaconedObject {
  post_id: number;
  name: string;
}

export interface EventPositions {
  stations: EventStation[];
  latest_pos_seq: number;
  aprs_status: string;
  aprs_status_detail: string;
  objects: BeaconedObject[];
}

// --- Event Winlink messages ---

export type MessageDirection = "inbound" | "outbound";
export type MessageStatus = "unread" | "read" | "dismissed";

export interface EventMessageAttachment {
  id: number;
  filename: string;
  content_type: string;
  size: number;
}

export interface EventReceivedForm {
  is_form: boolean;
  display_form: string;
  reply_template: string;
}

export interface EventMessage {
  id: number;
  msg_seq: number;
  direction: MessageDirection;
  raw_message_id: number | null;
  participant_id: number | null;
  from_callsign: string;
  to_address: string;
  subject: string;
  body: string;
  status: MessageStatus;
  reply_to_id: number | null;
  actor: string | null;
  received_at: string;
  created_at: string;
  attachments: EventMessageAttachment[];
  form: EventReceivedForm | null;
  delivery_status?: string;
}

export interface EventMessages {
  messages: EventMessage[];
  latest_msg_seq: number;
  messaging_configured: boolean;
}

export interface FormCatalogEntry { name: string; template_path: string; input_form_path: string; }
export interface FormCatalogNode { name: string; folders: FormCatalogNode[]; forms: FormCatalogEntry[]; }
export interface FormPreview { to: string; subject: string; body: string; attachment_filename: string; }
export interface ReplyForm { reply_template_path: string; input_form_path: string | null; prefill: Record<string, string>; }

export interface PatSessionEvent { ts: string; kind: string; text: string; }
export interface PatSession {
  id: number;
  status: "connecting" | "connected" | "syncing" | "completed" | "failed" | "aborted";
  method_label: string;
  sent_count: number;
  received_count: number;
  error: string | null;
  events: PatSessionEvent[];
  started_at: string | null;
  ended_at: string | null;
}
export interface PatConnectOptions {
  aliases: { name: string; url: string }[];
  gateways: { callsign: string; modes: string; freq: string }[];
}
export interface PatConnectInput {
  alias?: string;
  mode?: string;
  gateway?: string;
  freq?: string;
}

export type WeatherStatus = "ok" | "stale" | "unavailable" | "disabled" | "no_area";

export interface WeatherFeature {
  id?: string;
  type: "Feature";
  geometry: unknown;                 // GeoJSON geometry, passed straight to L.geoJSON
  properties: {
    event?: string;                  // e.g. "Tornado Warning"
    headline?: string;
    severity?: string;               // Extreme | Severe | Moderate | Minor | Unknown
    expires?: string;
    [k: string]: unknown;
  };
}

export interface WeatherAlerts {
  type: "FeatureCollection";
  features: WeatherFeature[];
}

export interface WeatherData {
  alerts: WeatherAlerts;
  updated_at: string | null;
  status: WeatherStatus;
}
