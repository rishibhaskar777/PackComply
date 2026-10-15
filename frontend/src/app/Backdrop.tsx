/**
 * Fixed ambient layer behind the app: three soft aurora blobs drifting on long CSS loops (transform only)
 * over a faint dot grid that fades towards the bottom. Paused while the tab is hidden; still under reduced motion.
 */
export function Backdrop({ variant = "app" }: { variant?: "app" | "auth" }) {
  return (
    <div className={`backdrop backdrop--${variant}`} aria-hidden="true">
      <span className="backdrop__grid" />
      <span className="aurora aurora--indigo" />
      <span className="aurora aurora--violet" />
      <span className="aurora aurora--cyan" />
    </div>
  );
}
