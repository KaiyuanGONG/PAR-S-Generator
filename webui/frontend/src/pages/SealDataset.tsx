import { useEffect, useState } from "react";
import { api, type FinalizeResult, type StageRecord } from "../api";
import ErrorNotice from "../components/ErrorNotice";
import { useI18n } from "../i18n";
import { useWorkspace } from "../workspace";

export default function SealDataset() {
  const { state, dispatch } = useWorkspace();
  const { t } = useI18n();
  const runRoot = state.activeRun.runRoot;
  const runId = state.activeRun.runId ?? state.draft.identity.runId;
  const [stages, setStages] = useState<StageRecord[]>([]);
  const [manifest, setManifest] = useState<Record<string, any> | null>(null);
  const [splits, setSplits] = useState<Record<string, any> | null>(null);
  const [typedRunId, setTypedRunId] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<FinalizeResult | null>(null);

  async function refresh() {
    if (!runRoot) return;
    setBusy(true);
    setError(null);
    try {
      const [stageResponse, manifestResponse, splitResponse] = await Promise.all([
        api.stages(runRoot),
        api.manifest(runRoot),
        api.splits(runRoot),
      ]);
      setStages(stageResponse.stages);
      setManifest(manifestResponse);
      setSplits(splitResponse);
      if (stageResponse.finalized) dispatch({ type: "run/sealed" });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [runRoot]);

  const packageStage = stages.find((stage) => stage.stage === "package");
  const failedStage = stages.find((stage) => stage.status === "failed");
  const active = ["running", "pause-requested", "paused"].includes(state.lifecycle);
  const modeAllowsSeal = state.draft.simulation.mode !== "prepare";
  const checks = [
    { id: "run", passed: Boolean(runRoot), text: t("seal.checkRun") },
    { id: "mode", passed: modeAllowsSeal, text: t("seal.checkMode") },
    { id: "inactive", passed: !active, text: t("seal.checkInactive") },
    { id: "package", passed: packageStage?.status === "passed", text: t("seal.checkPackage") },
    { id: "stages", passed: !failedStage, text: t("seal.checkStages") },
    { id: "manifest", passed: Boolean(manifest && Array.isArray(manifest.files)), text: t("seal.checkManifest") },
  ];
  const ready = checks.every((check) => check.passed);
  const confirmed = typedRunId === runId && acknowledged;
  const sealed = state.activeRun.finalized || state.lifecycle === "sealed" || Boolean(result?.finalized);
  const hash = result?.package_sha256 ?? String(valueAt(stages.find((stage) => stage.stage === "finalize"), "package_sha256") ?? "");
  const files = Array.isArray(manifest?.files) ? manifest.files as Array<Record<string, unknown>> : [];
  const splitMap = splits?.splits && typeof splits.splits === "object" ? splits.splits as Record<string, unknown[]> : {};

  async function finalize() {
    if (!runRoot || !ready || !confirmed) return;
    setBusy(true);
    setError(null);
    try {
      const finalized = await api.finalizeRun(runRoot);
      setResult(finalized);
      dispatch({ type: "run/sealed" });
      await refresh();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="seal-workspace" aria-busy={busy}>
      {error != null && <ErrorNotice error={error} onRetry={() => void refresh()} />}
      <section className="seal-readiness" aria-labelledby="seal-readiness-title">
        <header className="workspace-section-head"><div><span className="run-eyebrow">01</span><h2 id="seal-readiness-title">{t("seal.readiness")}</h2></div><span className="section-state" data-state={ready ? "passed" : "failed"}>{ready ? t("status.ready") : t("status.blocked")}</span></header>
        <div className="seal-checklist">{checks.map((check) => <div key={check.id} data-state={check.passed ? "passed" : "failed"}><span aria-hidden="true">{check.passed ? "✓" : "×"}</span><strong>{check.text}</strong><small>{check.passed ? t("status.passed") : t("status.blocked")}</small></div>)}</div>
        <p className="seal-authority">{t("seal.runnerAuthority")}</p>
      </section>

      <section className="seal-package" aria-labelledby="seal-package-title">
        <header className="workspace-section-head"><div><span className="run-eyebrow">02</span><h2 id="seal-package-title">{t("seal.packageEvidence")}</h2></div><button type="button" onClick={() => void refresh()} disabled={!runRoot || busy}>{t("review.refreshEvidence")}</button></header>
        <dl className="seal-summary"><div><dt>{t("seal.datasetId")}</dt><dd className="mono">{String(manifest?.dataset_id ?? runId)}</dd></div><div><dt>{t("seal.scope")}</dt><dd className="mono">{String(manifest?.scope ?? "—")}</dd></div><div><dt>{t("protocol.caseCount")}</dt><dd className="mono">{String(manifest?.case_count ?? "—")}</dd></div><div><dt>{t("seal.inventory")}</dt><dd className="mono">{t("seal.files.count", { count: files.length })}</dd></div><div><dt>{t("seal.splits")}</dt><dd className="mono">{["train", "val", "test"].map((name) => Array.isArray(splitMap[name]) ? splitMap[name].length : 0).join(" / ")}</dd></div><div><dt>{t("seal.orientation")}</dt><dd className="mono">{String(manifest?.projection_orientation ?? "—")}</dd></div></dl>
        <div className="manifest-files" tabIndex={0} role="region" aria-label={t("seal.inventory")}><table><thead><tr><th>{t("common.path")}</th><th className="num">{t("seal.bytes")}</th><th>sha-256</th></tr></thead><tbody>{files.map((file) => <tr key={String(file.path)}><td className="mono">{String(file.path)}</td><td className="num">{String(file.bytes ?? "—")}</td><td className="mono hash-cell">{String(file.sha256 ?? "—")}</td></tr>)}</tbody></table></div>
      </section>

      <section className="seal-confirmation" aria-labelledby="seal-confirmation-title">
        <header className="workspace-section-head"><div><span className="run-eyebrow">03</span><h2 id="seal-confirmation-title">{sealed ? t("seal.sealedTitle") : t("seal.confirmation")}</h2></div><span className="section-state" data-state={sealed ? "passed" : "failed"}>{sealed ? t("status.sealed") : t("seal.irreversible")}</span></header>
        {sealed ? <div className="sealed-result"><span>{t("seal.packageHash")}</span><strong className="mono">{hash || "—"}</strong><button type="button" disabled={!hash} onClick={() => void navigator.clipboard.writeText(hash).catch(setError)}>{t("review.copyEvidence")}</button><p>{t("seal.readOnlyAfterSeal")}</p></div> : <div className="irreversible-confirm"><p>{t("seal.confirmHelp", { runId })}</p><label className="stacked-field">{t("seal.typeRunId")}<input className="mono" value={typedRunId} onChange={(event) => setTypedRunId(event.target.value)} autoComplete="off" /></label><label className="check-row"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />{t("seal.acknowledge")}</label></div>}
      </section>

      <footer className="page-command-shelf seal-command-shelf">
        <span className="command-signal" data-tone={sealed ? "success" : ready ? "warning" : "danger"} />
        <div className="command-copy"><strong>{sealed ? t("seal.sealedTitle") : ready ? t("seal.readyToSeal") : t("seal.notReady")}</strong><span>{sealed ? t("seal.readOnlyAfterSeal") : t("seal.explicitOnly")}</span></div>
        <span className="command-meta mono">{runId} · {t("seal.files.count", { count: files.length })}</span>
        <div className="command-actions">{sealed ? <button type="button" onClick={() => dispatch({ type: "run/fork", runId: `${runId}-fork` })}>{t("action.fork")}</button> : <button type="button" className="danger" disabled={!ready || !confirmed || busy} onClick={() => void finalize()}>{busy ? t("common.loading") : t("action.finalize")}</button>}</div>
      </footer>
    </div>
  );
}

function valueAt(record: unknown, key: string): unknown {
  return record && typeof record === "object" && !Array.isArray(record) ? (record as Record<string, unknown>)[key] : undefined;
}
