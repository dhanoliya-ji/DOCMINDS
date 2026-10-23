/**
 * embeddingColors.ts
 * ==================
 * The embedding cloud's colours, in their own tiny module so the page's
 * legend can use them without importing the three.js scene (which must stay
 * lazy-loaded -- see components/EmbeddingCloud.tsx).
 */

/** Mid-tone colours that read on both the light and the dark background. */
export const DOCUMENT_COLORS = ["#6366f1", "#06b6d4", "#a855f7", "#10b981", "#f97316", "#ec4899", "#84cc16", "#0ea5e9"];
/** A search hit: warm and bright, unlike any document colour. */
export const HIT_COLOR = "#f59e0b";
/** Everything else while there ARE hits: pushed back. */
export const DIM_COLOR = "#94a3b8";
