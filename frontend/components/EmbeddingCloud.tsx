/**
 * EmbeddingCloud.tsx  (F17)
 * =========================
 * "Show embedding map" on the search page: every chunk of the project as a
 * point in 3D, with the current search's hits lit up.
 *
 * WHAT YOU ARE LOOKING AT
 * -----------------------
 * Each chunk's embedding (hundreds of numbers) is squashed to three by PCA
 * on the server (backend/app/services/embedding_map.py). Chunks about the
 * same thing sit close together, so the cloud shows the SHAPE of a project:
 * a clump per topic, a document that stands apart, and -- after a search --
 * whether the hits come from one neighbourhood or are scattered across it.
 *
 * WHEN THINGS LOAD
 * ----------------
 * Nothing at all until the disclosure is opened: no request, no three.js.
 * Opening it fetches the map and lazy-loads the scene (next/dynamic,
 * ssr:false). The canvas then draws only while on screen (useIsActive).
 *
 * FALLBACKS
 * ---------
 *   * "View as table": the same points as a table -- filename, snippet,
 *     coordinates, and whether each is a search hit. This is what a screen
 *     reader gets (the canvas is aria-hidden), and it is the only view when
 *     the browser has no WebGL.
 *   * prefers-reduced-motion: the scene stays still (no auto-rotate) and
 *     only redraws when dragged.
 */

"use client";

import { ChevronDown, Loader2, Orbit, Table2 } from "lucide-react";
import dynamic from "next/dynamic";
import { useMemo, useRef, useState } from "react";

import { useIsActive, usePrefersReducedMotion } from "@/components/landing/hooks";
import { EmbeddingMap, EmbeddingPoint, api, getErrorMessage } from "@/lib/api";
import { DOCUMENT_COLORS, HIT_COLOR } from "@/lib/embeddingColors";

const Scene = dynamic(() => import("@/components/EmbeddingCloudScene"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center" role="status" aria-label="Loading the 3D view">
      <Loader2 size={20} className="animate-spin" aria-hidden="true" />
    </div>
  ),
});

/** True when the browser can create a WebGL context. Exported for the tests. */
export function hasWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

interface Props {
  projectId: string;
  /** chunk_ids of the current search's results. */
  hitIds?: string[];
}

