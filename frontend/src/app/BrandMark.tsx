// "Scan a packaged product": four scan-frame corners around an isometric box (24×24 grid).
// public/favicon.svg and public/apple-touch-icon.png draw the same paths.
export const BRAND_MARK_PATHS = [
  "M3 8V5.5A2.5 2.5 0 0 1 5.5 3H8 M16 3h2.5A2.5 2.5 0 0 1 21 5.5V8 M21 16v2.5a2.5 2.5 0 0 1-2.5 2.5H16 M8 21H5.5A2.5 2.5 0 0 1 3 18.5V16",
  "M12 6.8l4.6 2.4v5.6L12 17.2l-4.6-2.4V9.2z",
  "M7.4 9.2L12 11.6l4.6-2.4M12 11.6v5.6",
] as const;

/** Logo tile: brand-gradient rounded square (radius 24% of its size) with the white scan mark; soft shadow in light, glow in dark. */
export function BrandMark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`brand-mark ${className}`.trim()} style={{ width: size, height: size, borderRadius: Math.round(size * 0.24) }} aria-hidden="true">
      <svg width={Math.round(size * 0.66)} height={Math.round(size * 0.66)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" focusable="false">
        {BRAND_MARK_PATHS.map(path => <path key={path} d={path} />)}
      </svg>
    </span>
  );
}
