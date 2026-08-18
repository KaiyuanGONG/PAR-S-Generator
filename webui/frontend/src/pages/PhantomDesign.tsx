import { useEffect, useState } from "react";
import { api, img, type PreviewSummary } from "../api";
import { Card, Empty, KV, Param } from "../ui";

const PLANES: Array<{ key: string; label: string; axes: string }> = [
  { key: "axial", label: "axial", axes: "A · L" },
  { key: "coronal", label: "coronal", axes: "H · L" },
  { key: "sagittal", label: "sagittal", axes: "H · A" },
];

export default function PhantomDesign({ defaults }: { defaults: Record<string, any> | null }) {
  const base = defaults?.phantom ?? {};
  const [seed, setSeed] = useState(42);
  const [caseIndex, setCaseIndex] = useState(11);
  const [leftRatio, setLeftRatio] = useState<number>(base.target_left_ratio ?? 0.35);
  const [scaleJitter, setScaleJitter] = useState<number>((base.scale_jitter ?? 0.1) * 100);
  const [rotJitter, setRotJitter] = useState<number>(base.rot_jitter_deg ?? 5);
  const [tmin, setTmin] = useState<number>(base.tumor_count_min ?? 1);
  const [tmax, setTmax] = useState<number>(base.tumor_count_max ?? 5);
  const [margin, setMargin] = useState<number>(base.tumor_min_liver_margin_mm ?? 4.42);

  const [pid, setPid] = useState<string | null>(null);
  const [sum, setSum] = useState<PreviewSummary | null>(null);
  const [layer, setLayer] = useState<"activity" | "mu">("activity");
  const [idx, setIdx] = useState({ axial: 64, coronal: 61, sagittal: 70 });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function preview() {
    setBusy(true);
    setErr(null);
    try {
      const cfg = {
        ...base,
        target_left_ratio: leftRatio,
        scale_jitter: scaleJitter / 100,
        rot_jitter_deg: rotJitter,
        tumor_count_min: tmin,
        tumor_count_max: Math.max(tmin, tmax),
        tumor_min_liver_margin_mm: margin,
      };
      const r = await api.previewPhantom({ phantom_config: cfg, case_index: caseIndex, seed });
      setPid(r.preview_id);
      setSum(r.summary);
    } catch (e: any) {
      setErr(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (defaults) preview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaults]);

  const lesions = sum?.tumor_metadata ?? [];

  return (
    <>
      {err && <div className="banner err">{err}</div>}
      <div className="ph-grid">
        <div>
          <Card title="Liver anchors" note="sampled per case">
            <Param
              label="Target left-lobe ratio"
              dist="Cantlie plane solved per case to this target"
              value={Number(leftRatio.toFixed(3))}
              min={0.25}
              max={0.45}
              step={0.005}
              onChange={setLeftRatio}
            />
            <Param
              label="Scale jitter"
              dist="U[1−j, 1+j] on every semi-axis"
              value={Math.round(scaleJitter)}
              min={0}
              max={25}
              unit="%"
              onChange={setScaleJitter}
            />
            <Param
              label="Rotation jitter"
              dist="U[−r, +r] per lobe"
              value={rotJitter}
              min={0}
              max={15}
              unit="°"
              onChange={setRotJitter}
            />
          </Card>

          <Card title="Lesions" note={`${sum?.n_tumors ?? 0} in this case`}>
            <Param label="Count min" value={tmin} min={0} max={5} onChange={setTmin} />
            <Param label="Count max" value={tmax} min={0} max={8} onChange={setTmax} />
            <Param
              label="Min surface margin"
              dist="rejected below this distance to the liver surface"
              value={Number(margin.toFixed(2))}
              min={0}
              max={12}
              step={0.01}
              unit="mm"
              onChange={setMargin}
            />
            <div className="muted mono" style={{ marginTop: 6 }}>
              size bins {(base.tumor_size_bins_mm ?? [])
                .map((b: number[]) => `${b[0]}–${b[1]}`)
                .join(" / ")} mm · p {(base.tumor_probs ?? []).join(" / ")}
              <br />
              TNR U[{base.tumor_contrast_min ?? "—"}, {base.tumor_contrast_max ?? "—"}]
            </div>
          </Card>

          <Card title="Preview">
            <div className="field">
              <label>Seed</label>
              <input
                type="number"
                className="mono"
                value={seed}
                onChange={(e) => setSeed(Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label>Case index</label>
              <input
                type="number"
                className="mono"
                value={caseIndex}
                onChange={(e) => setCaseIndex(Number(e.target.value))}
              />
            </div>
            <div className="actions">
              <button className="primary" onClick={preview} disabled={busy}>
                {busy ? "Generating…" : "Regenerate preview"}
              </button>
              <button onClick={() => { setSeed(seed + 1); setTimeout(preview, 0); }} disabled={busy}>
                Next draw
              </button>
            </div>
          </Card>
        </div>

        <Card
          title="Orthogonal views"
          note={
            <span className="mono">
              128³ · {sum?.voxel_size_mm?.toFixed(2) ?? "4.42"} mm iso
            </span>
          }
        >
          <div className="actions" style={{ marginBottom: 10 }}>
            <button className={layer === "activity" ? "primary" : ""} onClick={() => setLayer("activity")}>
              activity
            </button>
            <button className={layer === "mu" ? "primary" : ""} onClick={() => setLayer("mu")}>
              μ-map
            </button>
            <span className="sp" />
            <span className="muted mono">
              {sum ? `case_${String(sum.case_id).padStart(4, "0")} · seed ${sum.seed}` : "—"}
            </span>
          </div>

          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            {PLANES.map((p) => (
              <div key={p.key}>
                <div className="viewer" style={{ aspectRatio: "1 / 1" }}>
                  {pid ? (
                    <img
                      src={img.slice(pid, p.key, (idx as any)[p.key], layer)}
                      alt={p.label}
                    />
                  ) : (
                    <div className="empty">no preview</div>
                  )}
                  <span className="tag">{p.label}</span>
                  <span className="tag r">{p.axes}</span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 5 }}>
                  <input
                    type="range"
                    min={0}
                    max={127}
                    value={(idx as any)[p.key]}
                    onChange={(e) => setIdx({ ...idx, [p.key]: Number(e.target.value) })}
                  />
                  <span className="mono muted" style={{ whiteSpace: "nowrap" }}>
                    {(idx as any)[p.key]}/128
                  </span>
                </div>
              </div>
            ))}
            <div className="viewer" style={{ aspectRatio: "1 / 1", display: "grid", placeItems: "center" }}>
              <div className="empty" style={{ padding: 12, textAlign: "center" }}>
                lesion map
                <br />
                {sum ? `${sum.n_tumors} lesion${sum.n_tumors === 1 ? "" : "s"} · ${sum.perfusion_mode}` : "—"}
              </div>
            </div>
          </div>
        </Card>

        <div>
          <Card title="This case" note="measured from final masks">
            {sum ? (
              <>
                <KV k="Liver volume" v={`${sum.liver_volume_ml.toFixed(1)} mL`} />
                <KV k="Left-lobe ratio" v={sum.left_ratio.toFixed(3)} />
                <KV k="Cantlie converged" v={sum.cantlie_converged ? "yes" : "no"} />
                <KV k="Perfusion" v={sum.perfusion_mode} />
                <KV k="Total counts" v={sum.total_counts_actual.toExponential(2)} />
                <KV k="Generation" v={`${sum.generation_time_s.toFixed(2)} s`} />
              </>
            ) : (
              <Empty>Generate a preview to see measured values.</Empty>
            )}
          </Card>

          <Card title="Lesions" note="measured, not requested" flush>
            {lesions.length ? (
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>mode</th>
                    <th>lobe</th>
                    <th className="num">Ø mm</th>
                    <th className="num">margin</th>
                    <th className="num">TNR</th>
                  </tr>
                </thead>
                <tbody>
                  {lesions.map((l, i) => (
                    <tr key={i}>
                      <td className="mono">{i + 1}</td>
                      <td>{l.mode ?? "—"}</td>
                      <td>{l.lobe ?? "—"}</td>
                      <td className="num">
                        {l.effective_diameter_mm != null ? l.effective_diameter_mm.toFixed(1) : "—"}
                      </td>
                      <td className="num">
                        {l.surface_margin_mm != null ? l.surface_margin_mm.toFixed(1) : "—"}
                      </td>
                      <td className="num">
                        {l.tnr_local != null
                          ? l.tnr_local.toFixed(2)
                          : l.target_contrast != null
                          ? l.target_contrast.toFixed(2)
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>No lesions in this draw.</Empty>
            )}
          </Card>

          <Card title="Population envelope">
            <KV k="Liver volume gate" v="904 – 1900 mL" />
            <KV k="Left-ratio tolerance" v="target ± 0.006" />
            <KV k="Containment" v="0 outside · 0 overlap" />
            <div className="muted" style={{ marginTop: 8 }}>
              These are the QC gates the whole cohort must satisfy, not this single draw.
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
