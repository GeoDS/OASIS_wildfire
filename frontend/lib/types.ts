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
  resolved_request: string | null;
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

export interface LegendStop {
  value: string | number;
  label: string;
  color: string;
}

export interface PopupField {
  key: string;
  label: string;
  unit: string | null;
}

export interface LayerVisualization {
  kind: "fixed" | "categorical" | "graduated" | "vector";
  label: string;
  field: string | null;
  unit: string | null;
  stops: LegendStop[];
  color: string | null;
  size_field: string | null;
  popup_fields: PopupField[];
  symbol: string | null;
  explanation: string | null;
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
  visualization: LayerVisualization | null;
  geojson: { type: "FeatureCollection"; features: unknown[] };
}

export type PublicSource = "weather" | "air_quality" | "wfigs" | "hmsfire" | "fire_history";

export type LocalCapability =
  | "official_fire_perimeters"
  | "satellite_hotspots"
  | "historical_fire_perimeters";

export interface PublicLayerResponse {
  metadata: {
    status: "loaded" | "empty" | "error";
    featureCount: number;
    notice: string | null;
    retrievedAt: string;
    scope: string;
  };
  layers: LayerResult[];
}

export interface LocalLayerResponse {
  bbox: [number, number, number, number];
  layer: LayerResult;
}

export interface LocalRasterDataset {
  dataset_id: string;
  event_id: string | null;
  event_name: string | null;
  spatial_scope: string | null;
  variable: string;
  time_start: string | null;
  time_end: string | null;
  dates: string[];
}

export interface LocalRasterCatalog {
  datasets: LocalRasterDataset[];
  mask: {
    dataset_id: string;
    label: string;
    available: boolean;
    crs: string;
  };
}

export interface RasterLayerResult {
  id: string;
  title: string;
  dataset_id: string;
  event_name: string | null;
  variable: string;
  date: string;
  display_band: number;
  bounds: [number, number, number, number];
  image_url: string;
  opacity: number;
  value_range: [number, number];
  mask: string;
  source: string;
  variable_label?: string;
  explanation?: string;
  source_label?: string;
  legend_label?: string;
  legend_color?: string;
  legend_stops?: LegendStop[];
  analysis_view?: "before" | "after" | "difference";
}

export interface RasterAnalysisInput {
  dataset: string;
  variable: string;
  band: number;
  band_description: string;
  date: string;
  temporal_meaning: string;
  scale_factor: number;
}

export interface RasterAnalysisOperation {
  type: "select" | "align_grids" | "difference" | "mask" | "zonal_statistics";
  parameters: Record<string, unknown>;
}

export interface RasterAnalysisStatistics {
  valid_pixels: number;
  valid_area_km2: number;
  mean_before: number;
  mean_after: number;
  mean_delta: number;
  median_delta: number;
  p10_delta: number;
  p90_delta: number;
  negative_percent: number;
  little_change_percent: number;
  positive_percent: number;
  damaged_percent?: number;
}

export interface AnalysisClassShare {
  label: string;
  color: string;
  pixels: number;
  area_km2: number;
  percent: number;
}

export interface SpatialAnalysis {
  analysis_id: string;
  event_id: string;
  event_name: string;
  operation: "ndvi_change" | "nbr_change";
  index?: string;
  index_source?: string;
  class_breakdown?: AnalysisClassShare[];
  title: string;
  formula: string;
  inputs: RasterAnalysisInput[];
  operations: RasterAnalysisOperation[];
  mask: "cumulative_burned_area";
  bounds: [number, number, number, number];
  layers: RasterLayerResult[];
  statistics: RasterAnalysisStatistics;
  summary: string;
  default_view: "before" | "after" | "difference";
  caveats: string[];
}

/** A measurement sampled over the footprint, with the unit it was recorded in. */
export interface ContextSample {
  label: string;
  unit: string;
  mean: number;
  min?: number;
  max?: number;
}

export interface LandCoverShare extends AnalysisClassShare {
  code: number;
}

