import { apiFetch } from "./client";
import type {
  EventLogEntry,
  EventMessage,
  EventMessages,
  EventParticipant,
  EventPositions,
  EventPost,
  EventReportParticipant,
  EventSnapshot,
  EventType,
  EventUpdates,
  FormCatalogNode,
  FormPreview,
  MessageStatus,
  NetEvent,
  ParticipantStatus,
  PatConnectInput,
  PatConnectOptions,
  PatSession,
  ReplyForm,
  WeatherData,
} from "../types";

// --- Events ---

export interface EventCreateInput {
  name: string;
  event_type: EventType;
  description?: string | null;
  scheduled_start?: string | null;
}

export async function fetchEvents(): Promise<NetEvent[]> {
  return apiFetch<NetEvent[]>(`/events`);
}

export async function createEvent(input: EventCreateInput): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/events`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function fetchEvent(id: number, token?: string): Promise<EventSnapshot> {
  const t = token ? `?token=${encodeURIComponent(token)}` : "";
  return apiFetch<EventSnapshot>(`/events/${id}${t}`);
}

export async function updateEvent(
  id: number,
  body: Partial<
    Pick<
      NetEvent,
      | "name"
      | "description"
      | "scheduled_start"
      | "aprs_other_stations"
      | "aprs_range_lat"
      | "aprs_range_lon"
      | "aprs_range_km"
      | "aprs_beacon_posts"
    >
  >,
): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/events/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function activateEvent(id: number): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/events/${id}/activate`, { method: "POST" });
}

export async function closeEvent(id: number): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/events/${id}/close`, { method: "POST" });
}

export async function reopenEvent(id: number): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/events/${id}/reopen`, { method: "POST" });
}

// --- Posts ---

export interface PostInput {
  name?: string;
  description?: string | null;
  lat?: number | null;
  lon?: number | null;
}

