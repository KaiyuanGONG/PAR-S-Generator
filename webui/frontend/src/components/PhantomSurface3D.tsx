import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { PreviewMesh } from "../api";

export interface VoxelCursor {
  x: number;
  y: number;
  z: number;
}

interface PhantomSurface3DProps {
  mesh: PreviewMesh | null;
  cursor: VoxelCursor;
  filter: "all" | "liver" | "tumors";
  onCursorChange: (cursor: VoxelCursor) => void;
  resetLabel: string;
  ariaLabel: string;
}

function clamp(value: number, max: number) {
  return Math.max(0, Math.min(max - 1, Math.round(value)));
}

export default function PhantomSurface3D({
  mesh,
  cursor,
  filter,
  onCursorChange,
  resetLabel,
  ariaLabel,
}: PhantomSurface3DProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cursorRef = useRef<THREE.Object3D | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !mesh) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x080d11);
    const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 2000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.replaceChildren(renderer.domElement);

    const [depth, height, width] = mesh.shape_zyx;
    const center = new THREE.Vector3((width - 1) / 2, (height - 1) / 2, (depth - 1) / 2);
    const span = Math.max(width, height, depth);
    const resetCamera = () => {
      camera.position.set(center.x + span * 1.35, center.y - span * 1.55, center.z + span * 1.1);
      camera.up.set(0, 0, 1);
      camera.lookAt(center);
      controls.target.copy(center);
      controls.update();
    };

    scene.add(new THREE.HemisphereLight(0xd8edf7, 0x17232b, 2.2));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.5);
    keyLight.position.set(width, -height, depth * 1.5);
    scene.add(keyLight);

    const group = new THREE.Group();
    group.name = "phantom-surfaces";
    for (const object of mesh.objects) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute(object.vertices, 3));
      geometry.setIndex(object.faces);
      geometry.computeVertexNormals();
      const material =
        object.kind === "liver"
          ? new THREE.MeshPhongMaterial({
              color: 0x48bda0,
              transparent: true,
              opacity: 0.28,
              depthWrite: false,
              side: THREE.DoubleSide,
            })
          : new THREE.MeshPhongMaterial({ color: 0xef635a, shininess: 35, side: THREE.DoubleSide });
      const surface = new THREE.Mesh(geometry, material);
      surface.name = object.id;
      surface.userData.kind = object.kind;
      group.add(surface);
    }
    scene.add(group);

    const cursorGroup = new THREE.Group();
    cursorGroup.name = "linked-cursor";
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(1.8, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0xffcf5b, depthTest: false }),
    );
    marker.renderOrder = 10;
    cursorGroup.add(marker);
    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(
        [-7, 0, 0, 7, 0, 0, 0, -7, 0, 0, 7, 0, 0, 0, -7, 0, 0, 7],
        3,
      ),
    );
    const lines = new THREE.LineSegments(
      lineGeometry,
      new THREE.LineBasicMaterial({ color: 0xffcf5b, depthTest: false }),
    );
    lines.renderOrder = 10;
    cursorGroup.add(lines);
    cursorGroup.position.set(cursor.x, cursor.y, cursor.z);
    scene.add(cursorGroup);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.minDistance = span * 0.45;
    controls.maxDistance = span * 4;
    resetCamera();

    const render = () => renderer.render(scene, camera);
    controls.addEventListener("change", render);
    const resize = () => {
      const rect = host.getBoundingClientRect();
      const widthPx = Math.max(1, Math.round(rect.width));
      const heightPx = Math.max(1, Math.round(rect.height));
      renderer.setSize(widthPx, heightPx, false);
      camera.aspect = widthPx / heightPx;
      camera.updateProjectionMatrix();
      render();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let pointerDown: { x: number; y: number } | null = null;
    const onPointerDown = (event: PointerEvent) => {
      pointerDown = { x: event.clientX, y: event.clientY };
    };
    const onPointerUp = (event: PointerEvent) => {
      if (!pointerDown || Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y) > 4) return;
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const intersections = raycaster.intersectObjects(group.children, false);
      const hit = intersections[0];
      if (hit) {
        onCursorChange({
          x: clamp(hit.point.x, width),
          y: clamp(hit.point.y, height),
          z: clamp(hit.point.z, depth),
        });
      }
    };
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointerup", onPointerUp);

    cameraRef.current = camera;
    controlsRef.current = controls;
    rendererRef.current = renderer;
    sceneRef.current = scene;
    cursorRef.current = cursorGroup;
    host.dataset.ready = "true";
    host.dataset.reset = "available";
    (host as HTMLDivElement & { resetCamera?: () => void }).resetCamera = () => {
      resetCamera();
      render();
    };
    render();

    return () => {
      observer.disconnect();
      controls.removeEventListener("change", render);
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      group.traverse((node) => {
        if (node instanceof THREE.Mesh) {
          node.geometry.dispose();
          if (Array.isArray(node.material)) node.material.forEach((material) => material.dispose());
          else node.material.dispose();
        }
      });
      marker.geometry.dispose();
      (marker.material as THREE.Material).dispose();
      lineGeometry.dispose();
      (lines.material as THREE.Material).dispose();
      renderer.dispose();
      host.replaceChildren();
      delete (host as HTMLDivElement & { resetCamera?: () => void }).resetCamera;
      cameraRef.current = null;
      controlsRef.current = null;
      rendererRef.current = null;
      sceneRef.current = null;
      cursorRef.current = null;
    };
  }, [mesh, onCursorChange]);

  useEffect(() => {
    const scene = sceneRef.current;
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    if (!scene || !renderer || !camera) return;
    const group = scene.getObjectByName("phantom-surfaces");
    group?.children.forEach((object) => {
      object.visible = filter === "all" || object.userData.kind === (filter === "liver" ? "liver" : "tumor");
    });
    renderer.render(scene, camera);
  }, [filter]);

  useEffect(() => {
    const marker = cursorRef.current;
    const scene = sceneRef.current;
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    if (!marker || !scene || !renderer || !camera) return;
    marker.position.set(cursor.x, cursor.y, cursor.z);
    renderer.render(scene, camera);
  }, [cursor]);

  function moveCursor(event: React.KeyboardEvent<HTMLDivElement>) {
    if (!mesh) return;
    const step = event.shiftKey ? 5 : 1;
    const next = { ...cursor };
    if (event.key === "ArrowLeft") next.x -= step;
    else if (event.key === "ArrowRight") next.x += step;
    else if (event.key === "ArrowUp") next.y -= step;
    else if (event.key === "ArrowDown") next.y += step;
    else if (event.key === "PageUp") next.z += step;
    else if (event.key === "PageDown") next.z -= step;
    else return;
    event.preventDefault();
    const [depth, height, width] = mesh.shape_zyx;
    onCursorChange({
      x: clamp(next.x, width),
      y: clamp(next.y, height),
      z: clamp(next.z, depth),
    });
  }

  return (
    <div className="surface-stage">
      <div
        ref={hostRef}
        className="surface-canvas"
        role="application"
        tabIndex={0}
        aria-label={`${ariaLabel}; x ${cursor.x}, y ${cursor.y}, z ${cursor.z}`}
        onKeyDown={moveCursor}
      />
      {!mesh && <div className="scan-empty">{ariaLabel}</div>}
      <button
        type="button"
        className="surface-reset"
        disabled={!mesh}
        onClick={() => (hostRef.current as HTMLDivElement & { resetCamera?: () => void })?.resetCamera?.()}
      >
        {resetLabel}
      </button>
    </div>
  );
}
