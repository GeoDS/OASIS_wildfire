/**
 * Mirror of the backend Analysis Contract.
 *
 * The authoritative definition lives in `backend/src/wildfire_agent/contract.py`
 * and can be generated from `GET /api/schema/contract`. This hand-written copy
 * is a development convenience: **the backend owns the contract**, so never
 * bend the backend to suit these types.
 */

export type Provenance = "user_stated" | "agent_inferred" | "default";

export type ExpertiseLevel = "general" | "practitioner" | "expert";

export type TaskIntent =
  | "observation"
  | "assessment"
  | "prediction"
  | "decision_support"
  | "evaluation_adaptation";

export interface ResolvedLocation {
  display_name: string | null;
  /** [lon, lat] in WGS84 - same order as GeoJSON */
  center: [number, number] | null;
  buffer_km: number | null;
  /** [west, south, east, north] */
  bbox: [number, number, number, number] | null;
  geocoder: string | null;
  confirmed_by_user: boolean;
  /** Same-name candidates. There are a dozen places called Madison in the US. */
  alternatives: string[];
  /** Candidates far apart and equally prominent. Never interrupts; only emphasised in the UI. */
  ambiguous: boolean;
}

export interface Slot {
  kind: "scalar" | "spatial";
  value: string | null;
  confidence: number;
  source: Provenance;
  is_blocking: boolean;
  blocking_reason: string | null;
  /** Only when kind === "spatial" */
  raw?: string | null;
  resolved?: ResolvedLocation | null;
}

export interface AnalysisContract {
  schema_version: string;
  created_at: string;
  original_request: string;
  restatement: string | null;
  user_role: string;
  expertise: ExpertiseLevel;
  task_intent: TaskIntent[];
  hazard_objects: string[];
  slots: Record<string, Slot>;
  assumptions: string[];
  unresolved: string[];
  clarification_rounds: number;
  ready_for_planning: boolean;
  /** Added by the API, not part of the contract itself */
  pending_slots: string[];
  filled_ratio: number;
}

export interface ClarificationOption {
  label: string;
  implication: string | null;
  recommended: boolean;
}

export interface ClarificationQuestion {
  slot: string;
  question: string;
  options: ClarificationOption[];
  allow_free_text: boolean;
}

export interface ClarificationPayload {
  type: "clarification";
  preamble: string;
  questions: ClarificationQuestion[];
  pending_slots: string[];
}

export interface PlannedLayer {
  capability_id: string;
  title: string;
  hazard_object: string;
  family: string | null;
  geometry_type: "Point" | "Polygon" | "LineString";
  caveat: string;
  reason: string;
}

export interface UnmetNeed {
  hazard_object: string;
  reason: string;
}

export interface ExecutionPlan {
  layers: PlannedLayer[];
  unmet: UnmetNeed[];
  notes: string[];
}

/** A fetched layer, ready to draw. `caveat` and `source` are not optional
 *  decoration: a satellite heat pixel shown without them reads as "a fire". */
export interface LayerResult {
  capability_id: string;
  title: string;
  hazard_object: string;
  family: string | null;
  geometry_type: "Point" | "Polygon" | "LineString";
  caveat: string;
  feature_count: number;
  truncated: boolean;
  source: string;
  as_of: string | null;
  retrieved_at: string | null;
  geojson: { type: "FeatureCollection"; features: unknown[] };
}

export interface ShowcaseArea {
  id: string;
  name: string;
  center: [number, number];
  bbox: [number, number, number, number];
  context: string;
}

export interface HazardObjectInfo {
  layer: "hazard" | "exposure" | "action";
  label: string;
  required_variables: string[];
  dataset_families: string[];
  /** Capability ids in this deployment that can serve the object; empty = no source */
  covered_by: string[];
}

export interface Taxonomy {
  intents: Record<string, string>;
  expertise: Record<ExpertiseLevel, string>;
  roles: Record<string, string>;
  slots: Record<string, string>;
  intent_slot_matrix: Record<string, Record<string, "B" | "D" | "O">>;
  hazard_objects: Record<string, HazardObjectInfo>;
  stages: Record<string, string>;
  stage_agents: Record<string, string>;
  showcase_area: ShowcaseArea;
  capabilities: Record<string, { title: string; caveat: string; family: string | null }>;
}

export interface Health {
  ok: boolean;
  llm: string;
  mock: boolean;
  detail: string;
}

/** Every stage, in pipeline order. The first four belong to the User Goal
 *  Agent, the last two to the Planning Agent downstream of the contract. */
export const STAGES = [
  "requirement_understanding",
  "task_compiler",
  "ambiguity_resolution",
  "analysis_contract",
  "planning",
  "execution",
] as const;

/** Index at which the handoff to the Planning Agent happens. */
export const HANDOFF_INDEX = 4;

export type StageId = (typeof STAGES)[number];
export type StageStatus = "pending" | "active" | "done";

export interface ChatMessage {
  role: "user" | "agent";
  content: string;
  /** Clarification payload, so agent turns can render their options as buttons */
  clarification?: ClarificationPayload;
}
