// The WebGL half of the embedding cloud

"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { useLayoutEffect, useMemo, useRef } from "react";
import { Color, type InstancedMesh, Object3D } from "three";

import type { EmbeddingPoint } from "@/lib/api";
import { DIM_COLOR, DOCUMENT_COLORS, HIT_COLOR } from "@/lib/embeddingColors";

export interface EmbeddingCloudSceneProps {
  points: EmbeddingPoint[];
  hits: Set<string>;
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
      aria-hidden="true"
    >
      <ambientLight intensity={0.9} />
      <directionalLight position={[3, 4, 5]} intensity={1.2} />
      <Points {...rest} />
      <OrbitControls enablePan={false} autoRotate={!reduced} autoRotateSpeed={0.6} enableDamping={!reduced} />
    </Canvas>
  );
}
