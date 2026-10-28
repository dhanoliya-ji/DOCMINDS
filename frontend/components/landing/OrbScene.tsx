// A distorted, iridescent sphere that leans toward the cursor.

"use client";

import { MeshDistortMaterial, Sphere } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useRef } from "react";
import type { Mesh } from "three";

interface OrbSceneProps {
  active: boolean;
}

function Orb() {
  const mesh = useRef<Mesh>(null);

  useFrame((state, delta) => {
    const m = mesh.current;
    if (!m) return;
    const k = Math.min(1, delta * 3);
    m.rotation.y += (state.pointer.x * 0.6 - m.rotation.y) * k;
    m.rotation.x += (-state.pointer.y * 0.4 - m.rotation.x) * k;
    m.position.x += (state.pointer.x * 0.25 - m.position.x) * k;
    m.position.y += (state.pointer.y * 0.15 - m.position.y) * k;
  });

  return (
    <Sphere ref={mesh} args={[1.2, 96, 96]}>
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
