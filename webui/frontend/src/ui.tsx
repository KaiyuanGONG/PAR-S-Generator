/** Shared presentational primitives. No pipeline logic lives here. */
import React from "react";

export function Card({
  title,
  note,
  children,
  flush,
}: {
  title?: string;
  note?: React.ReactNode;
  children: React.ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="card">
      {title && (
        <header>
          <h2>{title}</h2>
          {note && <span className="note">{note}</span>}
        </header>
      )}
      <div className={"inner" + (flush ? " flush" : "")}>{children}</div>
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <div>
        {children}
        {hint && <div className="hint">{hint}</div>}
      </div>
    </div>
  );
}

export function KV({ k, v }: { k: React.ReactNode; v: React.ReactNode }) {
  return (
    <div className="kv">
      <span>{k}</span>
      <b>{v}</b>
    </div>
  );
}

const TONE: Record<string, string> = {
  passed: "ok",
  prepared: "ok",
  complete: "ok",
  done: "ok",
  ok: "ok",
  finished: "ok",
  running: "run",
  simulating: "run",
  paused: "warn",
  warning: "warn",
  open: "warn",
  queued: "",
  pending: "",
  failed: "err",
  error: "err",
};

export function Status({ s }: { s?: string }) {
  const tone = TONE[(s ?? "").toLowerCase()] ?? "";
  return (
    <span className={"pill " + tone}>
      <i className="dot" />
      {s ?? "—"}
    </span>
  );
}

/** Ordered stage rail — the run's real stage vocabulary, straight from the API. */
export function StageRail({
  order,
  states,
}: {
  order: string[];
  states: Record<string, string | undefined>;
}) {
  return (
    <div className="rail-stages">
      {order.map((name, i) => {
        const s = states[name];
        const done = s === "passed" || s === "prepared";
        return (
          <React.Fragment key={name}>
            {i > 0 && <span className="link" data-done={done ? "true" : "false"} />}
            <div className="stage" data-s={s ?? "pending"}>
              <span className="b" />
              <small>{name.replace(/_/g, " ")}</small>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}

export function Bar({ value }: { value: number }) {
  return (
    <div className="bar">
      <i style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

/** Range + numeric readout, with the sampling distribution spelled out —
 *  these controls describe a cohort, not one hand-drawn case. */
export function Param({
  label,
  dist,
  value,
  min,
  max,
  step = 1,
  unit,
  onChange,
}: {
  label: string;
  dist?: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange?: (v: number) => void;
}) {
  return (
    <div style={{ marginBottom: 11 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <label style={{ fontSize: 12.5, color: "var(--tx-2)" }}>{label}</label>
        <span className="sp" />
        <b className="mono" style={{ fontWeight: 500 }}>
          {value}
          {unit ? <span className="muted"> {unit}</span> : null}
        </b>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange?.(Number(e.target.value))}
      />
      {dist && <div className="muted mono">{dist}</div>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: "26px 14px", color: "var(--tx-3)", fontSize: 12.5 }}>{children}</div>;
}
