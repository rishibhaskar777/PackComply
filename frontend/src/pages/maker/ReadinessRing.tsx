import { useId } from "react";

/** Circular progress: the share of products ready to print. The number is always shown as text in the middle. */
export function ReadinessRing({ percent, size = 112, label }: { percent: number; size?: number; label: string }) {
  const id = useId();
  const stroke = 10;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div className="ring" style={{ width: size, height: size }} role="img" aria-label={label}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" focusable="false">
        <defs>
          <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" className="ring__stop ring__stop--from" />
            <stop offset="1" className="ring__stop ring__stop--to" />
          </linearGradient>
        </defs>
        <circle className="ring__track" cx={size / 2} cy={size / 2} r={radius} strokeWidth={stroke} fill="none" />
        <circle className="ring__value" cx={size / 2} cy={size / 2} r={radius} strokeWidth={stroke} fill="none" stroke={`url(#${id}-fill)`} strokeLinecap="round"
          strokeDasharray={circumference} strokeDashoffset={circumference * (1 - clamped / 100)} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </svg>
      <span className="ring__text tabular" aria-hidden="true">{clamped}<small>%</small></span>
    </div>
  );
}
