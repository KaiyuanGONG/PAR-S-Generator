import { useEffect, useState } from "react";
import { api, img, type CaseRecord, type Protocol, type StageRecord } from "../api";
import { Card, Empty, KV, Status } from "../ui";

export default function QCDataset({
  protocol,
  runRoot,
  setRunRoot,
}: {
  protocol: Protocol | null;
  runRoot: string | null;
  setRunRoot: (v: string) => void;
}) {
  const [runs, setRuns] = useState<Array<{ run_id: string; root: string; finalized: boolean }>>([]);
  const [stages, setStages] = useState<StageRecord[]>([]);
  const [finalized, setFinalized] = useState(false);
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [view, setView] = useState(0);
  const [row, setRow] = useState(64);
  const [layer, setLayer] = useState<"expectation" | "observation">("expectation");
  const [manifest, setManifest] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    api.runs().then((r) => setRuns(r.runs)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!runRoot) return;
    api.stages(runRoot).then((s) => {
      setStages(s.stages);
      setFinalized(s.finalized);
    }).catch(() => setStages([]));
    api.cases(runRoot, 0, 500).then((c) => {
      setCases(c.cases);
      if (c.cases.length) setSel(c.cases[0].case_id);
    }).catch(() => setCases([]));
    api.run(runRoot).then(setManifest).catch(() => setManifest(null));
  }, [runRoot]);

  return (
    <>
      <Card title="Run">
        <div className="actions">
          <select
            value={runRoot ?? ""}
            onChange={(e) => setRunRoot(e.target.value)}
            style={{ maxWidth: 520 }}
            className="mono"
          >
            <option value="">select a run…</option>
            {runs.map((r) => (
              <option key={r.root} value={r.root}>
                {r.run_id} {r.finalized ? "· finalized" : ""}
              </option>
            ))}
          </select>
          <span className="muted">{runs.length} run(s) under the runs root</span>
        </div>
      </Card>

      {!runRoot ? (
        <Card>
          <Empty>Select a run to inspect its stage gates, case ledger and projections.</Empty>
        </Card>
      ) : (
        <div className="split">
          <div>
            <Card title="Stage gates" note="evidence recorded in run.json" flush>
              {stages.length ? (
                <table>
                  <thead>
                    <tr>
                      <th>stage</th>
                      <th>result</th>
                      <th>evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stages.map((s) => {
                      const { stage, status, ...rest } = s as any;
                      delete rest.updated_utc;
                      return (
                        <tr key={stage}>
                          <td>{stage.replace(/_/g, " ")}</td>
                          <td>
                            <Status s={status} />
                          </td>
                          <td className="mono muted" style={{ maxWidth: 380, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {JSON.stringify(rest)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <Empty>No stage records yet.</Empty>
              )}
            </Card>

            <Card title="Cases" note={`${cases.length} in ledger`} flush>
              {cases.length ? (
                <table>
                  <thead>
                    <tr>
                      <th>case</th>
                      <th>split</th>
                      <th>phantom</th>
                      <th>projection</th>
                      <th>observation</th>
                      <th className="num">counts</th>
                      <th className="num">ang. CV</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cases.map((c) => {
                      const qc = (c.qc ?? {}) as any;
                      const obs = (c.observation ?? {}) as any;
                      return (
                        <tr
                          key={c.case_id}
                          data-sel={sel === c.case_id}
                          onClick={() => setSel(c.case_id)}
                          style={{ cursor: "pointer" }}
                        >
                          <td className="mono">{c.case_id}</td>
                          <td>{c.split ?? "—"}</td>
                          <td>
                            <Status s={qc.phantom?.status ?? qc.phantom_status} />
                          </td>
                          <td>
                            <Status s={qc.projection?.status ?? qc.projection_status} />
                          </td>
                          <td>
                            <Status s={qc.observation?.status} />
                          </td>
                          <td className="num">
                            {obs.sum != null ? (obs.sum / 1e6).toFixed(2) + " M" : "—"}
                          </td>
                          <td className="num">
                            {obs.angular_cv != null ? obs.angular_cv.toFixed(3) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <Empty>No cases recorded.</Empty>
              )}
            </Card>
          </div>

          <div>
            <Card
              title="Projection viewer"
              note={<span className="mono">{sel ?? "—"}</span>}
            >
              <div className="actions" style={{ marginBottom: 10 }}>
                <button
                  className={layer === "expectation" ? "primary" : ""}
                  onClick={() => setLayer("expectation")}
                >
                  expectation
                </button>
                <button
                  className={layer === "observation" ? "primary" : ""}
                  onClick={() => setLayer("observation")}
                >
                  observation
                </button>
              </div>
              <div className="viewer" style={{ aspectRatio: "1 / 1" }}>
                {sel ? (
                  <img src={img.projection(runRoot, sel, view, layer)} alt="projection" />
                ) : (
                  <div className="empty">no case selected</div>
                )}
                <span className="tag">
                  view {view + 1} / 60 · {(view * 6).toFixed(1)}°
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={59}
                value={view}
                onChange={(e) => setView(Number(e.target.value))}
                style={{ marginTop: 6 }}
              />

              <div className="viewer" style={{ marginTop: 10 }}>
                {sel ? (
                  <img src={img.sinogram(runRoot, sel, row, layer)} alt="sinogram" />
                ) : (
                  <div className="empty">—</div>
                )}
                <span className="tag">sinogram · row {row}</span>
              </div>
              <input
                type="range"
                min={0}
                max={127}
                value={row}
                onChange={(e) => setRow(Number(e.target.value))}
                style={{ marginTop: 6 }}
              />
              <div className="muted" style={{ marginTop: 6 }}>
                Rendered in the validated canonical orientation{" "}
                <code>{protocol?.canonical_projection_transform}</code>.
              </div>
            </Card>

            <Card title="Dataset" note={finalized ? "finalized" : "draft"}>
              <KV k="Run ID" v={manifest?.run_id ?? "—"} />
              <KV k="Created" v={manifest?.created_utc?.slice(0, 19).replace("T", " ") ?? "—"} />
              <KV k="Cases" v={manifest?.case_count ?? cases.length} />
              <KV k="Mode" v={manifest?.effective_config?.simulation_mode ?? "—"} />
              <KV
                k="Manifest sha-256"
                v={manifest?.package_sha256 ? String(manifest.package_sha256).slice(0, 16) + "…" : "—"}
              />
              <div className="actions" style={{ marginTop: 12 }}>
                <button className="primary" disabled={finalized}>
                  {finalized ? "Finalized" : "Finalize dataset"}
                </button>
                <span className="muted">A finalized manifest is immutable.</span>
              </div>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
