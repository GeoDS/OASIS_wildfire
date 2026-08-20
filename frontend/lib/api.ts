/**
 * Backend client.
 *
 * SSE runs over `fetch` + ReadableStream rather than `EventSource`, because
 * EventSource can only issue GET and we need to POST a body. The parser is
 * right below and it is short.
 */

import type {
  ExpertiseLevel,
  FireLifecycle,
  Health,
  LocalCapability,
  LocalLayerResponse,
  LocalRasterCatalog,
  PublicLayerResponse,
  PublicSource,
  RasterLayerResult,
  Taxonomy,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export const resolveApiUrl = (url: string) =>
  url.startsWith("http") ? url : `${API_BASE}${url}`;

export interface SseEvent {
  event: string;
  data: unknown;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export const getHealth = () => getJson<Health>("/api/health");
export const getTaxonomy = () => getJson<Taxonomy>("/api/taxonomy");

export interface PublicLayerRequest {
  source: PublicSource;
  day?: string;
  latitude?: number;
  longitude?: number;
  start_year?: number;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${path} → HTTP ${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const fetchPublicLayer = (request: PublicLayerRequest) =>
  postJson<PublicLayerResponse>("/api/layers/public", request);

export const fetchLocalLayer = (
  capabilityId: LocalCapability,
  bbox: [number, number, number, number],
) =>
  postJson<LocalLayerResponse>("/api/layers/local", {
    capability_id: capabilityId,
    bbox,
  });

export const getLocalRasterCatalog = () =>
  getJson<LocalRasterCatalog>("/api/local-data/raster-catalog");

export async function fetchLocalRasterLayer(
  datasetId: string,
  bbox: [number, number, number, number],
  day?: string,
): Promise<RasterLayerResult> {
  const result = await postJson<RasterLayerResult>("/api/layers/local-raster", {
    dataset_id: datasetId,
    bbox,
    day,
  });
  return {
    ...result,
    image_url: resolveApiUrl(result.image_url),
  };
}

export async function fetchFireLifecycle(
  eventId: string,
  day?: string,
): Promise<FireLifecycle> {
  const result = await postJson<FireLifecycle>("/api/layers/fire-lifecycle", {
    event_id: eventId,
    day,
  });
  return {
    ...result,
    layers: result.layers.map((layer) => ({
      ...layer,
      image_url: resolveApiUrl(layer.image_url),
    })),
  };
}

export async function createSession(): Promise<string> {
  const res = await fetch(`${API_BASE}/api/sessions`, { method: "POST" });
  if (!res.ok) throw new Error(`Could not create a session: HTTP ${res.status}`);
  const body = (await res.json()) as { session_id: string };
  return body.session_id;
}

/**
 * Send a message and yield SSE events one at a time.
 *
 * The same endpoint serves both the opening question and a clarification
 * answer; the backend decides which by checking whether the session is parked
 * on an interrupt, so the client never has to care.
 */
export async function* sendMessage(
  sessionId: string,
  text: string,
  expertiseOverride?: ExpertiseLevel | null,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, expertise_override: expertiseOverride ?? null }),
    signal,
  });

  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${detail}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Event blocks are separated by a blank line, and a blank line has three
    // spellings (CRLF / LF / CR).
    // sse-starlette emits CRLF: looking only for "\n\n" receives nothing at all,
    // because the stream actually contains "\r\n\r\n". Do not drop this.
    let match: RegExpMatchArray | null;
    while ((match = buffer.match(/\r\n\r\n|\n\n|\r\r/)) !== null) {
      const idx = match.index!;
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + match[0].length);

      const parsed = parseEventBlock(block);
      if (parsed) yield parsed;
    }
  }

  // The stream can end with a final block that has no trailing blank line
  const tail = parseEventBlock(buffer);
  if (tail) yield tail;
}

function parseEventBlock(block: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of block.split(/\r\n|\n|\r/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;

  const raw = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: raw };
  }
}
