import { useEffect, useRef, useState } from "react";
import { ArrowDownToLine, LoaderCircle } from "lucide-react";
import { readLiveArtifact } from "../carla/CarlaLiveData";

/** Core-only downloads. Verify original sealed bytes before exposing a file. */
export default function RecoveryDownload({
  id,
  kind,
  url,
  sha256,
  size,
}: {
  id: string;
  kind: "export" | "archive";
  url: string;
  sha256: string;
  size: number;
}) {
  const [pending, setPending] = useState(false),
    [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(
    () => () => request.current?.abort(),
    [id, kind, url, sha256, size],
  );
  const label =
    kind === "archive"
      ? "Download original remote archive"
      : "Download recovery manifest";
  async function download() {
    if (pending) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    setError("");
    try {
      const expected = `/api/v1/execution-recoveries/${encodeURIComponent(id)}/${kind}`;
      if (
        url !== expected ||
        !/^[a-f0-9]{64}$/.test(sha256) ||
        !Number.isSafeInteger(size) ||
        size < 1 ||
        size > 128 * 1024 * 1024
      )
        throw new Error("Recovery download identity, hash or size is invalid.");
      const bytes = await readLiveArtifact(
        { download_url: expected, size_bytes: size, sha256 },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      const blobUrl = URL.createObjectURL(
        new Blob([bytes], { type: "application/json" }),
      );
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = `execution-recovery-${kind}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(
          reason instanceof Error
            ? reason.message
            : "Recovery download failed.",
        );
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  }
  return (
    <>
      <a
        className="button secondary small"
        href={url}
        download
        aria-disabled={pending}
        onClick={(event) => {
          event.preventDefault();
          void download();
        }}
      >
        {pending ? (
          <LoaderCircle size={14} className="spin" />
        ) : (
          <ArrowDownToLine size={14} />
        )}
        {pending ? "Verifying bytes…" : label}
      </a>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
