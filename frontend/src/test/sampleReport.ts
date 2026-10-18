import type { EvalReport } from "../types";

const field = (tp: number, fp: number, fn: number) => ({ tp, fp, fn, precision: tp + fp ? tp / (tp + fp) : 1, recall: tp + fn ? tp / (tp + fn) : 1 });

/** A small, internally consistent evaluation report in the shape `batch_evaluate --report-json` writes. */
export const sampleReport: EvalReport = {
  schema: 1,
  runs: [
    {
      label: "unseen-2026-10-29", kind: "unseen", generated_at: "2026-10-29T10:00:00+00:00", note: "12 new phone photos.",
      ruleset: { id: "LM_PC_2011", version: "2", rules: 14 },
      dataset_sizes: { dev: 0, phone_holdout_retired_to_dev: 0, unseen: 12, total: 12 },
      processing_seconds: { median: 21.5, max: 40, photos: 12 },
      groups: [{
        name: "ALL", photos: 12, fields_scored: 228, precision: 0.9, recall: 0.75,
        per_field: { mrp: field(9, 1, 2), net_quantity: field(10, 0, 2), manufacturer: field(6, 1, 5) },
        rule_agreement: { agree: 140, total: 168 },
        verdict_confusion: { COMPLIANT: { COMPLIANT: 1, NEEDS_REVIEW: 3 }, NEEDS_REVIEW: { NEEDS_REVIEW: 5 }, NON_COMPLIANT: { NON_COMPLIANT: 2, NEEDS_REVIEW: 1 } },
        verdicts_correct: 8, false_non_compliant: 0, false_pass_mandatory: 1,
      }],
    },
    {
      label: "development-2026-10", kind: "development", generated_at: "2026-10-18T10:00:00+00:00",
      ruleset: { id: "LM_PC_2011", version: "2", rules: 14 },
      dataset_sizes: { dev: 23, phone_holdout_retired_to_dev: 14, unseen: 0, total: 23 },
      processing_seconds: { median: 23.6, max: 105.2, photos: 23 },
      groups: [{
        name: "DEV", photos: 23, fields_scored: 438, precision: 0.968, recall: 0.825,
        per_field: { mrp: field(20, 0, 3) }, rule_agreement: { agree: 290, total: 322 }, verdict_confusion: { COMPLIANT: { COMPLIANT: 17 } },
        verdicts_correct: 17, false_non_compliant: 0, false_pass_mandatory: 2,
      }],
    },
  ],
};
