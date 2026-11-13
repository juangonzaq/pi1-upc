// Contratos de la API de Perforación IA (adaptador HTTP en src/perfo/adapters/http/api.py).

export type DocState =
  | "CARGADO"
  | "EN_COLA"
  | "PROCESADO"
  | "EN_REVISION"
  | "VALIDADO"
  | "APROBADO"
  | "ERROR"
  | "DUPLICADO_PENDIENTE";

export type FieldType = "numeric" | "code" | "time" | "date" | "free_text" | "master_ref";
export type Route = "AUTO" | "REVIEW";

export interface Job {
  id: string;
  kind: string;
  status: "running" | "done" | "failed";
  started_at: string;
  finished_at: string | null;
  log: string[];
  log_size: number;
  result: CycleResult | null;
  error: string | null;
}

export interface UploadLimits {
  max_bytes: number;
  reference_long_px: number;
  template_dpi: number;
  recommended_dpi: number;
  min_dpi: number;
}

export interface Health {
  ok: boolean;
  workspace: string;
  profile: string | null;
  device: string;
  active_model: string | null;
  active_calibrated: boolean;
  masters: Record<string, number>;
  synthetic: boolean;
  ground_truth: boolean;
  reference_csv: string | null;
  split: boolean;
  busy: Job | null;
  upload?: UploadLimits;
}

export interface Learning {
  corrections: number;
  confirmed_review: number;
  confirmed_auto: number;
  excluded_protected: number;
  documents: number;
  split_ready: boolean;
  last_cycle: { id: string; counts: Record<string, unknown> } | null;
}

export interface Status {
  documents: number;
  by_state: Partial<Record<DocState, number>>;
  by_batch: Record<string, number>;
  fields: { auto: number; review: number };
  active_model: {
    version: string;
    status: string;
    parent: string | null;
    thresholds: Record<string, number | null> | null;
    temperatures: Record<string, number | null> | null;
  } | null;
  learning: Learning;
  models: number;
}

export interface Sample {
  sample_id: string;
  form_id: string;
  batch: string;
  operator: string | null;
  new_operator: boolean | null;
  bad_scan: string | null;
  business_key: string | null;
  rescan: boolean;
  document: { id: string; state: DocState } | null;
}

export interface ProcessResult {
  document_id: string;
  state: DocState;
  cause: string | null;
  seconds: number;
  stages: Record<string, number>;
  fields: number;
  review: number;
  model: string;
}

export interface IngestResult {
  document_id: string;
  outcome: "nuevo" | "repetida" | "reintentada" | "duplicado_pendiente";
  processing?: ProcessResult;
  state: DocState;
  cause: string | null;
}

export interface DocSummary {
  id: string;
  form_id: string | null;
  source_name: string;
  batch: string | null;
  state: DocState;
  cause: string | null;
  business_key: string | null;
  uploaded_by: string;
  uploaded_at: string;
  fields: number;
  review: number;
  auto: number;
  rescan: boolean;
  duplicate_of: string | null;
}

export interface Candidate {
  code: string;
  description: string | null;
  score: number;
}

export interface Homologation {
  master_type: string;
  input: string;
  normalized: string;
  code: string | null;
  description: string | null;
  score: number;
  method: string;
  accepted: boolean;
  candidates: Candidate[];
}

export interface Field {
  id: string;
  label: string;
  block: string;
  block_label: string;
  table: string | null;
  row: number | null;
  col: string | null;
  type: FieldType;
  master_type: string | null;
  code_set: string | null;
  required: string;
  box: [number, number, number, number];
  text: string | null;
  value: string | null;
  value_label: string | null;
  confidence: number | null;
  raw_score: number | null;
  reliable: boolean | null;
  model_version: string | null;
  route: Route;
  reasons: { code: string; text: string }[];
  violations: { rule: string; message: string; critical: boolean }[];
  homologation: Homologation | null;
  mark: string | null;
  empty: boolean;
  crop: string | null;
  validated: { value: string | null; source: string } | null;
  truth: { value: string | null; text: string | null; mark: string | null } | null;
  correct: boolean | null;
}

