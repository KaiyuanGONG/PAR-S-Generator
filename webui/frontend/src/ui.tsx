/** Shared semantic primitives. No pipeline logic lives here. */
import React, { useId } from "react";

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

export function Status({ s, label }: { s?: string; label?: string }) {
  const tone = TONE[(s ?? "").toLowerCase()] ?? "";
  return (
    <span className={"status-mark " + tone} data-status={(s ?? "pending").toLowerCase()}>
      <i className="dot" />
      {label ?? s ?? "—"}
    </span>
  );
}

/** Ordered stage rail — the run's real stage vocabulary, straight from the API. */
export function StageRail({
  order,
  states,
  label = (name) => name.replace(/_/g, " "),
  statusLabel = (status) => status,
  ariaLabel = "Pipeline stages",
}: {
  order: string[];
  states: Record<string, string | undefined>;
  label?: (name: string) => string;
  statusLabel?: (status: string) => string;
  ariaLabel?: string;
}) {
  return (
    <div className="rail-stages" role="list" aria-label={ariaLabel}>
      {order.map((name, i) => {
        const s = states[name];
        const done = s === "passed" || s === "prepared";
        return (
          <React.Fragment key={name}>
            {i > 0 && <span className="link" data-done={done ? "true" : "false"} aria-hidden="true" />}
            <div className="stage" data-s={s ?? "pending"} role="listitem">
              <span className="b" aria-hidden="true" />
              <small>{label(name)}</small>
              <span className="sr-only"> — {statusLabel(s ?? "pending")}</span>
            </div>
          </React.Fragment>
        );
      })}
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
  disabled = false,
}: {
  label: string;
  dist?: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange?: (v: number) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="param">
      <div className="param-head">
        <label htmlFor={id}>{label}</label>
        <span className="sp" />
        <output className="mono" htmlFor={id}>
          {value}
          {unit ? <span className="muted"> {unit}</span> : null}
        </output>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange?.(Number(e.target.value))}
      />
      {dist && <div className="param-dist mono">{dist}</div>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-state">{children}</div>;
}
