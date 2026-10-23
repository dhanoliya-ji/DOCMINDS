/**
 * EmbeddingCloudScene.tsx  (F17: the WebGL half of the embedding cloud)
 * ======================================================================
 * Draws every chunk of a project as a small sphere at its 3D PCA position
 * (GET /projects/{id}/embedding-map). Loaded only through next/dynamic from
 * EmbeddingCloud.tsx, so three.js never reaches the server bundle or a page
 * that does not open the map.
 *
 * ONE DRAW CALL FOR 2000 POINTS
 * -----------------------------
 * An <instancedMesh> is one geometry drawn N times with a per-instance
 * matrix (position + scale) and colour. Two thousand separate meshes would
 * be two thousand draw calls; this is one. The matrices are written once per
 * change of data or highlight, in an effect -- not every frame.
 *
 * HOVER
 * -----
 * R3F raycasts instanced meshes per instance: the pointer event carries
 * `instanceId`, which indexes straight into `points`. The tooltip itself is
 * ordinary DOM, drawn by the parent, so it uses the theme's colours.
 *
 * COST CONTROL
 * ------------
 *   * dpr capped at 1.5 -- a 3x phone would otherwise shade 9x the pixels
 *   * frameloop "never" while off-screen or the tab is hidden (`active`)
 *   * under reduced motion: no auto-rotate, and frameloop "demand", so a
 *     frame is drawn only when the user drags the view
 */

"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { useLayoutEffect, useMemo, useRef } from "react";
import { Color, type InstancedMesh, Object3D } from "three";

import type { EmbeddingPoint } from "@/lib/api";
import { DIM_COLOR, DOCUMENT_COLORS, HIT_COLOR } from "@/lib/embeddingColors";

export interface EmbeddingCloudSceneProps {
  points: EmbeddingPoint[];
  /** chunk_ids found by the current search. */
  hits: Set<string>;
  /** document_id -> colour index, decided by the parent (shared with the legend). */
  colorIndex: Map<string, number>;
  active: boolean;
  reduced: boolean;
  onHover: (point: EmbeddingPoint | null) => void;
}

function Points({ points, hits, colorIndex, onHover }: Omit<EmbeddingCloudSceneProps, "active" | "reduced">) {
  const mesh = useRef<InstancedMesh>(null);
  const scratch = useMemo(() => new Object3D(), []);
  const color = useMemo(() => new Color(), []);

  useLayoutEffect(() => {
    const m = mesh.current;
    if (!m) return;
    const anyHits = hits.size > 0;
    points.forEach((p, i) => {
      const hit = hits.has(p.chunk_id);
      // Spread [-1, 1] out to [-2, 2]: the camera sits at z = 6.
      scratch.position.set(p.x * 2, p.y * 2, p.z * 2);
      scratch.scale.setScalar(hit ? 2.2 : 1);
      scratch.updateMatrix();
      m.setMatrixAt(i, scratch.matrix);
      color.set(
        hit
          ? HIT_COLOR
          : anyHits
            ? DIM_COLOR
            : DOCUMENT_COLORS[(colorIndex.get(p.document_id) ?? 0) % DOCUMENT_COLORS.length]
      );
      m.setColorAt(i, color);
    });
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
    m.computeBoundingSphere();
  }, [points, hits, colorIndex, scratch, color]);

  const hover = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    onHover(event.instanceId === undefined ? null : (points[event.instanceId] ?? null));
  };

  return (
    <instancedMesh
      // Re-created when the count changes: an instanced mesh's size is fixed.
      key={points.length}
      ref={mesh}
      args={[undefined, undefined, points.length]}
      onPointerMove={hover}
      onPointerOut={() => onHover(null)}
    >
      <sphereGeometry args={[0.03, 10, 10]} />
      <meshStandardMaterial roughness={0.4} metalness={0.1} />
    </instancedMesh>
  );
}

export default function EmbeddingCloudScene({ active, reduced, ...rest }: EmbeddingCloudSceneProps) {
  return (
    <Canvas
      dpr={[1, 1.5]}
      frameloop={!active ? "never" : reduced ? "demand" : "always"}
      camera={{ position: [0, 0, 6], fov: 45 }}
      gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
      // The table view carries the same information for assistive tech.
      aria-hidden="true"
    >
      <ambientLight intensity={0.9} />
      <directionalLight position={[3, 4, 5]} intensity={1.2} />
      <Points {...rest} />
      <OrbitControls enablePan={false} autoRotate={!reduced} autoRotateSpeed={0.6} enableDamping={!reduced} />
    </Canvas>
  );
}
