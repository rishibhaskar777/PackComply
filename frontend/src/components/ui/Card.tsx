import { ReactNode } from "react";
import { HTMLMotionProps, m } from "motion/react";
import { fadeRise, STAGGER, useReducedMotion } from "../../lib/motion";
import { CountUp } from "./CountUp";

type CardProps = Omit<HTMLMotionProps<"section">, "title" | "children"> & {
  title?: ReactNode; description?: ReactNode; action?: ReactNode; children?: ReactNode;
  headingLevel?: 2 | 3; flush?: boolean;
  /** Position in a group that enters together; staggers the entrance by 40 ms per step. */
  index?: number;
};

/** Staggered fade-and-rise for the first appearance; nothing under reduced motion. */
function useEntrance(index: number | undefined) {
  const reduced = useReducedMotion();
  return index !== undefined && !reduced
    ? { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, transition: fadeRise(index * STAGGER) }
    : {};
}

/**
 * Bordered surface with an optional header (title, description, action). `flush` drops body padding for tables.
 * Every card gets a cursor spotlight; padded cards also lift on hover (table cards stay still while you read them).
 */
export function Card({ title, description, action, headingLevel = 2, flush = false, index, className = "", children, ...props }: CardProps) {
  const entrance = useEntrance(index);
  const Heading = `h${headingLevel}` as const;
  const classes = ["card", flush ? "card--flush" : "card--lift", className].filter(Boolean).join(" ");
  return (
    <m.section className={classes} {...entrance} {...props}>
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
    </m.section>
  );
}

type StatCardProps = { label: ReactNode; value: ReactNode; caption?: ReactNode; icon: ReactNode; tone?: "primary" | "violation" | "review" | "pass"; index?: number };

export function StatCard({ label, value, caption, icon, tone = "primary", index = 0 }: StatCardProps) {
  const entrance = useEntrance(index);
  return (
    <m.section className="stat-card" {...entrance}>
      <div className="stat-card__top">
        <p className="stat-card__label">{label}</p>
        <span className={`icon-chip icon-chip--${tone}`} aria-hidden="true">{icon}</span>
      </div>
      <p className="stat-card__value tabular">{typeof value === "number" ? <CountUp value={value} /> : value}</p>
      {caption && <p className="stat-card__caption">{caption}</p>}
    </m.section>
  );
}
