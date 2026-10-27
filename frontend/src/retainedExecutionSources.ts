// Public source annotations for explicitly connected, immutable executions.
// Pins must match the versioned retained-execution-import/1 operator receipt.
// The execution API remains authoritative; a mismatched receipt gets no label.
export const retainedExecutionSourceRegistry = {
  schema_version: "retained-execution-source/1",
  source_label: "내부 CARLA 작업공간 → 공유 default · 보존 결과 연결",
  execution_receipts: {
    job_033d62571d38424d827e8a06d841525a: "51b70505f5fe31a25944518bea5514410895c1243ede46c24572e8722d5ed54c",
    job_59913285dc8d4e38afca789b72bcd0a3: "47741d3b5e42e830800e491e5d164504fc632603084a87abdc2cdf1fc280e056",
    job_715992f7f0f34b5199630f8196434ffd: "d5eb891fdf38dda2d2002c8b9035e18818518743c3239610cd9a36145114b80c",
  } as Record<string, string>,
};

export function retainedExecutionSource(id: string, manifestHash: string | null | undefined): string | null {
  return manifestHash && retainedExecutionSourceRegistry.execution_receipts[id] === manifestHash
    ? retainedExecutionSourceRegistry.source_label
    : null;
}
