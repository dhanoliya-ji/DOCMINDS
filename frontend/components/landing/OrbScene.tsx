/**
 * OrbScene.tsx  (F2: the 3D "AI orb" itself)
 * ==========================================
 * A distorted, iridescent sphere that leans toward the cursor.
 *
 * This file is the ONLY one in the hero that imports three.js. It is never
 * imported directly -- OrbHero loads it through next/dynamic with ssr:false,
 * so the ~600KB of 3D code is a separate chunk fetched after first paint, and
 * never at all for visitors who prefer reduced motion.
 *
 * PERFORMANCE GUARDS (F19)
 * ------------------------
 * - dpr capped at 1.5: a 3x phone would otherwise shade 9x the pixels.
 * - frameloop switches to "never" when `active` is false (off screen or tab
 *   hidden), so the GPU is idle while the visitor reads further down.
 */

"use client";

import { MeshDistortMaterial, Sphere } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useRef } from "react";
import type { Mesh } from "three";

interface OrbSceneProps {
  /** Render only while visible; false freezes the frame loop. */
  active: boolean;
}

function Orb() {
  const mesh = useRef<Mesh>(null);

  useFrame((state, delta) => {
    const m = mesh.current;
    if (!m) return;
    // `state.pointer` is the cursor in -1..1 coordinates. Easing toward it
    // (rather than snapping) gives the orb its weighty, liquid feel.
    const k = Math.min(1, delta * 3);
    m.rotation.y += (state.pointer.x * 0.6 - m.rotation.y) * k;
    m.rotation.x += (-state.pointer.y * 0.4 - m.rotation.x) * k;
    m.position.x += (state.pointer.x * 0.25 - m.position.x) * k;
    m.position.y += (state.pointer.y * 0.15 - m.position.y) * k;
  });

  return (
    <Sphere ref={mesh} args={[1.2, 96, 96]}>
      {/* Distortion animates the vertices; low roughness plus high
          metalness lets the coloured lights read as an iridescent sheen. */}
      <MeshDistortMaterial
        color="#6366f1"
        distort={0.38}
        speed={1.6}
        roughness={0.12}
        metalness={0.85}
      />
    </Sphere>
  );
}

export default function OrbScene({ active }: OrbSceneProps) {
  return (
    <Canvas
      dpr={[1, 1.5]}
      frameloop={active ? "always" : "never"}
      camera={{ position: [0, 0, 4], fov: 45 }}
      gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
      // Decorative: the hero text carries all meaning.
      aria-hidden="true"
    >
      <ambientLight intensity={0.4} />
      <pointLight position={[3, 2, 3]} intensity={30} color="#22d3ee" />
      <pointLight position={[-3, -1, 2]} intensity={30} color="#a855f7" />
      <pointLight position={[0, 3, -2]} intensity={20} color="#f0abfc" />
      <Orb />
    </Canvas>
  );
}
