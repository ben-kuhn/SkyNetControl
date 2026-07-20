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
  ReplyForm,
} from "../types";

// --- Events ---

export interface EventCreateInput {
  name: string;
  event_type: EventType;
  description?: string | null;
  scheduled_start?: string | null;
  activate?: boolean;
}

export async function fetchEvents(netSlug: string): Promise<NetEvent[]> {
  return apiFetch<NetEvent[]>(`/nets/${netSlug}/events`);
}

export async function createEvent(input: EventCreateInput, netSlug: string): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function fetchEvent(id: number, netSlug: string): Promise<EventSnapshot> {
  return apiFetch<EventSnapshot>(`/nets/${netSlug}/events/${id}`);
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
  netSlug: string,
): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function activateEvent(id: number, netSlug: string): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events/${id}/activate`, { method: "POST" });
}

export async function closeEvent(id: number, netSlug: string): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events/${id}/close`, { method: "POST" });
}

export async function reopenEvent(id: number, netSlug: string): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events/${id}/reopen`, { method: "POST" });
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
  netSlug: string,
): Promise<EventPost> {
  return apiFetch<EventPost>(`/nets/${netSlug}/events/${eventId}/posts`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateEventPost(
  eventId: number,
  postId: number,
  input: PostInput,
  netSlug: string,
): Promise<EventPost> {
  return apiFetch<EventPost>(`/nets/${netSlug}/events/${eventId}/posts/${postId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteEventPost(eventId: number, postId: number, netSlug: string): Promise<void> {
  return apiFetch<void>(`/nets/${netSlug}/events/${eventId}/posts/${postId}`, { method: "DELETE" });
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
  netSlug: string,
): Promise<EventParticipant> {
  return apiFetch<EventParticipant>(`/nets/${netSlug}/events/${eventId}/participants`, {
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
  netSlug: string,
): Promise<EventParticipant> {
  return apiFetch<EventParticipant>(
    `/nets/${netSlug}/events/${eventId}/participants/${participantId}`,
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
  netSlug: string,
): Promise<EventLogEntry> {
  return apiFetch<EventLogEntry>(`/nets/${netSlug}/events/${eventId}/log`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function setEventLogPinned(
  eventId: number,
  entryId: number,
  pinned: boolean,
  netSlug: string,
): Promise<EventLogEntry> {
  return apiFetch<EventLogEntry>(`/nets/${netSlug}/events/${eventId}/log/${entryId}`, {
    method: "PATCH",
    body: JSON.stringify({ pinned }),
  });
}

// --- Updates + report ---

export async function fetchEventUpdates(
  eventId: number,
  since: number,
  netSlug: string,
): Promise<EventUpdates> {
  return apiFetch<EventUpdates>(`/nets/${netSlug}/events/${eventId}/updates?since=${since}`);
}

export async function fetchEventReport(
  eventId: number,
  netSlug: string,
): Promise<{ participants: EventReportParticipant[] }> {
  return apiFetch<{ participants: EventReportParticipant[] }>(
    `/nets/${netSlug}/events/${eventId}/report`,
  );
}

// --- APRS positions ---

export async function fetchEventPositions(
  eventId: number,
  since: number,
  netSlug: string,
): Promise<EventPositions> {
  return apiFetch<EventPositions>(`/nets/${netSlug}/events/${eventId}/positions?since=${since}`);
}

// --- Event messages (Winlink) ---

export async function fetchEventMessages(
  eventId: number,
  since: number,
  netSlug: string,
  includeDismissed = false,
): Promise<EventMessages> {
  const params = new URLSearchParams({ since: String(since) });
  if (includeDismissed) params.set("include_dismissed", "true");
  return apiFetch<EventMessages>(`/nets/${netSlug}/events/${eventId}/messages?${params}`);
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
  netSlug: string,
): Promise<{ message: EventMessage; delivered: boolean }> {
  return apiFetch<{ message: EventMessage; delivered: boolean }>(
    `/nets/${netSlug}/events/${eventId}/messages`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function setEventMessageStatus(
  eventId: number,
  messageId: number,
  status: MessageStatus,
  netSlug: string,
): Promise<EventMessage> {
  return apiFetch<EventMessage>(`/nets/${netSlug}/events/${eventId}/messages/${messageId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export async function rescanEventMailbox(
  eventId: number,
  netSlug: string,
): Promise<{ new_messages: number }> {
  return apiFetch<{ new_messages: number }>(`/nets/${netSlug}/events/${eventId}/rescan`, {
    method: "POST",
  });
}

export function eventAttachmentUrl(
  eventId: number,
  messageId: number,
  attachmentId: number,
  netSlug: string,
): string {
  return `/api/nets/${netSlug}/events/${eventId}/messages/${messageId}/attachments/${attachmentId}`;
}

// --- Form composition ---

export async function fetchFormCatalog(netSlug: string, q = ""): Promise<FormCatalogNode> {
  const p = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch<FormCatalogNode>(`/nets/${netSlug}/forms/catalog${p}`);
}

export function formRenderUrl(netSlug: string, path: string, prefill?: Record<string, string>): string {
  let url = `/api/nets/${netSlug}/forms/render?path=${encodeURIComponent(path)}`;
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

export async function previewForm(eventId: number, input: FormComposeInput, netSlug: string): Promise<FormPreview> {
  return apiFetch<FormPreview>(`/nets/${netSlug}/events/${eventId}/forms/preview`, {
    method: "POST", body: JSON.stringify(input),
  });
}

export async function sendFormMessage(
  eventId: number, input: FormComposeInput, netSlug: string,
): Promise<{ message: EventMessage; delivered: boolean }> {
  return apiFetch<{ message: EventMessage; delivered: boolean }>(
    `/nets/${netSlug}/events/${eventId}/form-messages`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function fetchReplyForm(eventId: number, messageId: number, netSlug: string): Promise<ReplyForm> {
  return apiFetch<ReplyForm>(`/nets/${netSlug}/events/${eventId}/messages/${messageId}/reply-form`);
}
