import {
  Check,
  Circle,
  CircleSlash2,
  LoaderCircle,
  OctagonAlert,
  Pause,
  X,
} from "lucide-react";
import { jobStatusLabels, type JobStatus } from "./JobTypes";

const icons = {
  queued: Circle,
  running: LoaderCircle,
  cancel_requested: Pause,
  completed: Check,
  failed: X,
  cancelled: CircleSlash2,
  interrupted: OctagonAlert,
};

export default function JobStatusBadge({ status }: { status: JobStatus }) {
  const Icon = icons[status];
  return (
    <span className={`job-status ${status}`} data-testid="job-status">
      <Icon size={13} className={status === "running" ? "spin" : ""} />
      {jobStatusLabels[status]}
    </span>
  );
}
