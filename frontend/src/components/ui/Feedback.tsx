import { CSSProperties, ReactNode, useId } from "react";
import { CircleAlert, Info } from "lucide-react";
import { CountUp } from "./CountUp";

/** 6px risk track; fill colour by score (>=50 violation, >=25 review, else pass). The fill grows from 0 on mount; the score is always shown as text beside it. */
export function RiskBar({ score }: { score: number }) {
  const clamped = Math.max(0, Math.min(100, score));
  const tone = clamped >= 50 ? "violation" : clamped >= 25 ? "review" : "pass";
  return <span className="risk-bar" aria-hidden="true"><span className={`risk-bar__fill risk-bar__fill--${tone}`} style={{ width: `${clamped}%` }} /></span>;
}

export function RiskScore({ score }: { score: number }) {
  return <span className="risk-score"><span className="risk-score__value mono"><CountUp value={score} /></span><RiskBar score={score} /></span>;
}

/** A package label passing under a moving scan line. Colours come from tokens; the line stops under reduced motion. */
export function ScanIllustration() {
  const id = useId();
  return (
    <svg className="scan-art" width="120" height="88" viewBox="0 0 120 88" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={`${id}-beam`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className="scan-art__beam-stop" stopOpacity="0" />
          <stop offset="1" className="scan-art__beam-stop" stopOpacity="0.35" />
        </linearGradient>
      </defs>
      <rect className="scan-art__box" x="18" y="10" width="84" height="68" rx="8" />
      <rect className="scan-art__label" x="28" y="20" width="64" height="48" rx="4" />
      <rect className="scan-art__line scan-art__line--strong" x="34" y="27" width="34" height="4" rx="2" />
      <rect className="scan-art__line" x="34" y="36" width="52" height="3" rx="1.5" />
      <rect className="scan-art__line" x="34" y="43" width="44" height="3" rx="1.5" />
      <g className="scan-art__barcode">
        {[0, 3, 5, 9, 11, 14, 18, 20, 23].map((x, index) => <rect key={x} x={34 + x} y="52" width={index % 3 === 0 ? 2 : 1} height="9" />)}
      </g>
      <g className="scan-art__scanner">
        <rect x="20" y="0" width="80" height="14" fill={`url(#${id}-beam)`} />
        <rect className="scan-art__laser" x="20" y="13" width="80" height="1.5" rx="0.75" />
      </g>
    </svg>
  );
}

type EmptyStateProps = { icon?: ReactNode; title: ReactNode; description?: ReactNode; action?: ReactNode };

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <span className="empty-state__art" aria-hidden="true">
        <ScanIllustration />
        {icon && <span className="empty-state__icon">{icon}</span>}
      </span>
      <h2 className="empty-state__title">{title}</h2>
      {description && <p className="empty-state__description">{description}</p>}
      {action && <div className="empty-state__action">{action}</div>}
    </div>
  );
}

export function Skeleton({ width, height = 16, className = "", style }: { width?: number | string; height?: number | string; className?: string; style?: CSSProperties }) {
  return <span className={`skeleton ${className}`.trim()} style={{ width, height, ...style }} aria-hidden="true" />;
}

/** Placeholder rows for a table or list while it loads. */
export function SkeletonRows({ rows = 5, label }: { rows?: number; label: string }) {
  return (
    <div className="skeleton-rows" role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton-rows__row" key={index}>
          <Skeleton width="38%" /><Skeleton width="22%" /><Skeleton width="14%" /><Skeleton width="12%" />
        </div>
      ))}
    </div>
  );
}

type AlertProps = { variant?: "error" | "info"; title?: ReactNode; children?: ReactNode; action?: ReactNode; className?: string };

export function Alert({ variant = "error", title, children, action, className = "" }: AlertProps) {
  const Icon = variant === "error" ? CircleAlert : Info;
  return (
    <div className={`alert alert--${variant} ${className}`.trim()}>
      <Icon className="alert__icon" size={16} aria-hidden="true" />
      <div className="alert__content" role={variant === "error" ? "alert" : "status"}>
        {title && <p className="alert__title">{title}</p>}
        {children && <div className="alert__body">{children}</div>}
      </div>
      {action && <div className="alert__action">{action}</div>}
    </div>
  );
}
