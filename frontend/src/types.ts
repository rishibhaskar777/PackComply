/** One OCR box: four [x, y] corners in original-photo pixels. */
export type BBox = number[][];
/** Which part of the pack a photo shows. */
export type Panel = "BACK" | "FRONT" | "DATE_STAMP" | "OTHER";
export type FieldAlternative = { image_id: string | null; panel: Panel | null; value: string | null; confidence: number };
export type Field = { value: string | null; confidence: number; needs_review: boolean; source_text?: string | null; source_bboxes?: BBox[] | null; source?: "OCR" | "INSPECTOR"; image_id?: string | null; panel?: Panel | null; alternatives?: FieldAlternative[] };
/** One stored photo; replaced ones stay listed with active=false as evidence. */
export type PhotoInfo = { id: string; panel: Panel; width?: number | null; height?: number | null; checksum?: string | null; uploaded_at: string; active: boolean; original_filename?: string | null; read_error?: string | null };
export type FindingStatus = "PASS" | "FAIL" | "REVIEW" | "NOT_APPLICABLE";
export type Finding = {
  rule_id: string; rule_name: string; status: FindingStatus; severity: string; message: string; field: string | null; value: string | null;
  legal_reference?: string | null; source_bboxes?: BBox[] | null; image_id?: string | null; fields?: string[]; corrected?: boolean; recommended_action?: string | null;
  /** Plain-language remediation (rule catalogue): shown to manufacturers, a hint for officials. */
  fix_title?: string | null; fix_steps?: string[]; example?: string | null; pass_text?: string | null; rule_chip?: string | null;
};
export type Compliance = { overall_status: string; verdict?: string | null; ruleset_label?: string | null; ruleset_version?: string | null; summary: { passed: number; failed: number; review: number; not_applicable?: number }; findings: Finding[] };
export type QuantityVerification = { declared_quantity: string | null; normalized_quantity: string | null; unit: string | null; status: "PASS" | "FAIL" | "REVIEW"; reason: string; rule_code: string; severity: string; evidence: string | null; requirement: string | null };
export type RiskLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type RiskReason = { type: string; points: number; description: string; rule_code?: string | null; severity?: string | null; field?: string | null };
export type FieldCorrection = { id: string; field_name: string; original_value: string | null; corrected_value: string; reason: string | null; corrected_by?: string | null; corrected_by_name?: string | null; created_at: string };
export type Review = { status: "UNREVIEWED" | "VERIFIED"; by?: string | null; at?: string | null; remarks?: string | null };
export type OcrLine = { text: string; confidence: number | null; bbox?: BBox | null; language?: string | null };
export type Summary = {
  id: string; inspection_number: string; status: string; failure_message?: string | null; created_at?: string; completed_at?: string | null;
  image?: { id: string; original_filename: string; width?: number | null; height?: number | null; checksum?: string | null; byte_size?: number; panel?: Panel };
  images?: PhotoInfo[];
  ocr?: { image_id?: string | null; raw_text: string; languages: string[]; results: OcrLine[]; duration_ms?: number | null };
  extraction?: { fields: Record<string, Field> };
  compliance?: Compliance;
  quantity_verification?: QuantityVerification | null;
  risk?: { risk_score: number; risk_level: string; reasons: RiskReason[] } | null;
  product_name?: string; brand_name?: string | null; maker_name?: string | null;
  corrections?: FieldCorrection[]; review?: Review; previous_inspection_id?: string | null;
};
export type ProgressStep = {
  id: string; kind: "prepare" | "read" | "link" | "rules" | "risk"; label: string; status: "pending" | "running" | "done" | "failed";
  panel?: Panel | null; language?: string | null; languages?: string[] | null; image_id?: string | null; count?: number | null; cached?: boolean; message?: string | null; duration_ms?: number | null;
};
export type Progress = {
  inspection_id: string; state: "idle" | "running" | "completed" | "failed"; stage?: string | null; percent: number; completed_steps: number; total_steps: number;
  started_at: string | null; elapsed_ms: number; image_id?: string | null; steps: ProgressStep[]; error: { message: string; step_id: string | null; stage?: string | null } | null; version: number;
};
export type AuditEvent = { type: string; at: string; actor?: string | null; details: Record<string, unknown> };
export type User = { username: string; role?: string; full_name?: string };

