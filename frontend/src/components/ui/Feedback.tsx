import { CSSProperties, ReactNode } from "react";
import { CircleAlert, Info } from "lucide-react";

/** 6px risk track; fill colour by score (>=50 violation, >=25 review, else pass). The score is always shown as text beside it. */
export function RiskBar({ score }: { score: number }) {
  const clamped = Math.max(0, Math.min(100, score));
  const tone = clamped >= 50 ? "violation" : clamped >= 25 ? "review" : "pass";
  return <span className="risk-bar" aria-hidden="true"><span className={`risk-bar__fill risk-bar__fill--${tone}`} style={{ width: `${clamped}%` }} /></span>;
}

export function RiskScore({ score }: { score: number }) {
  return <span className="risk-score"><span className="risk-score__value mono">{score}</span><RiskBar score={score} /></span>;
}

type EmptyStateProps = { icon?: ReactNode; title: ReactNode; description?: ReactNode; action?: ReactNode };

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      {icon && <span className="empty-state__icon" aria-hidden="true">{icon}</span>}
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
