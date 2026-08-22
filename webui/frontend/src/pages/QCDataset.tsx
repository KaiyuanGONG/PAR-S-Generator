import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { ApiError, api, img, type ArtifactSummary, type CaseEvidence, type CaseRecord, type Protocol, type RunSummary, type StageRecord } from "../api";
import ErrorNotice from "../components/ErrorNotice";
import FileBrowser from "../components/FileBrowser";
import { stageTranslationKey, translateStatus, useI18n, type PipelineStage, type TranslationKey } from "../i18n";
import { useWorkspace } from "../workspace";

function valueAt(record: unknown, path: string[]): unknown {
  let current = record;
  for (const key of path) {
    if (!current || typeof current !== "object" || Array.isArray(current)) return undefined;
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

function numeric(record: unknown, paths: string[][]): number | null {
  for (const path of paths) {
    const value = valueAt(record, path);
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

const EFFECTIVE_LABELS = {
  projection_shape: "protocol.projectionMatrix",
  nn_multiplier: "simulation.historyMultiplier",
  detector_matrix: "protocol.detectorMatrix",
  voxel_size_mm: "phantom.voxelSize",
  source_activity_mbq: "protocol.activity",
  exposure_time_s_per_projection: "protocol.exposure",
  smc_index25_activity_time: "review.effective.smcIndex25",
  type7_density_threshold_times_1000: "review.effective.type7Threshold",
  phantom_cross_sections: "review.effective.crossSections",
} as const satisfies Record<string, TranslationKey>;

const ARTIFACT_LABELS = {
  path: "common.path",
  shape: "review.artifact.shape",
  dtype: "review.artifact.dtype",
  canonical_transform: "review.artifact.transform",
  sum: "review.artifact.sum",
  minimum: "review.artifact.minimum",
  maximum: "review.artifact.maximum",
  nonzero_fraction: "review.artifact.nonzero",
} as const satisfies Record<string, TranslationKey>;

export default function QCDataset({
  protocol,
  runRoot,
  setRunRoot,
}: {
  protocol: Protocol | null;
  runRoot: string | null;
  setRunRoot: (root: string) => void;
}) {
  const { state, dispatch } = useWorkspace();
  const { t } = useI18n();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [stages, setStages] = useState<StageRecord[]>([]);
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [manifest, setManifest] = useState<Record<string, any> | null>(null);
  const [splits, setSplits] = useState<Record<string, any> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<CaseEvidence | null>(null);
  const [layer, setLayer] = useState<"expectation" | "observation">("expectation");
  const [view, setView] = useState(0);
  const [row, setRow] = useState(64);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [artifact, setArtifact] = useState<ArtifactSummary | null>(null);
  const requestGeneration = useRef(0);

  async function refresh(root = runRoot) {
    const generation = ++requestGeneration.current;
    setBusy(true);
    setError(null);
    try {
      const runResponse = await api.runs(state.draft.identity.runsRoot);
      if (generation !== requestGeneration.current) return;
      setRuns(runResponse.runs);
      const target = root ?? runResponse.runs[0]?.root ?? null;
      if (!target) {
        setStages([]);
        setCases([]);
        return;
      }
      if (target !== runRoot) setRunRoot(target);
      const [stageResponse, caseResponse, manifestResponse, splitResponse] = await Promise.all([
        api.stages(target),
        api.cases(target),
        api.manifest(target).catch((caught) => caught instanceof ApiError && caught.status === 404 ? null : Promise.reject(caught)),
        api.splits(target).catch((caught) => caught instanceof ApiError && caught.status === 404 ? null : Promise.reject(caught)),
      ]);
      if (generation !== requestGeneration.current) return;
      setStages(stageResponse.stages);
      setCases(caseResponse.cases);
      setManifest(manifestResponse);
      setSplits(splitResponse);
      setSelected((current) => current && caseResponse.cases.some((item) => item.case_id === current) ? current : caseResponse.cases[0]?.case_id ?? null);
    } catch (caught) {
      if (generation === requestGeneration.current) setError(caught);
    } finally {
      if (generation === requestGeneration.current) setBusy(false);
    }
  }

  useEffect(() => {
    void refresh(runRoot);
    // runRoot intentionally selects a new authoritative run; other draft edits do not refetch evidence.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runRoot]);

  useEffect(() => {
    if (!runRoot || !selected || artifact) {
      setEvidence(null);
      return;
    }
    let live = true;
    api.caseEvidence(runRoot, selected)
      .then((payload) => { if (live) setEvidence(payload); })
      .catch((caught) => { if (live) setError(caught); });
    return () => { live = false; };
  }, [artifact, runRoot, selected]);

  const selectedCase = cases.find((item) => item.case_id === selected) ?? null;
  const shape = artifact?.shape ?? evidence?.effective.projection_shape ?? [60, 128, 128];
  const maxView = Math.max(0, shape[0] - 1);
  const maxRow = Math.max(0, shape[1] - 1);
  const projectionSource = artifact
    ? img.artifactProjection(artifact.path, Math.min(view, maxView))
    : runRoot && selected ? img.projection(runRoot, selected, Math.min(view, maxView), layer) : null;
  const sinogramSource = artifact
    ? img.artifactSinogram(artifact.path, Math.min(row, maxRow))
    : runRoot && selected ? img.sinogram(runRoot, selected, Math.min(row, maxRow), layer) : null;

  function selectRun(root: string) {
    setArtifact(null);
    setSelected(null);
    setRunRoot(root);
  }

  async function inspectArtifact(path: string) {
    setError(null);
    try {
      const summary = await api.inspectArtifact(path);
      setArtifact(summary);
      setView(0);
      setRow(Math.floor(summary.shape[1] / 2));
    } catch (caught) {
      setError(caught);
    }
  }

  function projectionPointer(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const next = Math.max(0, Math.min(maxRow, Math.round(((event.clientY - rect.top) / rect.height) * maxRow)));
    setRow(next);
  }

  function exportCsv() {
    const header = ["case_id", "split", "seed", "backend", "projection_qc", "observation_qc", "total_counts", "angular_cv"];
    const lines = cases.map((record) => [
      record.case_id,
      record.split ?? "",
      record.seed ?? "",
      String(valueAt(record, ["expectation", "backend"]) ?? ""),
      String(valueAt(record, ["qc", "projection", "status"]) ?? ""),
      String(valueAt(record, ["qc", "observation", "status"]) ?? ""),
      numeric(record, [["observation", "sum"], ["observation", "observed_total_counts"]]) ?? "",
      numeric(record, [["observation", "angular_cv"]]) ?? "",
    ].map((value) => `"${String(value).replaceAll('"', '""')}"`).join(","));
    download(`${state.activeRun.runId ?? "run"}-cases.csv`, [header.join(","), ...lines].join("\n"), "text/csv");
  }

  function exportReport() {
    download(
      `${state.activeRun.runId ?? "run"}-qc-report.json`,
      JSON.stringify({ run_root: runRoot, stages, cases, manifest, splits, generated_by: "PAR-S Web Review" }, null, 2),
      "application/json",
    );
  }

  async function copyEvidence(stage: StageRecord) {
    try {
      await navigator.clipboard.writeText(JSON.stringify(stage, null, 2));
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div className="review-workspace" aria-busy={busy}>
      {error != null && <ErrorNotice error={error} onRetry={() => void refresh()} />}
      <header className="review-toolbar">
        <label>{t("review.run")}<select value={runRoot ?? ""} onChange={(event) => selectRun(event.target.value)}><option value="">{t("review.chooseRun")}</option>{runs.map((run) => <option key={run.root} value={run.root}>{run.run_id}{run.finalized ? ` · ${t("status.sealed")}` : ""}</option>)}</select></label>
        <span className="mono" title={runRoot ?? undefined}>{artifact?.path ?? runRoot ?? t("review.noRun")}</span>
        <button type="button" onClick={() => void refresh()} disabled={busy}>{t("review.refreshEvidence")}</button>
        <button type="button" onClick={() => setBrowserOpen(true)}>{t("review.openA00")}</button>
        {artifact && <button type="button" onClick={() => setArtifact(null)}>{t("review.returnRun")}</button>}
      </header>

      <section className="review-stages" aria-labelledby="review-stages-title">
        <div className="run-section-heading"><h2 id="review-stages-title">{t("review.stageEvidence")}</h2><span className="run-section-count">{stages.length}</span></div>
        <div className="stage-evidence-list">{stages.map((stage) => <details key={stage.stage}><summary><span className="lifecycle-state" data-state={stage.status} aria-hidden="true" /><strong>{stage.stage in stageTranslationKey ? t(stageTranslationKey[stage.stage as PipelineStage]) : stage.stage.replaceAll("_", " ")}</strong><span>{translateStatus(t, stage.status)}</span></summary><pre className="code mono">{JSON.stringify(stage, null, 2)}</pre><button type="button" onClick={() => void copyEvidence(stage)}>{t("review.copyEvidence")}</button></details>)}</div>
      </section>

      <section className="review-cases" aria-labelledby="review-cases-title">
        <div className="run-section-heading"><h2 id="review-cases-title">{t("review.caseLedger")}</h2><span className="run-section-count">{cases.length}</span></div>
        <div className="review-table-region" tabIndex={0} role="region" aria-label={t("review.caseLedger")}><table><thead><tr><th>{t("common.case")}</th><th>{t("review.split")}</th><th>{t("review.backend")}</th><th>/RR</th><th>{t("review.projectionQc")}</th><th>{t("review.observationQc")}</th><th className="num">{t("review.counts")}</th><th className="num">{t("review.angularCv")}</th></tr></thead><tbody>{cases.map((record) => {
          const backend = valueAt(record, ["expectation", "backend"]);
          const projectionQc = valueAt(record, ["qc", "projection", "status"]);
          const observationQc = valueAt(record, ["qc", "observation", "status"]);
          const counts = numeric(record, [["observation", "sum"], ["observation", "observed_total_counts"]]);
          const cv = numeric(record, [["observation", "angular_cv"]]);
          return <tr key={record.case_id} data-selected={selected === record.case_id}><th scope="row"><button type="button" className="table-link mono" onClick={() => { setArtifact(null); setSelected(record.case_id); }}>{record.case_id}</button></th><td>{record.split ?? "—"}</td><td>{String(backend ?? "—")}</td><td className="mono">{String(valueAt(record, ["expectation", "rr_seed"]) ?? record.seed ?? "—")}</td><td>{String(projectionQc ?? "—")}</td><td>{String(observationQc ?? "—")}</td><td className="num">{counts == null ? "—" : counts.toExponential(3)}</td><td className="num">{cv == null ? "—" : cv.toFixed(4)}</td></tr>;
        })}</tbody></table></div>
      </section>

      <section className="review-viewer" aria-labelledby="review-viewer-title">
        <div className="review-viewer-head"><h2 id="review-viewer-title">{artifact ? t("review.artifactInspector") : selected ?? t("review.projectionReview")}</h2>{!artifact && <div role="group" aria-label={t("review.layer")}><button type="button" aria-pressed={layer === "expectation"} onClick={() => setLayer("expectation")}>{t("review.expectation")}</button><button type="button" aria-pressed={layer === "observation"} onClick={() => setLayer("observation")}>{t("review.observation")}</button></div>}</div>
        <div className="review-image-grid">
          <div className="review-image-pane"><div className="review-image" onPointerDown={projectionPointer}>{projectionSource ? <img src={projectionSource} alt={t("review.projectionAlt")} onError={() => setError(new Error(t("review.imageUnavailable")))} /> : <span>{t("review.noImage")}</span>}<i className="projection-row" style={{ top: `${(Math.min(row, maxRow) / Math.max(maxRow, 1)) * 100}%` }} /></div><label>{t("review.view")}<input type="range" min={0} max={maxView} value={Math.min(view, maxView)} onChange={(event) => setView(Number(event.target.value))} /><output>{Math.min(view, maxView)}/{maxView}</output></label></div>
          <div className="review-image-pane"><div className="review-image">{sinogramSource ? <img src={sinogramSource} alt={t("review.sinogramAlt")} onError={() => setError(new Error(t("review.imageUnavailable")))} /> : <span>{t("review.noImage")}</span>}</div><label>{t("review.detectorRow")}<input type="range" min={0} max={maxRow} value={Math.min(row, maxRow)} onChange={(event) => setRow(Number(event.target.value))} /><output>{Math.min(row, maxRow)}/{maxRow}</output></label></div>
        </div>
        <div className="review-probe mono">{artifact ? `${artifact.shape.join("×")} · Σ ${artifact.sum.toExponential(4)} · max ${artifact.maximum.toExponential(3)} · nonzero ${(artifact.nonzero_fraction * 100).toFixed(2)}%` : evidence ? `${evidence.backend ?? "—"} · ${evidence.effective.projection_shape?.join("×") ?? "—"} · /NN ${evidence.effective.nn_multiplier ?? "—"} · /RR ${evidence.rr_seed ?? "—"}` : "—"}</div>
      </section>

      <aside className="review-evidence" aria-labelledby="review-detail-title">
        <div className="run-section-heading"><h2 id="review-detail-title">{t("review.effectiveEvidence")}</h2></div>
        {evidence ? <><dl className="review-facts">{Object.entries(evidence.effective).map(([key, value]) => <div key={key}><dt>{key in EFFECTIVE_LABELS ? t(EFFECTIVE_LABELS[key as keyof typeof EFFECTIVE_LABELS]) : key.replaceAll("_", " ")}</dt><dd className="mono">{Array.isArray(value) ? value.join(" × ") : String(value ?? "—")}</dd></div>)}</dl>{evidence.res_excerpt && <details className="res-evidence"><summary>{t("review.resEvidence")}</summary><pre className="code mono">{evidence.res_excerpt}</pre></details>}</> : artifact ? <dl className="review-facts">{Object.entries(artifact).map(([key, value]) => <div key={key}><dt>{key in ARTIFACT_LABELS ? t(ARTIFACT_LABELS[key as keyof typeof ARTIFACT_LABELS]) : key.replaceAll("_", " ")}</dt><dd className="mono">{Array.isArray(value) ? value.join(" × ") : String(value)}</dd></div>)}</dl> : <p className="preflight-empty">{selectedCase ? t("common.loading") : t("review.selectCase")}</p>}
        <details className="manifest-evidence"><summary>{t("review.manifestAndSplits")}</summary><pre className="code mono">{JSON.stringify({ manifest, splits }, null, 2)}</pre></details>
      </aside>

      <footer className="page-command-shelf review-command-shelf">
        <span className="command-signal" data-tone={manifest ? "success" : cases.length ? "warning" : "danger"} />
        <div className="command-copy"><strong>{manifest ? t("review.packageAvailable") : t("review.packagePending")}</strong><span>{protocol?.canonical_projection_transform ?? "raw[:,::-1,:]"}</span></div>
        <span className="command-meta mono">{t("review.commandMeta", { cases: cases.length, passed: stages.filter((stage) => stage.status === "passed" || stage.status === "prepared").length, total: stages.length })}</span>
        <div className="command-actions"><button type="button" onClick={exportCsv} disabled={!cases.length}>{t("review.exportCsv")}</button><button type="button" onClick={exportReport} disabled={!cases.length}>{t("review.exportReport")}</button><button type="button" className="primary" disabled={!runRoot || !manifest} onClick={() => dispatch({ type: "view/set", view: "seal" })}>{t("review.continueSeal")}</button></div>
      </footer>

      <FileBrowser open={browserOpen} title={t("review.chooseA00")} initialPath={runRoot ?? state.draft.identity.runsRoot} selection="file" extensions={[".a00"]} onSelect={(path) => void inspectArtifact(path)} onClose={() => setBrowserOpen(false)} />
    </div>
  );
}
