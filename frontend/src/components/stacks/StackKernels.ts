import {
  componentRoles,
  type StackComponentMetadata,
  type StackDefinition,
  type StackDetail,
  type StackKernelVersion,
} from "./StackTypes";

export const stackKernels: StackKernelVersion[] = [
  "stack-kernel/1",
  "stack-kernel/2",
];
export const kernelLabels: Record<StackKernelVersion, string> = {
  "stack-kernel/1": "Legacy geometric pilot · kernel 1",
  "stack-kernel/2": "Sample grid and stopping plan · kernel 2",
};
export function componentKernel(
  item: StackComponentMetadata,
): StackKernelVersion | null {
  const kernel =
    item.implementation && Object.hasOwn(item.implementation, "kernel")
      ? item.implementation.kernel
      : "stack-kernel/1";
  return stackKernels.includes(kernel as StackKernelVersion)
    ? (kernel as StackKernelVersion)
    : null;
}
export function definitionKernel(
  definition: StackDefinition,
): StackKernelVersion | null {
  if (definition?.schema_version === "1") return "stack-kernel/1";
  if (
    definition?.schema_version === "2" &&
    definition.kernel_version === "stack-kernel/2"
  )
    return "stack-kernel/2";
  return null;
}
export function kernelAvailable(
  catalog: StackComponentMetadata[],
  kernel: StackKernelVersion,
) {
  return componentRoles.every((role) =>
    catalog.some(
      (item) =>
        item.role === role &&
        item.status === "available" &&
        componentKernel(item) === kernel,
    ),
  );
}
export function stackAvailable(
  stack: StackDetail,
  catalog: StackComponentMetadata[],
) {
  const kernel = definitionKernel(stack.definition);
  return (
    !!kernel &&
    kernel === stack.kernel_version &&
    componentRoles.every((role) => {
      const selection = stack.definition[role];
      return catalog.some(
        (item) =>
          item.id === selection.id &&
          item.version === selection.version &&
          item.role === role &&
          item.status === "available" &&
          componentKernel(item) === kernel,
      );
    })
  );
}
