import { KeyboardEvent, ReactNode, useId, useRef } from "react";
import { m } from "motion/react";
import { SPRING } from "../../lib/motion";

type TabItem<T extends string> = { value: T; label: ReactNode; count?: number; /** Shown but not selectable (kept focusable so its tooltip is reachable). */ disabled?: boolean; title?: string };
type TabsProps<T extends string> = { label: string; value: T; items: TabItem<T>[]; onChange: (value: T) => void; className?: string; stretch?: boolean };

/** Muted segmented control (tablist). The selected pill slides between options. Arrow keys, Home and End move between tabs. */
export function Tabs<T extends string>({ label, value, items, onChange, className = "", stretch = false }: TabsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const pillId = useId();
  function onKeyDown(event: KeyboardEvent, index: number) {
    const enabled = items.map((item, position) => (item.disabled ? -1 : position)).filter(position => position >= 0);
    const step = (direction: 1 | -1) => enabled[(enabled.indexOf(index) + direction + enabled.length) % enabled.length];
    const next = event.key === "ArrowRight" ? step(1) : event.key === "ArrowLeft" ? step(-1) : event.key === "Home" ? enabled[0] : event.key === "End" ? enabled[enabled.length - 1] : null;
    if (next === null) return;
    event.preventDefault();
    onChange(items[next].value);
    refs.current[next]?.focus();
  }
  return (
    <div role="tablist" aria-label={label} className={`tabs ${stretch ? "tabs--stretch" : ""} ${className}`.trim()}>
      {items.map((item, index) => {
        const selected = item.value === value;
        return (
          <button key={item.value} ref={element => { refs.current[index] = element; }} type="button" role="tab" aria-selected={selected} tabIndex={selected ? 0 : -1}
            title={item.title} aria-disabled={item.disabled || undefined}
            className={`${selected ? "tabs__tab is-active" : "tabs__tab"}${item.disabled ? " is-disabled" : ""}`} onClick={() => { if (!item.disabled) onChange(item.value); }} onKeyDown={event => onKeyDown(event, index)}>
            {selected && <m.span layoutId={`tabs-pill-${pillId}`} className="tabs__pill" transition={SPRING} aria-hidden="true" />}
            <span className="tabs__label">
              {item.label}
              {item.count !== undefined && <span className="tabs__count tabular">{item.count}</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}