export type HistoryItem = { id: string; inspection_number: string; status: string; created_at: string; updated_at: string; product_name: string | null; brand_name?: string | null; maker_name?: string | null; review_status?: string; compliance_status: string | null; verdict?: string | null; image_filename: string | null };
export type HistoryResponse = { items: HistoryItem[]; page: number; page_size: number; total: number; total_pages: number };
export type Pattern = { pattern_key: string; pattern_type: "COMPLIANCE_VIOLATION" | "COMPLIANCE_WARNING" | "MISSING_FIELD"; title: string; description: string; occurrence_count: number; first_seen: string; last_seen: string; severity: string; inspection_ids: string[]; affected_field?: string | null; rule_code?: string | null };
export type RiskItem = { inspection_id: string; product_name: string | null; risk_score: number; risk_level: RiskLevel; compliance_status: string; primary_reason: string; completed_at: string; priority: number };
export type RiskPageData = { items: RiskItem[]; total: number; page: number; page_size: number; total_pages: number; summary: Record<RiskLevel, number> };

export type DashboardPeriod = "7d" | "30d" | "all";
export type Verdict = "NON_COMPLIANT" | "NEEDS_REVIEW" | "COMPLIANT";
export type DashboardSummary = {
  period: DashboardPeriod;
  total_inspections: number;
  verdict_counts: Record<Verdict | "PENDING", number>;
  top_risk: Array<{ id: string; inspection_number: string; product_name: string | null; maker: string | null; verdict: Verdict; risk_score: number | null; risk_level: RiskLevel | null }>;
  repeat_issues: Array<{ rule_id: string; title: string; legal_reference: string | null; label_count: number }>;
  median_processing_seconds: number | null;
  ruleset_version: string;
  /** Rules with the most violations across the caller's inspections (top 5). */
  rule_counts?: Array<{ rule_id: string; title: string; legal_reference: string | null; violations: number; review: number }>;
};

export type ComplianceRule = {
  code: string; title: string; description: string; legal_reference: string | null; severity: string; applies_when?: string | null;
  expected?: string | null; rule_chip?: string | null; fix_title?: string | null; fix_steps?: string[]; example?: string | null; pass_text?: string | null;
};
export type Ruleset = { ruleset_id: string; version: string; label: string; title: string; disclaimer?: string | null; rules: ComplianceRule[] };

/** The evaluation report written by `batch_evaluate --report-json` (GET /api/system/evaluation). */
export type EvalGroup = {
  name: string; photos: number; fields_scored: number; precision: number; recall: number;
  per_field: Record<string, { tp: number; fp: number; fn: number; precision: number; recall: number }>;
  rule_agreement: { agree: number; total: number };
  verdict_confusion: Record<string, Record<string, number>>;
  verdicts_correct: number; false_non_compliant: number; false_pass_mandatory: number;
};
export type EvalRun = {
  label: string; kind: "development" | "unseen"; generated_at: string; note?: string;
  ruleset: { id: string; version: string; rules: number };
  dataset_sizes: { dev: number; phone_holdout_retired_to_dev: number; unseen: number; total: number };
  processing_seconds: { median: number; max: number; photos: number } | null;
  groups: EvalGroup[];
};
export type EvalReport = { schema: number; runs: EvalRun[] };

/** Manufacturer portal: READY (nothing to fix or verify) / FIX (any failed rule) / CHECK (only items to verify on the pack). */
export type Readiness = "READY" | "FIX" | "CHECK";
export type ProductCheck = { inspection_id: string; checked_at: string; fixes: number; review: number; status: Readiness };
export type ProductCard = {
  product_key: string; inspection_id: string; product_name: string; net_quantity: string | null; checked_at: string; checks: number;
  status: Readiness; fixes: number; review: number; passed: number; rules_total: number; has_photo: boolean; image_id: string | null;
  previous_fixes: number | null; improved: boolean; history: ProductCheck[];
};
export type ProductsSummary = {
  total: number; ready: number; fix: number; check: number; readiness_percent: number; open_fixes: number; labels_to_fix: number; products: ProductCard[];
};
export type FixGroup = {
  rule_id: string; title: string; rule_name: string; legal_reference: string | null; rule_chip: string | null; severity: string; status: "FAIL" | "REVIEW";
  product_count: number; fix_steps: string[]; example: string | null; products: Array<{ inspection_id: string; product_name: string; status: string }>;
};
export type FixesResponse = { total_items: number; groups: FixGroup[] };
