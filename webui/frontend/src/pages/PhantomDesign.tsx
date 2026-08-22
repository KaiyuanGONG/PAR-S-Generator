import {
  useCallback,
  useEffect,
  lazy,
  Suspense,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { api, img, type LesionRecord, type PhantomProbe, type PreviewGeometry, type PreviewMesh, type PreviewSummary } from "../api";
import ErrorNotice from "../components/ErrorNotice";
import type { VoxelCursor } from "../components/PhantomSurface3D";
import { useI18n, type TranslationKey } from "../i18n";
import { Param } from "../ui";
import { toPhantomConfig, useWorkspace, type PhantomDraft } from "../workspace";

type Plane = "axial" | "coronal" | "sagittal";
type Layer = "activity" | "mu";
type Overlay = "liver_and_tumors" | "tumors" | "liver" | "contours" | "none";

const PhantomSurface3D = lazy(() => import("../components/PhantomSurface3D"));

const PLANES: Array<{ key: Plane; label: TranslationKey; axes: string }> = [
  { key: "axial", label: "phantom.plane.axial", axes: "A · L" },
  { key: "coronal", label: "phantom.plane.coronal", axes: "H · L" },
  { key: "sagittal", label: "phantom.plane.sagittal", axes: "H · A" },
];

const MORPHOLOGY_LABELS = {
  ellipsoid: "phantom.morphology.ellipsoid",
  spiculated: "phantom.morphology.spiculated",
} as const satisfies Record<string, TranslationKey>;

const PERFUSION_LABELS = {
  whole_liver: "phantom.perfusion.whole_liver",
  tumor_only: "phantom.perfusion.tumor_only",
  left_only: "phantom.perfusion.left_only",
  right_only: "phantom.perfusion.right_only",
} as const satisfies Record<string, TranslationKey>;

const LOBE_LABELS = {
  left: "phantom.lobe.left",
  right: "phantom.lobe.right",
} as const satisfies Record<string, TranslationKey>;

const DEFAULT_GEOMETRY: PreviewGeometry = {
  shape_zyx: [128, 128, 128],
  voxel_size_mm: 4.42,
  origin: "voxel-center",
};

function numeric(value: unknown, fallback: number) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clamp(value: number, maximum: number) {
  return Math.max(0, Math.min(maximum - 1, Math.round(value)));
}

function Measurement({ label, value, state }: { label: string; value: string; state?: "pass" | "fail" }) {
  const { t } = useI18n();
  const stateLabel = state === "pass" ? t("status.passed") : state === "fail" ? t("status.failed") : null;
  const showStateLabel = stateLabel !== null && value !== stateLabel;
  return (
    <div className="measurement-row" data-state={state}>
      <span>{label}</span>
      <strong>
        {value}
        {showStateLabel && <span className="measurement-state"> · {stateLabel}</span>}
      </strong>
    </div>
  );
}

function sliceIndex(plane: Plane, cursor: VoxelCursor) {
  return plane === "axial" ? cursor.z : plane === "coronal" ? cursor.y : cursor.x;
}

function planeMaximum(plane: Plane, geometry: PreviewGeometry) {
  const [depth, height, width] = geometry.shape_zyx;
  return plane === "axial" ? depth : plane === "coronal" ? height : width;
}

function planePosition(plane: Plane, cursor: VoxelCursor, geometry: PreviewGeometry) {
  const [depth, height, width] = geometry.shape_zyx;
  if (plane === "axial") return { horizontal: cursor.x / Math.max(width - 1, 1), vertical: cursor.y / Math.max(height - 1, 1) };
  if (plane === "coronal") return { horizontal: cursor.x / Math.max(width - 1, 1), vertical: cursor.z / Math.max(depth - 1, 1) };
  return { horizontal: cursor.y / Math.max(height - 1, 1), vertical: cursor.z / Math.max(depth - 1, 1) };
}

function cursorFromPlane(
  plane: Plane,
  horizontal: number,
  vertical: number,
  current: VoxelCursor,
  geometry: PreviewGeometry,
) {
  const [depth, height, width] = geometry.shape_zyx;
  if (plane === "axial") {
    return { ...current, x: clamp(horizontal * (width - 1), width), y: clamp(vertical * (height - 1), height) };
  }
  if (plane === "coronal") {
    return { ...current, x: clamp(horizontal * (width - 1), width), z: clamp(vertical * (depth - 1), depth) };
  }
  return { ...current, y: clamp(horizontal * (height - 1), height), z: clamp(vertical * (depth - 1), depth) };
}

interface ScanViewportProps {
  plane: Plane;
  previewId: string | null;
  cursor: VoxelCursor;
  geometry: PreviewGeometry;
  layer: Layer;
  overlay: Overlay;
  mip?: boolean;
  label: string;
  axes: string;
  emptyText: string;
  onCursorChange: (cursor: VoxelCursor) => void;
  onImageError: () => void;
}

function ScanViewport({
  plane,
  previewId,
  cursor,
  geometry,
  layer,
  overlay,
  mip = false,
  label,
  axes,
  emptyText,
  onCursorChange,
  onImageError,
}: ScanViewportProps) {
  const frame = useRef<number | null>(null);
  const queued = useRef<VoxelCursor | null>(null);
  const position = planePosition(plane, cursor, geometry);
  const cursorStyle = {
    "--cursor-x": `${position.horizontal * 100}%`,
    "--cursor-y": `${position.vertical * 100}%`,
  } as CSSProperties;

  function schedule(next: VoxelCursor) {
    queued.current = next;
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      if (queued.current) onCursorChange(queued.current);
    });
  }

  useEffect(() => () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
  }, []);

  function moveFromPointer(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const horizontal = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const vertical = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    schedule(cursorFromPlane(plane, horizontal, vertical, cursor, geometry));
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    moveFromPointer(event);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 5 : 1;
    const current = planePosition(plane, cursor, geometry);
    const [depth, height, width] = geometry.shape_zyx;
    const horizontalMaximum = plane === "sagittal" ? height : width;
    const verticalMaximum = plane === "axial" ? height : depth;
    let horizontal = Math.round(current.horizontal * (horizontalMaximum - 1));
    let vertical = Math.round(current.vertical * (verticalMaximum - 1));
    if (event.key === "ArrowLeft") horizontal -= step;
    else if (event.key === "ArrowRight") horizontal += step;
    else if (event.key === "ArrowUp") vertical -= step;
    else if (event.key === "ArrowDown") vertical += step;
    else return;
    event.preventDefault();
    schedule(
      cursorFromPlane(
        plane,
        clamp(horizontal, horizontalMaximum) / Math.max(horizontalMaximum - 1, 1),
        clamp(vertical, verticalMaximum) / Math.max(verticalMaximum - 1, 1),
        cursor,
        geometry,
      ),
    );
  }

  const source = previewId
    ? mip
      ? img.mip(previewId, plane, layer, overlay)
      : img.slice(previewId, plane, sliceIndex(plane, cursor), layer, overlay)
    : null;

  return (
    <div className="scan-viewport" data-empty={!previewId}>
      <div
        className="scan-image-frame"
        style={cursorStyle}
        role="application"
        tabIndex={previewId ? 0 : -1}
        aria-label={`${label}; x ${cursor.x}, y ${cursor.y}, z ${cursor.z}`}
        onPointerDown={previewId ? onPointerDown : undefined}
        onPointerMove={(event) => {
          if (previewId && event.currentTarget.hasPointerCapture(event.pointerId)) moveFromPointer(event);
        }}
        onKeyDown={onKeyDown}
      >
        {source ? (
          <img src={source} alt={`${label} · ${layer}`} draggable={false} onError={onImageError} />
        ) : (
          <div className="scan-empty">{emptyText}</div>
        )}
        {previewId && <><i className="cursor-line-x" /><i className="cursor-line-y" /><i className="cursor-point" /></>}
      </div>
      <span className="scan-label">{label}</span>
      <span className="scan-orientation">{axes}</span>
      {previewId && <span className="scan-scale"><i />50 mm</span>}
    </div>
  );
}

