/**
 * Fixed ambient layer behind the app, in the sign-in page's measurement language: faint 8px/40px graph paper
 * that fades out below the first ~400px, and one soft indigo glow drifting at the top centre (transform only).
 * Paused while the tab is hidden (see src/theme/theme.tsx); still under reduced motion.
 */
export function Backdrop() {
  return (
    <div className="backdrop" aria-hidden="true">
      <span className="backdrop__grid" />
      <span className="backdrop__glow" />
    </div>
  );
}