export interface FireContextDay {
  date: string;
  new_area_km2: number;
  cumulative_area_km2: number;
  centroid_shift_km?: number;
  spread_bearing_deg?: number;
  spread_compass?: string;
  downwind_bearing_deg?: number;
  spread_wind_offset_deg?: number;
  conditions?: Record<string, ContextSample>;
  conditions_zone?: string;
}

export interface FireContext {
  analysis_id: string;
  event_id: string;
  event_name: string;
  analysis: "land_cover_composition" | "spread_behaviour" | "fire_weather";
  title: string;
  end_date: string;
  footprint_area_km2: number | null;
  land_cover_source: string | null;
  composition: LandCoverShare[];
  terrain: Record<string, ContextSample>;
  elevation_trend: {
    early_mean_m: number;
    late_mean_m: number;
    change_m: number;
    direction: string;
    split: string;
  } | null;
  wind_alignment: {
    days_compared: number;
    area_weighted_offset_deg: number;
    days_within_45_deg: number;
    area_share_within_45_deg: number;
  } | null;
  peak_growth_day: FireContextDay | null;
  timeline: FireContextDay[];
  summary: string;
  caveats: string[];
}

export interface FireTimelinePoint {
  date: string;
  active_pixels: number;
  new_burned_pixels: number;
  cumulative_burned_pixels: number;
  new_burned_km2: number;
  cumulative_burned_km2: number;
}

export interface FireLifecycle {
  event_id: string;
  event_name: string;
  spatial_scope: string | null;
  dates: string[];
  selected_date: string;
  bounds: [number, number, number, number];
  layers: RasterLayerResult[];
  timeline: FireTimelinePoint[];
  selected_metrics: FireTimelinePoint;
  source_label: string;
  caveat: string;
  prediction_status: string;
}

export interface FireDataStatus {
  status: "matched" | "city_assessed" | "weather_assessed" | "no_match" | "no_scope" | "outside_scope";
  workflow?: "fire" | "city";
  focus?: "fire" | "weather";
  evidence?: "low" | "elevated";
  message: string;
  details?: string[];
  detail?: string;
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

export interface SuggestionItem {
  title: string;
  /** Wording verified to trigger the topic. Sent as-is; never paraphrased. */
  ask: string;
  does: string;
  /** True when the wording refers back to an earlier answer. Never offer these
   *  as buttons: a click sends immediately, and "it" would refer to nothing. */
  follow_up?: boolean;
}

export interface SuggestionsPayload {
  type: "suggestions";
  items: SuggestionItem[];
}

/** `GET /api/capabilities` - the declaration the agent answers "what can you
 *  do" from, served so the starter questions come from the same list. */
export interface CapabilitiesPayload {
  topics: SuggestionItem[];
  archive: {
    event_count?: number;
    events?: { name: string; span: string | null; has_burned_area_labels: boolean }[];
  };
  approval_required: { hazard_object: string; source: string }[];
  consent_rule: string;
}

export interface ChatMessage {
  role: "user" | "agent";
  content: string;
  /** Clarification payload, so agent turns can render their options as buttons */
  clarification?: ClarificationPayload;
  /** Questions offered under a capability answer. Unlike clarification options
   *  these are sent on click rather than appended: nothing is being asked, so
   *  there is no answer to assemble. */
  suggestions?: SuggestionItem[];
}

export type SessionStatus = "idle" | "analyzing" | "complete" | "failed";

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  status: SessionStatus;
  preview: string;
  message_count: number;
}

export interface WorkspaceSnapshot {
  status: SessionStatus;
  messages: ChatMessage[];
  contract: AnalysisContract | null;
  stages: Record<StageId, StageStatus>;
  plan: ExecutionPlan | null;
  layers: LayerResult[];
  rasters: RasterLayerResult[];
  fireDataStatus: FireDataStatus | null;
  fireLifecycle: FireLifecycle | null;
  spatialAnalysis: SpatialAnalysis | null;
  fireContext: FireContext | null;
  analysisView: "before" | "after" | "difference";
}

export interface ArchivedSession extends SessionSummary {
  snapshot: Partial<WorkspaceSnapshot>;
}