export async function createEventPost(
  eventId: number,
  input: PostInput & { name: string },
): Promise<EventPost> {
  return apiFetch<EventPost>(`/events/${eventId}/posts`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateEventPost(
  eventId: number,
  postId: number,
  input: PostInput,
): Promise<EventPost> {
  return apiFetch<EventPost>(`/events/${eventId}/posts/${postId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteEventPost(eventId: number, postId: number): Promise<void> {
  return apiFetch<void>(`/events/${eventId}/posts/${postId}`, { method: "DELETE" });
}

// --- Participants ---

export interface CheckInInput {
  callsign: string;
  name?: string | null;
  post_id?: number | null;
  location?: string | null;
}

export async function checkInParticipant(
  eventId: number,
  input: CheckInInput,
): Promise<EventParticipant> {
  return apiFetch<EventParticipant>(`/events/${eventId}/participants`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export interface ParticipantUpdateInput {
  status?: ParticipantStatus;
  post_id?: number | null;
  location?: string | null;
  name?: string | null;
}

export async function updateParticipant(
  eventId: number,
  participantId: number,
  input: ParticipantUpdateInput,
): Promise<EventParticipant> {
  return apiFetch<EventParticipant>(
    `/events/${eventId}/participants/${participantId}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
}

// --- Log ---

export interface NoteInput {
  message: string;
  callsign?: string | null;
  pinned?: boolean;
}

export async function addEventNote(
  eventId: number,
  input: NoteInput,
): Promise<EventLogEntry> {
  return apiFetch<EventLogEntry>(`/events/${eventId}/log`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function setEventLogPinned(
  eventId: number,
  entryId: number,
  pinned: boolean,
): Promise<EventLogEntry> {
  return apiFetch<EventLogEntry>(`/events/${eventId}/log/${entryId}`, {
    method: "PATCH",
    body: JSON.stringify({ pinned }),
  });
}

// --- Updates + report ---

export async function fetchEventUpdates(
  eventId: number,
  since: number,
  token?: string,
): Promise<EventUpdates> {
  const t = token ? `&token=${encodeURIComponent(token)}` : "";
  return apiFetch<EventUpdates>(`/events/${eventId}/updates?since=${since}${t}`);
}

export async function fetchEventReport(
  eventId: number,
): Promise<{ participants: EventReportParticipant[] }> {
  return apiFetch<{ participants: EventReportParticipant[] }>(
    `/events/${eventId}/report`,
  );
}

// --- APRS positions ---

export async function fetchEventPositions(
  eventId: number,
  since: number,
  token?: string,
): Promise<EventPositions> {
  const t = token ? `&token=${encodeURIComponent(token)}` : "";
  return apiFetch<EventPositions>(`/events/${eventId}/positions?since=${since}${t}`);
}

// --- Event messages (Winlink) ---

export async function fetchEventMessages(
  eventId: number,
  since: number,
  includeDismissed = false,
): Promise<EventMessages> {
  const params = new URLSearchParams({ since: String(since) });
  if (includeDismissed) params.set("include_dismissed", "true");
  return apiFetch<EventMessages>(`/events/${eventId}/messages?${params}`);
}

export interface ComposeMessageInput {
  to_address: string;
  subject: string;
  body: string;
  reply_to_id?: number | null;
}

export async function composeEventMessage(
  eventId: number,
  input: ComposeMessageInput,
): Promise<{ message: EventMessage; delivered: boolean }> {
  return apiFetch<{ message: EventMessage; delivered: boolean }>(
    `/events/${eventId}/messages`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function setEventMessageStatus(
  eventId: number,
  messageId: number,
  status: MessageStatus,
): Promise<EventMessage> {
  return apiFetch<EventMessage>(`/events/${eventId}/messages/${messageId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export async function rescanEventMailbox(
  eventId: number,
): Promise<{ new_messages: number }> {
  return apiFetch<{ new_messages: number }>(`/events/${eventId}/rescan`, {
    method: "POST",
  });
}

export async function retryEventMessage(
  eventId: number,
  messageId: number,
): Promise<{ retried: boolean }> {
  return apiFetch<{ retried: boolean }>(`/events/${eventId}/messages/${messageId}/retry`, {
    method: "POST",
  });
}

export function eventAttachmentUrl(
  eventId: number,
  messageId: number,
  attachmentId: number,
): string {
  return `/api/events/${eventId}/messages/${messageId}/attachments/${attachmentId}`;
}

// --- Form composition ---

export async function fetchFormCatalog(eventId: number, q = ""): Promise<FormCatalogNode> {
  const p = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch<FormCatalogNode>(`/events/${eventId}/forms/catalog${p}`);
}

export function formRenderUrl(eventId: number, path: string, prefill?: Record<string, string>): string {
  let url = `/api/events/${eventId}/forms/render?path=${encodeURIComponent(path)}`;
  if (prefill && Object.keys(prefill).length > 0) {
    url += `&prefill=${encodeURIComponent(JSON.stringify(prefill))}`;
  }
  return url;
}

export interface FormComposeInput {
  template_path: string;
  variables: Record<string, string>;
  datetime_stamp: string;
  reply_to_id?: number | null;
}

export async function previewForm(eventId: number, input: FormComposeInput): Promise<FormPreview> {
  return apiFetch<FormPreview>(`/events/${eventId}/forms/preview`, {
    method: "POST", body: JSON.stringify(input),
  });
}

export async function sendFormMessage(
  eventId: number, input: FormComposeInput,
): Promise<{ message: EventMessage; delivered: boolean }> {
  return apiFetch<{ message: EventMessage; delivered: boolean }>(
    `/events/${eventId}/form-messages`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function fetchReplyForm(eventId: number, messageId: number): Promise<ReplyForm> {
  return apiFetch<ReplyForm>(`/events/${eventId}/messages/${messageId}/reply-form`);
}

// --- PAT transport (event-scoped) ---

export async function fetchPatConnectOptions(eventId: number): Promise<PatConnectOptions> {
  return apiFetch<PatConnectOptions>(`/events/${eventId}/pat/connect-options`);
}

export async function patConnect(eventId: number, input: PatConnectInput): Promise<{ session_id: number }> {
  return apiFetch<{ session_id: number }>(`/events/${eventId}/pat/connect`, { method: "POST", body: JSON.stringify(input) });
}

export async function fetchPatSession(eventId: number, sessionId: number): Promise<PatSession> {
  return apiFetch<PatSession>(`/events/${eventId}/pat/sessions/${sessionId}`);
}

export async function abortPatSession(eventId: number, sessionId: number): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/events/${eventId}/pat/sessions/${sessionId}/abort`, { method: "POST" });
}

export async function testPatConnection(eventId: number): Promise<{ ok: boolean; error?: string }> {
  return apiFetch<{ ok: boolean; error?: string }>(`/events/${eventId}/pat/test`, { method: "POST" });
}

// --- Weather ---

export async function fetchEventWeather(eventId: number, token?: string): Promise<WeatherData> {
  const t = token ? `?token=${encodeURIComponent(token)}` : "";
  return apiFetch<WeatherData>(`/events/${eventId}/weather${t}`);
}

// --- By token (public page) ---

export async function fetchEventByToken(token: string): Promise<EventSnapshot> {
  return apiFetch<EventSnapshot>(`/events/by-token/${encodeURIComponent(token)}`);
}
