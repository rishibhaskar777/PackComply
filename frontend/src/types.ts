/** One OCR box: four [x, y] corners in original-photo pixels. */
export type BBox = number[][];
export type Field = { value: string | null; confidence: number; needs_review: boolean; source_text?: string | null; source_bboxes?: BBox[] | null; source?: "OCR" | "INSPECTOR" };
export type FindingStatus = "PASS" | "FAIL" | "REVIEW" | "NOT_APPLICABLE";
export type Finding = {
  rule_id: string; rule_name: string; status: FindingStatus; severity: string; message: string; field: string | null; value: string | null;
  legal_reference?: string | null; source_bboxes?: BBox[] | null; fields?: string[]; corrected?: boolean; recommended_action?: string | null;
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
  image?: { id: string; original_filename: string; width?: number | null; height?: number | null; checksum?: string | null; byte_size?: number };
  ocr?: { raw_text: string; languages: string[]; results: OcrLine[]; duration_ms?: number | null };
  extraction?: { fields: Record<string, Field> };
  compliance?: Compliance;
  quantity_verification?: QuantityVerification | null;
  risk?: { risk_score: number; risk_level: string; reasons: RiskReason[] } | null;
  product_name?: string; brand_name?: string | null; maker_name?: string | null;
  corrections?: FieldCorrection[]; review?: Review;
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
};

export type ComplianceRule = { code: string; title: string; description: string; legal_reference: string | null; severity: string; applies_when?: string | null };
export type Ruleset = { ruleset_id: string; version: string; label: string; title: string; disclaimer?: string | null; rules: ComplianceRule[] };
