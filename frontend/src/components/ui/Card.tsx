import { HTMLAttributes, ReactNode } from "react";

type CardProps = Omit<HTMLAttributes<HTMLElement>, "title"> & {
  title?: ReactNode; description?: ReactNode; action?: ReactNode;
  headingLevel?: 2 | 3; flush?: boolean;
};

/** Bordered surface with an optional header (title, description, action). `flush` drops body padding for tables. */
export function Card({ title, description, action, headingLevel = 2, flush = false, className = "", children, ...props }: CardProps) {
  const Heading = `h${headingLevel}` as const;
  return (
    <section className={`card ${className}`.trim()} {...props}>
      {(title || action) && (
        <header className="card__header">
          <div className="card__heading">
            {title && <Heading className="card__title">{title}</Heading>}
            {description && <p className="card__description">{description}</p>}
          </div>
          {action && <div className="card__action">{action}</div>}
        </header>
      )}
      <div className={flush ? "card__body card__body--flush" : "card__body"}>{children}</div>
    </section>
  );
}

type StatCardProps = { label: ReactNode; value: ReactNode; caption?: ReactNode; icon: ReactNode; tone?: "primary" | "violation" | "review" | "pass" };

export function StatCard({ label, value, caption, icon, tone = "primary" }: StatCardProps) {
  return (
    <section className="stat-card">
      <div className="stat-card__top">
        <p className="stat-card__label">{label}</p>
        <span className={`icon-chip icon-chip--${tone}`} aria-hidden="true">{icon}</span>
      </div>
      <p className="stat-card__value tabular">{value}</p>
      {caption && <p className="stat-card__caption">{caption}</p>}
    </section>
  );
}