function lesionCenter(lesion: LesionRecord): VoxelCursor | null {
  const center = lesion.center_vox;
  if (!Array.isArray(center) || center.length !== 3 || !center.every((value) => typeof value === "number")) return null;
  return { z: center[0] as number, y: center[1] as number, x: center[2] as number };
}

export default function PhantomDesign() {
  const { state, dispatch } = useWorkspace();
  const { t } = useI18n();
  const phantom = state.draft.phantom;
  const locked = state.activeRun.locked;
  const [seed, setSeed] = useState(() => numeric(phantom.global_seed, 42));
  const [caseIndex, setCaseIndex] = useState(1);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [geometry, setGeometry] = useState<PreviewGeometry>(DEFAULT_GEOMETRY);
  const [summary, setSummary] = useState<PreviewSummary | null>(null);
  const [mesh, setMesh] = useState<PreviewMesh | null>(null);
  const [probe, setProbe] = useState<PhantomProbe | null>(null);
  const [layer, setLayer] = useState<Layer>("activity");
  const [overlay, setOverlay] = useState<Overlay>("liver_and_tumors");
  const [fourthView, setFourthView] = useState<"surface" | "mip">("surface");
  const [mipPlane, setMipPlane] = useState<Plane>("axial");
  const [surfaceFilter, setSurfaceFilter] = useState<"all" | "liver" | "tumors">("all");
  const [cursor, setCursor] = useState<VoxelCursor>({ x: 64, y: 64, z: 64 });
  const [busy, setBusy] = useState(false);
  const [meshBusy, setMeshBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [extendedRanges, setExtendedRanges] = useState(() => {
    const shape = Array.isArray(phantom.volume_shape) ? numeric(phantom.volume_shape[0], 128) : 128;
    return ![96, 128, 160].includes(shape)
      || numeric(phantom.voxel_size_mm, 4.42) < 3.54
      || numeric(phantom.voxel_size_mm, 4.42) > 5.89;
  });
  const autoPreviewStarted = useRef(false);
  const presetInput = useRef<HTMLInputElement | null>(null);

  const patchPhantom = (patch: Partial<PhantomDraft>) => dispatch({ type: "draft/phantom", patch });
  const updateCursor = useCallback((next: VoxelCursor) => {
    const [depth, height, width] = geometry.shape_zyx;
    setCursor({ x: clamp(next.x, width), y: clamp(next.y, height), z: clamp(next.z, depth) });
  }, [geometry.shape_zyx]);

  const generatePreview = useCallback(
    async (requestedSeed = seed, requestedCase = caseIndex) => {
      setBusy(true);
      setError(null);
      try {
        const response = await api.previewPhantom({
          phantom_config: toPhantomConfig(state.draft),
          case_index: requestedCase,
          seed: requestedSeed,
        });
        setPreviewId(response.preview_id);
        setSummary(response.summary);
        setGeometry(response.geometry);
        const [depth, height, width] = response.geometry.shape_zyx;
        setCursor({ x: Math.floor(width / 2), y: Math.floor(height / 2), z: Math.floor(depth / 2) });
        dispatch({ type: "plan/preview", configDigest: response.config_digest });
        setMeshBusy(true);
        api.previewMesh(response.preview_id)
          .then(setMesh)
          .catch(setError)
          .finally(() => setMeshBusy(false));
      } catch (caught: unknown) {
        setError(caught);
        dispatch({ type: "plan/preview", configDigest: null });
      } finally {
        setBusy(false);
      }
    },
    [caseIndex, dispatch, seed, state.draft],
  );

  useEffect(() => {
    if (autoPreviewStarted.current || Object.keys(phantom).length === 0) return;
    autoPreviewStarted.current = true;
    void generatePreview(seed, caseIndex);
  }, [caseIndex, generatePreview, phantom, seed]);

  useEffect(() => {
    if (!previewId) {
      setProbe(null);
      return;
    }
    let current = true;
    const handle = window.setTimeout(() => {
      api.previewProbe(previewId, cursor)
        .then((payload) => {
          if (current) setProbe(payload);
        })
        .catch((caught) => {
          if (current) setError(caught);
        });
    }, 80);
    return () => {
      current = false;
      window.clearTimeout(handle);
    };
  }, [cursor, previewId]);

  const leftRatio = numeric(phantom.target_left_ratio, 0.35);
  const scaleJitter = numeric(phantom.scale_jitter, 0.1) * 100;
  const rotationJitter = numeric(phantom.rot_jitter_deg, 5);
  const lesionMin = numeric(phantom.tumor_count_min, 1);
  const lesionMax = numeric(phantom.tumor_count_max, 5);
  const margin = numeric(phantom.tumor_min_liver_margin_mm, 4.42);
  const volumeShape = Array.isArray(phantom.volume_shape) ? numeric(phantom.volume_shape[0], 128) : 128;
  const voxelSize = summary?.voxel_size_mm ?? numeric(phantom.voxel_size_mm, 4.42);
  const volumePass = summary ? summary.liver_volume_ml >= 904 && summary.liver_volume_ml <= 1900 : undefined;
  const ratioPass = summary ? Math.abs(summary.left_ratio - leftRatio) <= 0.006 : undefined;
  const lesions = summary?.tumor_metadata ?? [];
  const sizeBins = Array.isArray(phantom.tumor_size_bins_mm) ? phantom.tumor_size_bins_mm : [];
  const probabilities = Array.isArray(phantom.tumor_probs) ? phantom.tumor_probs : [];
  const stale = Boolean(summary && state.plan.previewConfigDigest === null);

  function nextDraw() {
    const next = seed + 1;
    setSeed(next);
    void generatePreview(next, caseIndex);
  }

  function moveCase(delta: number) {
    const next = Math.max(1, caseIndex + delta);
    setCaseIndex(next);
    void generatePreview(seed, next);
  }

  function savePreset() {
    const blob = new Blob([JSON.stringify(toPhantomConfig(state.draft), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${state.draft.identity.runId || "phantom"}.phantom.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function loadPreset(file: File | undefined) {
    if (!file) return;
    try {
      const payload: unknown = JSON.parse(await file.text());
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error(t("phantom.presetInvalid"));
      const { n_cases: cases, output_dir: _output, ...settings } = payload as Record<string, unknown>;
      dispatch({ type: "draft/phantom", patch: settings as PhantomDraft });
      if (typeof cases === "number" && Number.isFinite(cases)) {
        dispatch({ type: "draft/identity", patch: { cases: Math.max(1, Math.floor(cases)) } });
      }
    } catch (caught) {
      setError(caught);
    } finally {
      if (presetInput.current) presetInput.current.value = "";
    }
  }

  return (
    <div className="phantom-workbench" aria-busy={busy}>
      <aside className="phantom-controls" aria-labelledby="cohort-controls-title">
        <header className="inspector-heading">
          <h2 id="cohort-controls-title">{t("phantom.cohort")}</h2>
          <p>GE 870 CZT · {geometry.shape_zyx.join("×")} · {voxelSize.toFixed(2)} mm</p>
        </header>

        {error !== null && <ErrorNotice error={error} />}
        {stale && <div className="banner warn" role="status">{t("phantom.previewStale")}</div>}

        <fieldset className="inspector-fieldset" disabled={locked}>
          <section className="instrument-section">
            <div className="instrument-section-head"><h3>{t("phantom.liverAnchors")}</h3><span>{t("phantom.perCase")}</span></div>
            <Param label={t("phantom.targetLeftRatio")} dist={t("phantom.cantliePlane")} value={Number(leftRatio.toFixed(3))} min={extendedRanges ? 0.15 : 0.25} max={extendedRanges ? 0.6 : 0.45} step={0.005} onChange={(value) => patchPhantom({ target_left_ratio: value })} />
            <Param label={t("phantom.scaleJitter")} dist="U[1−j, 1+j]" value={Math.round(scaleJitter)} min={0} max={extendedRanges ? 40 : 20} unit="%" onChange={(value) => patchPhantom({ scale_jitter: value / 100 })} />
            <Param label={t("phantom.rotationJitter")} dist="U[−r, +r]" value={rotationJitter} min={0} max={extendedRanges ? 30 : 15} unit="°" onChange={(value) => patchPhantom({ rot_jitter_deg: value })} />
          </section>

          <section className="instrument-section">
            <div className="instrument-section-head"><h3>{t("phantom.lesions")}</h3><span>{lesionMin}–{lesionMax}</span></div>
            <Param label={t("phantom.minimum")} value={lesionMin} min={0} max={5} onChange={(value) => patchPhantom({ tumor_count_min: value, tumor_count_max: Math.max(value, lesionMax) })} />
            <Param label={t("phantom.maximum")} value={lesionMax} min={0} max={extendedRanges ? 8 : 5} onChange={(value) => patchPhantom({ tumor_count_max: Math.max(lesionMin, value) })} />
            <Param label={t("phantom.margin")} dist={t("phantom.marginHelp")} value={Number(margin.toFixed(2))} min={0} max={12} step={0.01} unit="mm" onChange={(value) => patchPhantom({ tumor_min_liver_margin_mm: value })} />
            <p className="parameter-note mono">
              {t("phantom.sizeBins")} {sizeBins.map((bin) => Array.isArray(bin) ? `${bin[0]}–${bin[1]}` : String(bin)).join(" / ") || "—"} mm
              <br />p {probabilities.join(" / ") || "—"}
              <br />TNR U[{String(phantom.tumor_contrast_min ?? "—")}, {String(phantom.tumor_contrast_max ?? "—")}]
            </p>
          </section>

          <details className="expert-disclosure">
            <summary>{t("phantom.advanced")}</summary>
            <div className="expert-body">
              <label className="check-row"><input type="checkbox" checked={extendedRanges} onChange={(event) => setExtendedRanges(event.target.checked)} />{t("phantom.extendedRanges")}</label>
              <p className="parameter-note">{t("phantom.extendedRangesHelp")}</p>
              <label className="stacked-field">{t("phantom.matrixSize")}<select value={volumeShape} onChange={(event) => { const size = Number(event.target.value); patchPhantom({ volume_shape: [size, size, size] }); }}>{(extendedRanges ? [64, 96, 128, 192, 256] : [128]).map((size) => <option key={size} value={size}>{size} × {size} × {size}{size === 128 ? ` · ${t("phantom.matrixValidated")}` : ` · ${t("phantom.previewOnly")}`}</option>)}</select></label>
              <Param label={t("phantom.voxelSize")} value={numeric(phantom.voxel_size_mm, 4.42)} min={extendedRanges ? 2.5 : 3.54} max={extendedRanges ? 8 : 5.89} step={0.01} unit="mm" onChange={(value) => patchPhantom({ voxel_size_mm: value })} />
              <Param label={t("phantom.globalShift")} value={numeric(phantom.global_shift_range, 0.05)} min={0} max={extendedRanges ? 0.2 : 0.1} step={0.005} onChange={(value) => patchPhantom({ global_shift_range: value })} />
              <Param label={t("phantom.smoothing")} value={numeric(phantom.smooth_sigma, 1.2)} min={extendedRanges ? 0 : 0.8} max={extendedRanges ? 4 : 2} step={0.1} unit="px" onChange={(value) => patchPhantom({ smooth_sigma: value })} />
              <Param label={t("phantom.tnrMinimum")} value={numeric(phantom.tumor_contrast_min, 2)} min={extendedRanges ? 1 : 2} max={extendedRanges ? 12 : 8} step={0.1} onChange={(value) => patchPhantom({ tumor_contrast_min: value, tumor_contrast_max: Math.max(value, numeric(phantom.tumor_contrast_max, 8)) })} />
              <Param label={t("phantom.tnrMaximum")} value={numeric(phantom.tumor_contrast_max, 8)} min={extendedRanges ? 1 : 2} max={extendedRanges ? 12 : 8} step={0.1} onChange={(value) => patchPhantom({ tumor_contrast_max: Math.max(numeric(phantom.tumor_contrast_min, 2), value) })} />
              <Param label={t("phantom.totalCounts")} value={numeric(phantom.total_counts, 80_000)} min={extendedRanges ? 10_000 : 50_000} max={extendedRanges ? 500_000 : 200_000} step={10_000} onChange={(value) => patchPhantom({ total_counts: value })} />
              <Param label={t("phantom.residualBackground")} value={numeric(phantom.residual_bg, 0.05)} min={0} max={extendedRanges ? 0.5 : 0.15} step={0.01} onChange={(value) => patchPhantom({ residual_bg: value })} />
              <Param label={t("phantom.overlapGap")} value={numeric(phantom.tumor_overlap_gap_mm, 0)} min={0} max={extendedRanges ? 20 : 8} step={0.5} unit="mm" onChange={(value) => patchPhantom({ tumor_overlap_gap_mm: value })} />
              <Param label={t("phantom.subcapsularFraction")} value={numeric(phantom.subcapsular_fraction, 0)} min={0} max={1} step={0.05} onChange={(value) => patchPhantom({ subcapsular_fraction: value })} />
              <Param label={t("phantom.subcapsularDepth")} value={numeric(phantom.subcapsular_max_depth_mm, 5)} min={1} max={extendedRanges ? 20 : 10} step={0.5} unit="mm" onChange={(value) => patchPhantom({ subcapsular_max_depth_mm: value })} />
              <label className="check-row"><input type="checkbox" checked={phantom.allow_capacity_subcapsular_fallback !== false} onChange={(event) => patchPhantom({ allow_capacity_subcapsular_fallback: event.target.checked })} />{t("phantom.capacityFallback")}</label>
              <label className="stacked-field">{t("phantom.tumorMorphology")}
                <select value={String(phantom.tumor_mode_policy ?? "random")} onChange={(event) => patchPhantom({ tumor_mode_policy: event.target.value })}>
                  <option value="random">{t("common.random")}</option><option value="ellipsoid">{t(MORPHOLOGY_LABELS.ellipsoid)}</option><option value="spiculated">{t(MORPHOLOGY_LABELS.spiculated)}</option>
                </select>
              </label>
              <label className="stacked-field">{t("phantom.perfusionPolicy")}
                <select value={String(phantom.perfusion_mode_policy ?? "random")} onChange={(event) => patchPhantom({ perfusion_mode_policy: event.target.value })}>
                  <option value="random">{t("common.random")}</option><option value="whole_liver">{t(PERFUSION_LABELS.whole_liver)}</option><option value="tumor_only">{t(PERFUSION_LABELS.tumor_only)}</option><option value="left_only">{t(PERFUSION_LABELS.left_only)}</option><option value="right_only">{t(PERFUSION_LABELS.right_only)}</option>
                </select>
              </label>
              <label className="check-row"><input type="checkbox" checked={phantom.use_global_seed !== false} onChange={(event) => patchPhantom({ use_global_seed: event.target.checked })} />{t("phantom.useGlobalSeed")}</label>
              <label className="stacked-field">{t("phantom.globalSeed")}<input type="number" className="mono" value={numeric(phantom.global_seed, 42)} onChange={(event) => patchPhantom({ global_seed: Number(event.target.value) })} /></label>
            </div>
          </details>
        </fieldset>

        <section className="instrument-section preview-draw-section">
          <div className="instrument-section-head"><h3>{t("phantom.preview")}</h3><span>{locked ? t("status.ready") : t("status.draft")}</span></div>
          <div className="preview-controls">
            <label htmlFor="preview-seed">{t("common.seed")}<input id="preview-seed" type="number" className="mono" value={seed} onChange={(event) => setSeed(Number(event.target.value))} /></label>
            <label htmlFor="preview-case-index">{t("common.case")}<input id="preview-case-index" type="number" className="mono" min={1} value={caseIndex} onChange={(event) => setCaseIndex(Math.max(1, Number(event.target.value)))} /></label>
          </div>
          <div className="compact-actions">
            <button type="button" onClick={() => moveCase(-1)} disabled={busy || caseIndex <= 1}>{t("action.previousCase")}</button>
            <button type="button" onClick={() => moveCase(1)} disabled={busy}>{t("action.nextCase")}</button>
          </div>
          <div className="compact-actions">
            <button type="button" onClick={savePreset}>{t("action.savePreset")}</button>
            <button type="button" onClick={() => presetInput.current?.click()} disabled={locked}>{t("action.loadPreset")}</button>
            <input ref={presetInput} className="sr-only" type="file" accept="application/json,.json" aria-label={t("action.loadPreset")} onChange={(event) => void loadPreset(event.target.files?.[0])} />
          </div>
        </section>
      </aside>

      <section className="imaging-console" aria-labelledby="orthogonal-view-title">
        <div className="imaging-toolbar">
          <span className="sr-only" id="orthogonal-view-title">{t("phantom.viewer")}</span>
          <div className="toolbar-group" role="group" aria-label={t("phantom.channel")}>
            <button type="button" aria-pressed={layer === "activity"} onClick={() => setLayer("activity")}>{t("phantom.layer.activity")}</button>
            <button type="button" aria-pressed={layer === "mu"} onClick={() => setLayer("mu")}>{t("phantom.layer.mu")}</button>
          </div>
          <label className="toolbar-select">{t("phantom.overlay")}
            <select value={overlay} onChange={(event) => setOverlay(event.target.value as Overlay)}>
              <option value="liver_and_tumors">{t("phantom.overlay.all")}</option><option value="tumors">{t("phantom.overlay.tumors")}</option><option value="liver">{t("phantom.overlay.liver")}</option><option value="contours">{t("phantom.overlay.contours")}</option><option value="none">{t("phantom.overlay.none")}</option>
            </select>
          </label>
          <span className="imaging-context mono">{summary ? t("phantom.preview.case", { caseId: String(summary.case_id).padStart(4, "0"), seed: summary.seed }) : t("phantom.noPreview")}</span>
        </div>

        <div className="slice-grid">
          {PLANES.map((plane) => (
            <div className="slice-cell" key={plane.key}>
              <ScanViewport plane={plane.key} previewId={previewId} cursor={cursor} geometry={geometry} layer={layer} overlay={overlay} label={t(plane.label)} axes={plane.axes} emptyText={t("phantom.noPreviewHelp")} onCursorChange={updateCursor} onImageError={() => setError(new Error(t("phantom.imageUnavailable")))} />
              <label className="slice-slider"><span className="sr-only">{t(plane.label)}</span><input type="range" min={0} max={planeMaximum(plane.key, geometry) - 1} value={sliceIndex(plane.key, cursor)} onChange={(event) => {
                const value = Number(event.target.value);
                updateCursor(plane.key === "axial" ? { ...cursor, z: value } : plane.key === "coronal" ? { ...cursor, y: value } : { ...cursor, x: value });
              }} /><output>{sliceIndex(plane.key, cursor)}/{planeMaximum(plane.key, geometry) - 1}</output></label>
            </div>
          ))}

          <div className="slice-cell fourth-view">
            <div className="fourth-toolbar">
              <div role="group" aria-label={t("phantom.fourthView")}><button type="button" aria-pressed={fourthView === "surface"} onClick={() => setFourthView("surface")}>3D</button><button type="button" aria-pressed={fourthView === "mip"} onClick={() => setFourthView("mip")}>MIP</button></div>
              {fourthView === "surface" ? (
                <select aria-label={t("phantom.surfaceFilter")} value={surfaceFilter} onChange={(event) => setSurfaceFilter(event.target.value as typeof surfaceFilter)}><option value="all">{t("phantom.overlay.all")}</option><option value="liver">{t("phantom.overlay.liver")}</option><option value="tumors">{t("phantom.overlay.tumors")}</option></select>
              ) : (
                <select aria-label={t("phantom.mipPlane")} value={mipPlane} onChange={(event) => setMipPlane(event.target.value as Plane)}>{PLANES.map((plane) => <option key={plane.key} value={plane.key}>{t(plane.label)}</option>)}</select>
              )}
            </div>
            {fourthView === "surface" ? (
              <Suspense fallback={<div className="scan-empty">{t("phantom.surfaceLoading")}</div>}><PhantomSurface3D mesh={mesh} cursor={cursor} filter={surfaceFilter} onCursorChange={updateCursor} resetLabel={t("action.resetView")} ariaLabel={meshBusy ? t("phantom.surfaceLoading") : t("phantom.surfaceView")} /></Suspense>
            ) : (
              <ScanViewport plane={mipPlane} previewId={previewId} cursor={cursor} geometry={geometry} layer={layer} overlay={overlay} mip label={`MIP · ${t(PLANES.find((item) => item.key === mipPlane)!.label)}`} axes="MAX" emptyText={t("phantom.noPreviewHelp")} onCursorChange={updateCursor} onImageError={() => setError(new Error(t("phantom.imageUnavailable")))} />
            )}
            <div className="slice-slider"><span className="mono muted">{t("phantom.linkedCursor")}</span></div>
          </div>
        </div>

        <div className="imaging-probe" aria-live="polite">
          <span>VOXEL x {cursor.x} · y {cursor.y} · z {cursor.z}</span>
          <span>{probe ? `${probe.position_mm.x.toFixed(1)} · ${probe.position_mm.y.toFixed(1)} · ${probe.position_mm.z.toFixed(1)} mm` : `${voxelSize.toFixed(2)} mm iso`}</span>
          <span>ACT {probe ? probe.activity.toExponential(3) : "—"}</span><span>μ {probe ? `${probe.mu.toFixed(4)} ${summary?.mu_unit ?? "cm⁻¹"}` : "—"}</span>
          <span>{probe?.lesion_ids.length ? `L${probe.lesion_ids.join(", L")}` : probe?.in_liver ? t("phantom.inLiver") : t("phantom.outsideLiver")}</span>
        </div>
      </section>

      <aside className="phantom-measures" aria-labelledby="measurements-title">
        <header className="inspector-heading"><h2 id="measurements-title">{t("phantom.measurements")}</h2><p>{t("phantom.derivedMasks")}</p></header>
        <section className="measurement-list">
          <Measurement label={t("phantom.liverVolume")} value={summary ? `${summary.liver_volume_ml.toFixed(1)} mL` : "—"} state={volumePass == null ? undefined : volumePass ? "pass" : "fail"} />
          <Measurement label={t("phantom.leftRatio")} value={summary ? summary.left_ratio.toFixed(3) : "—"} state={ratioPass == null ? undefined : ratioPass ? "pass" : "fail"} />
          <Measurement label={t("phantom.cantlieSolver")} value={summary ? (summary.cantlie_converged ? t("status.passed") : t("status.failed")) : "—"} state={summary ? (summary.cantlie_converged ? "pass" : "fail") : undefined} />
          <Measurement label={t("phantom.perfusion")} value={summary?.perfusion_mode && summary.perfusion_mode in PERFUSION_LABELS ? t(PERFUSION_LABELS[summary.perfusion_mode as keyof typeof PERFUSION_LABELS]) : summary?.perfusion_mode ?? "—"} />
          <Measurement label={t("phantom.totalCounts")} value={summary ? summary.total_counts_actual.toExponential(3) : "—"} />
          <Measurement label={t("phantom.generationTime")} value={summary ? `${summary.generation_time_s.toFixed(2)} s` : "—"} />
        </section>

        <section aria-labelledby="lesion-ledger-title">
          <div className="section-title"><h2 id="lesion-ledger-title">{t("phantom.lesions.count", { count: lesions.length })}</h2><small>{t("common.measured")}</small></div>
          {lesions.length ? (
            <div className="lesion-ledger" role="region" aria-label={t("phantom.measuredLesions")} tabIndex={0}>
              <table><thead><tr><th scope="col">#</th><th scope="col">{t("phantom.mode")}</th><th scope="col">{t("phantom.lobe")}</th><th scope="col" className="num">{t("phantom.diameterMm")}</th><th scope="col" className="num">{t("phantom.tnr")}</th><th scope="col" className="num">{t("phantom.margin")}</th></tr></thead>
                <tbody>{lesions.map((lesion, index) => {
                  const center = lesionCenter(lesion);
                  const mode = lesion.mode && lesion.mode in MORPHOLOGY_LABELS ? t(MORPHOLOGY_LABELS[lesion.mode as keyof typeof MORPHOLOGY_LABELS]) : lesion.mode ?? "—";
                  const lobe = lesion.lobe && lesion.lobe in LOBE_LABELS ? t(LOBE_LABELS[lesion.lobe as keyof typeof LOBE_LABELS]) : lesion.lobe ?? "—";
                  return <tr key={lesion.id ?? index} data-selected={probe?.lesion_ids.includes(index + 1)}><th scope="row" className="mono"><button type="button" className="lesion-jump" disabled={!center} onClick={() => center && updateCursor(center)} aria-label={t("phantom.focusLesion", { count: index + 1 })}>{index + 1}</button></th><td>{mode}</td><td>{lobe}</td><td className="num">{lesion.effective_diameter_mm == null ? "—" : lesion.effective_diameter_mm.toFixed(1)}</td><td className="num">{lesion.tnr_local == null ? lesion.target_contrast?.toFixed(2) ?? "—" : lesion.tnr_local.toFixed(2)}</td><td className="num">{lesion.surface_margin_mm == null ? "—" : lesion.surface_margin_mm.toFixed(1)}</td></tr>;
                })}</tbody>
              </table>
            </div>
          ) : <p className="qc-note">{summary ? t("phantom.noLesions") : t("phantom.noPreviewHelp")}</p>}
        </section>

        <section aria-labelledby="quality-envelope-title">
          <div className="section-title"><h2 id="quality-envelope-title">{t("phantom.quality")}</h2><small>{t("phantom.cohortEnvelope")}</small></div>
          <div className="measurement-list"><Measurement label={t("phantom.liverVolume")} value="904–1900 mL" state={volumePass == null ? undefined : volumePass ? "pass" : "fail"} /><Measurement label={t("phantom.leftRatio")} value="target ±0.006" state={ratioPass == null ? undefined : ratioPass ? "pass" : "fail"} /><Measurement label={t("phantom.containment")} value="0 outside · 0 overlap" /></div>
          <p className="qc-note">{t("phantom.gatesNote")}</p>
        </section>
      </aside>

      <footer className="page-command-shelf phantom-command-shelf">
        <span className="command-signal" data-tone={error ? "danger" : summary && !stale ? "success" : "warning"} />
        <div className="command-copy"><strong>{summary ? t("phantom.preview.case", { caseId: summary.case_id, seed: summary.seed }) : t("phantom.noPreview")}</strong><span>{locked ? t("phantom.lockedConfig") : stale ? t("phantom.previewStale") : t("phantom.previewTransient")}</span></div>
        <span className="command-meta mono">{busy ? t("common.loading") : `${t("common.casesShort", { count: state.draft.identity.cases })} · ${voxelSize.toFixed(2)} mm`}</span>
        <div className="command-actions"><button type="button" onClick={() => void generatePreview()} disabled={busy}>{t("action.regenerate")}</button><button type="button" className="primary" onClick={nextDraw} disabled={busy}>{t("action.nextDraw")}</button></div>
      </footer>
    </div>
  );
}