export default function EmbeddingCloud({ projectId, hitIds = [] }: Props) {
  const [open, setOpen] = useState(false);
  const [map, setMap] = useState<EmbeddingMap | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [tableView, setTableView] = useState(false);
  const [hovered, setHovered] = useState<EmbeddingPoint | null>(null);
  // Checked once, on first open (never during render on the server).
  const [webgl, setWebgl] = useState(true);

  const reduced = usePrefersReducedMotion();
  const frame = useRef<HTMLDivElement>(null);
  const active = useIsActive(frame);

  const hits = useMemo(() => new Set(hitIds), [hitIds]);
  // One colour per document, in order of first appearance.
  const colorIndex = useMemo(() => {
    const index = new Map<string, number>();
    map?.points.forEach((p) => {
      if (!index.has(p.document_id)) index.set(p.document_id, index.size);
    });
    return index;
  }, [map]);
  const legend = useMemo(() => {
    const seen = new Map<string, string>();
    map?.points.forEach((p) => seen.has(p.document_id) || seen.set(p.document_id, p.filename));
    return [...seen.entries()];
  }, [map]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || map || loading) return;
    setWebgl(hasWebGL());
    setLoading(true);
    setError("");
    try {
      setMap(await api.getEmbeddingMap(projectId));
    } catch (err) {
      setError(getErrorMessage(err, "Could not load the embedding map."));
    } finally {
      setLoading(false);
    }
  }

  const showTable = tableView || !webgl;
  const variance = map ? Math.round(map.explained_variance.reduce((a, b) => a + b, 0) * 100) : 0;
  const hitCount = map ? map.points.filter((p) => hits.has(p.chunk_id)).length : 0;

  return (
    <section className="glass mt-6 p-4" aria-label="Embedding map">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls="embedding-map-body"
        className="flex w-full items-center justify-between gap-2 text-sm font-medium"
      >
        <span className="flex items-center gap-2">
          <Orbit size={15} aria-hidden="true" style={{ color: "var(--accent-hover)" }} />
          {open ? "Hide embedding map" : "Show embedding map"}
        </span>
        <ChevronDown
          size={15}
          aria-hidden="true"
          className={reduced ? undefined : "transition-transform"}
          style={{ transform: open ? "rotate(180deg)" : undefined }}
        />
      </button>

      {open && (
        <div id="embedding-map-body" className="mt-3">
          {loading && (
            <p role="status" className="flex items-center gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
              <Loader2 size={14} className="animate-spin" aria-hidden="true" /> Projecting the chunks...
            </p>
          )}
          {error && (
            <div
              role="alert"
              className="rounded-xl px-4 py-3 text-sm"
              style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}
            >
              {error}
            </div>
          )}
          {map && map.points.length === 0 && (
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              No processed chunks yet. Upload a document and wait for it to finish processing.
            </p>
          )}
          {map && map.points.length > 0 && (
            <>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
                <span>
                  {map.truncated
                    ? `Showing ${map.points.length} of ${map.total} chunks`
                    : `${map.points.length} chunks`}
                  {` · the 3 axes keep ${variance}% of the variation`}
                  {hits.size > 0 && ` · ${hitCount} search hit${hitCount === 1 ? "" : "s"} highlighted`}
                </span>
                {webgl && (
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    onClick={() => setTableView((v) => !v)}
                    aria-pressed={tableView}
                  >
                    {tableView ? <Orbit size={13} aria-hidden="true" /> : <Table2 size={13} aria-hidden="true" />}
                    {tableView ? "View in 3D" : "View as table"}
                  </button>
                )}
              </div>
              {!webgl && (
                <p className="mb-2 text-xs" style={{ color: "var(--text-muted)" }}>
                  This browser cannot draw 3D graphics, so the map is shown as a table.
                </p>
              )}

              {showTable ? (
                <div className="max-h-80 overflow-auto">
                  <table className="w-full text-left text-xs">
                    <caption className="sr-only">Chunks of this project and their 3D positions</caption>
                    <thead style={{ color: "var(--text-muted)" }}>
                      <tr>
                        <th className="py-1.5 pr-2 font-medium">File</th>
                        <th className="py-1.5 pr-2 font-medium">Passage</th>
                        <th className="py-1.5 pr-2 font-medium">x, y, z</th>
                        <th className="py-1.5 font-medium">Search hit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {map.points.map((p) => (
                        <tr
                          key={p.chunk_id}
                          className="border-t align-top"
                          style={{ borderColor: "var(--border-subtle)" }}
                          data-hit={hits.has(p.chunk_id) ? "true" : "false"}
                        >
                          <td className="py-1.5 pr-2">{p.filename}</td>
                          <td className="py-1.5 pr-2" style={{ color: "var(--text-secondary)" }}>
                            {p.snippet}
                          </td>
                          <td className="whitespace-nowrap py-1.5 pr-2 font-mono">
                            {p.x.toFixed(2)}, {p.y.toFixed(2)}, {p.z.toFixed(2)}
                          </td>
                          <td className="py-1.5">{hits.has(p.chunk_id) ? "Yes" : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div
                  ref={frame}
                  data-testid="embedding-canvas"
                  className="relative h-80 overflow-hidden rounded-xl"
                  style={{ background: "var(--surface-2)" }}
                >
                  <Scene
                    points={map.points}
                    hits={hits}
                    colorIndex={colorIndex}
                    active={active}
                    reduced={reduced}
                    onHover={setHovered}
                  />
                  {hovered && (
                    <div
                      className="pointer-events-none absolute left-3 top-3 max-w-xs rounded-lg px-3 py-2 text-xs shadow"
                      style={{ background: "var(--bg-elevated)", color: "var(--text-primary)" }}
                    >
                      <p className="font-medium">{hovered.filename}</p>
                      <p style={{ color: "var(--text-secondary)" }}>{hovered.snippet}</p>
                    </div>
                  )}
                  <p className="pointer-events-none absolute bottom-2 right-3 text-[11px]" style={{ color: "var(--text-muted)" }}>
                    {reduced ? "Drag to rotate" : "Drag to rotate · scroll to zoom"}
                  </p>
                </div>
              )}

              {/* Colour key: one swatch per document, plus the hit colour. */}
              <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label="Colour key" style={{ color: "var(--text-secondary)" }}>
                {legend.slice(0, DOCUMENT_COLORS.length).map(([id, name], i) => (
                  <li key={id} className="flex items-center gap-1.5">
                    <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: DOCUMENT_COLORS[i] }} />
                    {name}
                  </li>
                ))}
                {hits.size > 0 && (
                  <li className="flex items-center gap-1.5">
                    <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: HIT_COLOR }} />
                    Search hit
                  </li>
                )}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  );
}
