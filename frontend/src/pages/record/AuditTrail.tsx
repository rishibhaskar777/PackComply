import { ReactNode } from "react";
import { BadgeCheck, Camera, FilePlus2, PenLine, RefreshCw, Scale, ScanText, Undo2 } from "lucide-react";
import { Card, Skeleton } from "../../components/ui";
import { fill, TranslationKey, useI18n } from "../../i18n";
import { AuditEvent } from "../../types";
import { FIELD_LABELS } from "./Findings";

const ICONS: Record<string, typeof FilePlus2> = { CREATED: FilePlus2, PHOTO_UPLOADED: Camera, OCR_COMPLETED: ScanText, COMPLIANCE_EVALUATED: Scale, CORRECTION: PenLine, CORRECTION_UNDONE: Undo2, REANALYSED: RefreshCw, VERIFIED: BadgeCheck };
const TITLES: Record<string, TranslationKey> = { CREATED: "recAuditCreated", PHOTO_UPLOADED: "recAuditPhoto", OCR_COMPLETED: "recAuditOcr", COMPLIANCE_EVALUATED: "recAuditCompliance", CORRECTION: "recAuditCorrection", CORRECTION_UNDONE: "recAuditUndone", REANALYSED: "recAuditReanalysed", VERIFIED: "recAuditVerified" };

const text = (value: unknown) => (value === null || value === undefined || value === "" ? null : String(value));

/** The short, mono form of a SHA-256: first eight hex digits. */
export const shortChecksum = (checksum: string) => `${checksum.slice(0, 8)}…`;

/** Timestamps are UTC; one without an offset (SQLite) is read as UTC rather than local time. */
export const parseTimestamp = (value: string) => new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`);

export function formatDateTime(value: string | null | undefined, locale: string): string {
  if (!value) return "";
  const date = parseTimestamp(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function Details({ event }: { event: AuditEvent }) {
  const { t } = useI18n();
  const d = event.details;
  const fieldName = (name: unknown) => t(FIELD_LABELS[String(name)] ?? "productInformation");
  const parts: ReactNode[] = [];
  switch (event.type) {
    case "PHOTO_UPLOADED":
      if (text(d.filename)) parts.push(<span key="f" className="mono">{text(d.filename)}</span>);
      if (text(d.checksum)) parts.push(<span key="c">SHA-256 <span className="mono" title={text(d.checksum)!}>{shortChecksum(text(d.checksum)!)}</span></span>);
      break;
    case "OCR_COMPLETED":
      if (Array.isArray(d.languages)) parts.push(<span key="l" className="mono">{(d.languages as string[]).join(" + ")}</span>);
      if (typeof d.duration_ms === "number") parts.push(<span key="d" className="mono tabular">{fill(t("recSeconds"), { seconds: Math.max(1, Math.round(d.duration_ms / 1000)) })}</span>);
      break;
    case "COMPLIANCE_EVALUATED":
      parts.push(<span key="r">{text(d.ruleset) ?? `v${text(d.ruleset_version) ?? ""}`}</span>);
      parts.push(<span key="n" className="tabular">{fill(t("recAuditCounts"), { failed: Number(d.failed ?? 0), review: Number(d.review ?? 0), passed: Number(d.passed ?? 0) })}</span>);
      break;
    case "CORRECTION": case "CORRECTION_UNDONE":
      parts.push(<span key="f">{fieldName(d.field)}</span>);
      parts.push(<span key="v" className="mono audit-change">{text(d.original) ?? t("notDetected")} <span aria-hidden="true">→</span><span className="sr-only">{t("recTo")}</span> {text(d.corrected)}</span>);
      if (text(d.reason)) parts.push(<span key="r" className="audit-note">{text(d.reason)}</span>);
      break;
    case "VERIFIED":
      if (text(d.remarks)) parts.push(<span key="m" className="audit-note">“{text(d.remarks)}”</span>);
      break;
  }
  return parts.length ? <p className="audit-item__details">{parts.map((part, index) => <span key={index}>{part}</span>)}</p> : null;
}

/** Vertical timeline of everything that happened to the record. */
export function AuditTrail({ events, loading }: { events: AuditEvent[] | null; loading: boolean }) {
  const { t, locale } = useI18n();
  return (
    <Card title={t("recAuditTitle")} description={t("recAuditHint")} className="audit-card">
      {loading && !events ? (
        <div role="status"><span className="sr-only">{t("loading")}</span>{[0, 1, 2].map(index => <Skeleton key={index} height={36} className="audit-skeleton" />)}</div>
      ) : (
        <ol className="audit-timeline">
          {(events ?? []).map((event, index) => {
            const Icon = ICONS[event.type] ?? FilePlus2;
            return (
              <li key={`${event.type}-${event.at}-${index}`} className={`audit-item audit-item--${event.type.toLowerCase().replace(/_/g, "-")}`} data-event={event.type}>
                <span className="audit-item__icon" aria-hidden="true"><Icon size={14} /></span>
                <div className="audit-item__body">
                  <p className="audit-item__title"><strong>{t(TITLES[event.type] ?? "recAuditCreated")}</strong>{event.actor && <span className="audit-item__actor"> · {event.actor}</span>}</p>
                  <Details event={event} />
                </div>
                <time className="audit-item__time mono tabular" dateTime={event.at}>{formatDateTime(event.at, locale)}</time>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
