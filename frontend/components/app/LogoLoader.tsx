/**
 * LogoLoader.tsx  (F18)
 * =====================
 * The logo-morph loader shown while the app boots or a page waits on data.
 *
 * The mark is a document page whose outline morphs into a circle (the "mind")
 * and back, while a gradient sweeps its stroke. The morph is pure CSS: the
 * SVG path's `d` is animated with keyframes (supported in Chromium and
 * Firefox; Safari simply shows the static page shape, which is still a fine
 * loading mark). No JavaScript runs per frame.
 *
 * It is a `role="status"` with a label, so assistive tech hears "Loading
 * projects" rather than nothing. Under reduced motion the shape holds still
 * and only the label conveys progress.
 */

export default function LogoLoader({
  label = "Loading",
  size = 56,
  fullscreen = false,
}: {
  label?: string;
  size?: number;
  /** Centre in the viewport -- for app boot rather than inline sections. */
  fullscreen?: boolean;
}) {
  return (
    <div
      role="status"
      aria-label={label}
      className={`flex flex-col items-center justify-center gap-3 ${
        fullscreen ? "min-h-[60vh]" : "py-12"
      }`}
    >
      <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" className="logo-loader">
        <defs>
          <linearGradient id="logo-loader-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--accent)" />
            <stop offset="50%" stopColor="var(--violet)" />
            <stop offset="100%" stopColor="var(--cyan)" />
          </linearGradient>
        </defs>
        <path
          className="logo-loader-shape"
          d="M14 6 L30 6 L38 14 L38 42 L14 42 Z"
          fill="none"
          stroke="url(#logo-loader-grad)"
          strokeWidth="3"
          strokeLinejoin="round"
        />
        <circle className="logo-loader-core" cx="24" cy="25" r="4" fill="url(#logo-loader-grad)" />
      </svg>
      <span className="text-xs" style={{ color: "var(--text-muted)" }}>
        {label}…
      </span>
    </div>
  );
}
