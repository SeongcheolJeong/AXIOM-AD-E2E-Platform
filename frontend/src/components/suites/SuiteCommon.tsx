import { useRef } from "react";
import { AlertCircle, LoaderCircle } from "lucide-react";
import type { Parameters } from "../../types";

export { useJobResource as useSuiteResource } from "../jobs/useJobResource";

export const overrideFields: {
  key: keyof Parameters;
  label: string;
  unit: string;
  min: number;
  max: number;
}[] = [
  { key: "ego_speed_mps", label: "Ego speed", unit: "m/s", min: 0, max: 55 },
  {
    key: "target_speed_mps",
    label: "Actor speed",
    unit: "m/s",
    min: 0,
    max: 55,
  },
  { key: "initial_gap_m", label: "Initial gap", unit: "m", min: 0, max: 500 },
  { key: "trigger_time_s", label: "Trigger time", unit: "s", min: 0, max: 119 },
  { key: "friction", label: "Road friction", unit: "μ", min: 0.1, max: 1.2 },
  {
    key: "response_delay_s",
    label: "Response delay",
    unit: "s",
    min: 0,
    max: 10,
  },
];

export function useSuiteSubmissionKey() {
  const keys = useRef(new Map<string, string>());
  return (scope: string, body: string) => {
    const identity = `${scope}\n${body}`;
    let key = keys.current.get(identity);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(identity, key);
    }
    return key;
  };
}

export function SuiteRefreshNotice({
  error,
  hasData,
  label,
  onRetry,
}: {
  error: string;
  hasData: boolean;
  label: string;
  onRetry: () => void;
}) {
  if (!error) return null;
  return (
    <div className="suite-refresh-notice" role="status">
      <AlertCircle size={16} />
      <span>
        {label} refresh failed.{" "}
        {hasData ? "Showing the last observation for this selection. " : ""}
        {error}
      </span>
      <button type="button" className="suite-text-button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function SuitePendingButton({
  pending,
  label,
  pendingLabel,
  disabled,
}: {
  pending: boolean;
  label: string;
  pendingLabel: string;
  disabled?: boolean;
}) {
  return (
    <button className="button primary" disabled={pending || disabled}>
      {pending && <LoaderCircle size={15} className="spin" />}
      {pending ? pendingLabel : label}
    </button>
  );
}

export function criterionOperator(operator: string) {
  return operator === "lte" ? "≤" : operator === "gte" ? "≥" : "=";
}

export function suiteNumber(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value))
    return "—";
  return value.toLocaleString("en-US", { maximumSignificantDigits: 7 });
}
