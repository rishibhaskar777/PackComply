import { ReactNode } from "react";
import { TranslationKey, useI18n } from "../../i18n";

export type BadgeTone = "violation" | "review" | "pass" | "neutral" | "primary" | "critical";

// Verdicts, finding statuses and risk levels share one colour language; the text always names the state.
const VARIANTS: Record<string, { tone: BadgeTone; label: TranslationKey }> = {
  NON_COMPLIANT: { tone: "violation", label: "verdictNonCompliant" },
  NEEDS_REVIEW: { tone: "review", label: "verdictNeedsReview" },
  COMPLIANT: { tone: "pass", label: "verdictCompliant" },
  PASS: { tone: "pass", label: "pass" },
  FAIL: { tone: "violation", label: "fail" },
  REVIEW: { tone: "review", label: "warning" },
  WARNING: { tone: "review", label: "warning" },
  NOT_APPLICABLE: { tone: "neutral", label: "notApplicable" },
  LOW: { tone: "pass", label: "low" },
  MEDIUM: { tone: "review", label: "medium" },
  HIGH: { tone: "violation", label: "high" },
  CRITICAL: { tone: "critical", label: "critical" },
  PENDING: { tone: "neutral", label: "pending" },
  CREATED: { tone: "neutral", label: "created" },
  IMAGE_UPLOADED: { tone: "neutral", label: "imageUploadedStatus" },
  OCR_PROCESSING: { tone: "primary", label: "ocrProcessing" },
  OCR_COMPLETED: { tone: "primary", label: "ocrCompleted" },
  EXTRACTION_PROCESSING: { tone: "primary", label: "extractionProcessing" },
  EXTRACTION_COMPLETED: { tone: "primary", label: "extractionCompleted" },
  COMPLIANCE_PROCESSING: { tone: "primary", label: "complianceProcessing" },
  COMPLETED: { tone: "neutral", label: "completed" },
  FAILED: { tone: "violation", label: "failed" },
};

export const BADGE_VARIANTS = Object.keys(VARIANTS);

type BadgeProps = { variant?: string | null; tone?: BadgeTone; children?: ReactNode; dot?: boolean; className?: string };

export function Badge({ variant, tone, children, dot = true, className = "" }: BadgeProps) {
  const { t } = useI18n();
  const known = variant ? VARIANTS[variant] : undefined;
  const resolvedTone = tone ?? known?.tone ?? "neutral";
  const label = children ?? (known ? t(known.label) : variant ? variant : t("unknown"));
  return (
    <span className={`badge badge--${resolvedTone} ${className}`.trim()} data-variant={variant ?? undefined}>
      {dot && <span className="badge__dot" aria-hidden="true" />}
      {label}
    </span>
  );
}
