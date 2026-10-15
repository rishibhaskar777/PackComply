/** Logo tile: brand gradient square with three white label lines (a soft glow in dark). */
export function BrandMark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`brand-mark ${className}`.trim()} style={{ width: size, height: size }} aria-hidden="true">
      <svg width={size * 0.5} height={size * 0.5} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M3 4h10M3 8h7M3 12h9" />
      </svg>
    </span>
  );
}