export interface DocDetail {
  document: DocSummary & {
    sha256: string;
    template_version: string | null;
    content_type: string;
    has_preprocessed: boolean;
    transform: Record<string, unknown>;
    alignment: Record<string, unknown> | null;
    quality_warnings: string[];
  };
  run: { run_id: string; model_version: string | null; calibration_id: string | null; thresholds: Record<string, number | null> } | null;
  summary: {
    fields: number;
    auto: number;
    review: number;
    read: number;
    by_type: Record<string, number>;
    truth_available: boolean;
    truth_compared: number;
    truth_correct: number;
    auto_errors: number;
  };
  fields: Field[];
  history: { from: string | null; to: string; cause: string | null; actor: string; at: string }[];
  records: { id: string; version: number; validated_by: string; validated_at: string; sha256: string; stats: Record<string, unknown> }[];
  approvals: { decision: string; approver: string; comment: string; at: string }[];
}

export interface Catalogs {
  masters: Record<string, { code: string; description: string }[]>;
  code_sets: Record<string, { code: string; description: string }[]>;
}

export interface Gate {
  gate: string;
  name: string;
  passed: boolean;
  detail: string;
}

export interface ModelInfo {
  version: string;
  status: string;
  active: boolean;
  parent: string | null;
  created_at: string;
  base_checkpoint: string;
  artifact_sha256: string | null;
  description: string | null;
  dataset_snapshot: string | null;
  training: { best_step: number; steps: number; seconds: number; initial_select_cer: number; best_select_cer: number } | null;
  calibration: { id: string; temperatures: Record<string, number | null>; thresholds: Record<string, number | null>; created_at: string } | null;
  gates: Gate[] | null;
  gates_against: string | null;
  relative_cer_reduction_pct: number | null;
  required_gates: string[];
  routing: { coverage: number; residual_risk: number; ece: number; n_fields: number; auto: number } | null;
  metrics: {
    cer?: number;
    wer?: number;
    exact?: number;
    field_accuracy?: number;
    cer_unseen_operators?: number;
    n?: number;
    homologation?: { acc_at_1: number; recall_at_3: number };
  };
  cycle: { snapshot: string; stats: Record<string, unknown>; ie5_pct: number | null; status: string } | null;
  promotions: { version: string; previous: string | null; at: string; approver: string; note?: string; reason?: string }[];
  status_log: { status: string; note: string; at: string }[];
}

export interface ModelsResponse {
  active: string | null;
  models: ModelInfo[];
  history: { version: string; previous: string | null; at: string; approver: string; note?: string; reason?: string }[];
  learning: Learning;
}

export interface CycleResult {
  status: string;
  candidate: string | null;
  snapshot: string;
  stats: Record<string, unknown>;
  ie5_pct: number | null;
  gates: Gate[] | null;
  latency: { p50_s: number; p95_s: number } | null;
  active_before: string;
  error: string | null;
}

export interface PromoteResult {
  promoted: boolean;
  failed: string[];
  active: string;
  previous?: string | null;
  evidence: string;
}

export interface BenchCrop {
  crop_id: string;
  crop: string;
  field_id: string;
  label: string;
  block_label: string;
  row: number | null;
  type: FieldType;
  document_id: string;
  form_id: string | null;
  origin: string;
  truth: string | null;
}

export interface BenchRow {
  text: string;
  score: number | null;
  raw_score: number | null;
  reliable: boolean;
  threshold: number | null;
  route: Route;
  reason: string | null;
  correct: boolean | null;
}

export interface BenchModelResult {
  rows: BenchRow[];
  seconds: number;
  ms_per_crop: number;
  summary: { n: number; with_truth: number; exact: number | null; cer: number | null; auto: number; auto_errors: number };
}

export interface EvolutionPoint {
  version: string;
  kind: "base" | "initial" | "cycle";
  label: string;
  short: string;
  status: string;
  active: boolean;
  has_weights: boolean;
  created_at: string;
  accuracy: number;
  cer: number;
  exact: number;
  n: number | null;
  by_type: Partial<Record<FieldType, { accuracy: number; cer: number; exact: number; n: number }>>;
}

export interface Evolution {
  points: EvolutionPoint[];
  documents: number | null;
  real_handwriting: boolean;
}

export type EvolutionVerdict = "same_right" | "improved" | "same_wrong" | "worse";

export interface EvolutionExample {
  crop: string;
  type: FieldType;
  label: string;
  truth: string;
  before: string;
  after: string;
  verdict: EvolutionVerdict;
}

export interface EvolutionExamples {
  available: boolean;
  from?: string;
  to?: string;
  n?: number;
  counts?: Partial<Record<EvolutionVerdict, number>>;
  examples?: EvolutionExample[];
}
