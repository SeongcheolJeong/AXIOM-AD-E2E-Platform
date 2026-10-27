/** Bounded JSON with duplicate-key rejection and original sealed rig slices.
 * Keep number lexemes in the slice: Python canonical 0.0 is not JS canonical 0.
 */
export function parseSensorJson(text: string, driving = false) {
  if (text.length > (driving ? 8 : 4) * 1024 * 1024) throw new Error(`Sensor JSON exceeds its ${driving ? 8 : 4} MiB bound.`);
  let at = 0, nodes = 0;
  const rigSlices = new Map<string, string>();
  let preparedSlice = "";
  function fail(): never { throw new Error("Sensor JSON has invalid, duplicate or excessive structure."); }
  function space() { while (/[\t\n\r ]/.test(text[at] ?? "!") && at < text.length) at++; }
  function string(): string {
    if (text[at++] !== '"') fail();
    const start = at - 1;
    while (at < text.length) {
      const c = text[at++];
      if (c === '"') return JSON.parse(text.slice(start, at)) as string;
      if (c === "\\") { if (at >= text.length) fail(); at++; }
      else if (c.charCodeAt(0) < 32) fail();
    }
    return fail();
  }
  function value(path: string[], depth: number): unknown {
    if (++nodes > (driving ? 600000 : 300000) || depth > 64) fail();
    space(); const start = at; let result: unknown;
    if (text[at] === "{") {
      at++; space(); const object: Record<string, unknown> = Object.create(null), keys = new Set<string>();
      if (text[at] !== "}") while (true) {
        space(); const key = string(); if (keys.has(key)) fail(); keys.add(key); space(); if (text[at++] !== ":") fail();
        object[key] = value([...path, key], depth + 1); space(); if (text[at] !== ",") break; at++;
      }
      if (text[at++] !== "}") fail(); result = object;
    } else if (text[at] === "[") {
      at++; space(); const items: unknown[] = [];
      if (text[at] !== "]") while (true) { items.push(value([...path, String(items.length)], depth + 1)); space(); if (text[at] !== ",") break; at++; }
      if (text[at++] !== "]") fail(); result = items;
    } else if (text[at] === '"') result = string();
    else {
      const token = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(at));
      if (!token) fail(); at += token[0].length; result = JSON.parse(token[0]);
      if (typeof result === "number" && !Number.isFinite(result)) fail();
    }
    if (path.length === 4 && path[0] === "prepared" && path[1] === "input" && path[2] === "rigs") rigSlices.set(path[3], text.slice(start, at));
    if (path.length === 1 && path[0] === "prepared") preparedSlice = text.slice(start, at);
    return result;
  }
  const result = value([], 0); space(); if (at !== text.length) fail();
  return { value: result, rigSlices, preparedSlice };
}
