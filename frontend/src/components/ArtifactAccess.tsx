import { useEffect, useState, type AnchorHTMLAttributes } from "react";
import { authHeaders } from "../api";
export function DownloadLink({
  href,
  children,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const [error, setError] = useState("");
  return (
    <>
      <a
        {...rest}
        href={href}
        onClick={async (e) => {
          if (!sessionStorage.getItem("axiom.apiToken")) return;
          e.preventDefault();
          setError("");
          try {
            const response = await fetch(href, { headers: authHeaders() });
            if (!response.ok)
              throw new Error(`Download failed (${response.status})`);
            const blob = await response.blob(),
              url = URL.createObjectURL(blob),
              link = document.createElement("a");
            link.href = url;
            const disposition = response.headers.get("content-disposition");
            link.download =
              disposition?.match(/filename="?([^";]+)/)?.[1] ??
              "axiom-evidence";
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        {children}
      </a>
      {error && (
        <span className="inline-error" role="alert">
          {error}
        </span>
      )}
    </>
  );
}
export function ArtifactImage({ url, alt }: { url: string; alt: string }) {
  const [source, setSource] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true,
      objectUrl = "";
    setSource("");
    setError("");
    fetch(url, { headers: authHeaders() })
      .then((r) => {
        if (!r.ok) throw new Error(`Image unavailable (${r.status})`);
        return r.blob();
      })
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch((e) => {
        if (active) setError((e as Error).message);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  return error ? (
    <p className="inline-error">{error}</p>
  ) : source ? (
    <img className="artifact-preview" src={source} alt={alt} />
  ) : (
    <p className="muted">Loading recorded image…</p>
  );
}
