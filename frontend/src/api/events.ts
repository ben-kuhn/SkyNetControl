import { apiFetch } from "./client";
import type {
  EventLogEntry,
  EventParticipant,
  EventPost,
  EventReportParticipant,
  EventSnapshot,
  EventType,
  EventUpdates,
  NetEvent,
  ParticipantStatus,
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
  body: Partial<Pick<NetEvent, "name" | "description" | "scheduled_start">>,
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
