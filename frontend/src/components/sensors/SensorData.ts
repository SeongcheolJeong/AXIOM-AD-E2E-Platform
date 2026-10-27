import { useEffect, useState } from "react";
import { api } from "../../api";

/** Request identity is part of the state: an old selection is never new evidence. */
export function useSensorResource<T>(path: string | null, delay = 0) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    path: string | null;
    data: T | null;
    error: string;
    loading: boolean;
  }>({ path: null, data: null, error: "", loading: false });
  useEffect(() => {
    const controller = new AbortController();
    setState({ path, data: null, error: "", loading: path !== null });
    if (path === null) return;
    const timer = setTimeout(() => {
      api<T>(path, { signal: controller.signal })
        .then((data) => {
          if (!controller.signal.aborted)
            setState({ path, data, error: "", loading: false });
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted)
            setState({
              path,
              data: null,
              error:
                error instanceof Error
                  ? error.message
                  : "Sensor request failed.",
              loading: false,
            });
        });
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [path, attempt, delay]);
  const matches = state.path === path;
  return {
    data: matches ? state.data : null,
    error: matches ? state.error : "",
    loading: path !== null && (!matches || state.loading),
    reload: () => setAttempt((value) => value + 1),
  };
}

export function base64Bytes(
  base64: string,
  size: number,
): Uint8Array<ArrayBuffer> {
  if (!Number.isSafeInteger(size) || size < 0 || size > 64 * 1024 * 1024)
    throw new Error("The decoded buffer exceeds the browser preview bound.");
  if (base64.length > Math.ceil(size / 3) * 4 + 4)
    throw new Error("The decoded buffer length differs from its descriptor.");
  const binary = atob(base64);
  if (binary.length !== size)
    throw new Error("The decoded buffer size differs from its descriptor.");
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

export async function verifyBuffer(
  bytes: Uint8Array<ArrayBuffer>,
  expected: string,
) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hash = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  if (hash !== expected) throw new Error("Decoded buffer checksum mismatch.");
}

export function readFloat32LE(bytes: Uint8Array<ArrayBuffer>): Float32Array {
  if (bytes.byteLength % 4 !== 0)
    throw new Error("Invalid float32 buffer length.");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const values = new Float32Array(bytes.byteLength / 4);
  for (let index = 0; index < values.length; index++) {
    values[index] = view.getFloat32(index * 4, true);
    if (!Number.isFinite(values[index]))
      throw new Error(
        "The normalized point buffer contains a nonfinite value.",
      );
  }
  return values;
}
