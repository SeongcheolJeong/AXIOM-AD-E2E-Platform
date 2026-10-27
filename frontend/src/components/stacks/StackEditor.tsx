import { useState, type FormEvent } from "react";
import { Boxes, LoaderCircle } from "lucide-react";
import { api } from "../../api";
import { Modal } from "../ui";
import {
  componentRoles,
  roleLabels,
  type StackCreate,
  type StackDetail,
  type StackComponentMetadata,
  type ModuleSelection,
  type ComponentRole,
  type ParameterSchema,
  type StackKernelVersion,
} from "./StackTypes";
import {
  componentKernel,
  kernelAvailable,
  kernelLabels,
  stackKernels,
} from "./StackKernels";

function parameterShape(schema?: ParameterSchema) {
  return {
    schema: schema?.anyOf
      ? { ...schema, ...schema.anyOf.find((item) => item.type !== "null") }
      : schema,
    nullable: schema?.anyOf?.some((item) => item.type === "null") ?? false,
  };
}

type DraftModule = {
  id: string;
  version: string;
  parameters: Record<string, string>;
};
const toDraft = (selection: ModuleSelection): DraftModule => ({
  ...selection,
  parameters: Object.fromEntries(
    Object.entries(selection.parameters).map(([key, value]) => [
      key,
      value === null
        ? ""
        : typeof value === "string"
          ? value
          : JSON.stringify(value),
    ]),
  ),
});
function defaultModule(
  components: StackComponentMetadata[],
  role: ComponentRole,
  kernel: StackKernelVersion,
): ModuleSelection {
  const first = components.find(
    (item) =>
      item.role === role &&
      item.status === "available" &&
      componentKernel(item) === kernel,
  );
  return {
    id: first?.id ?? "",
    version: first?.version ?? "",
    parameters: first?.default_parameters ?? {},
  };
}
export default function StackEditor({
  stack,
  expectedRevision,
  components,
  onSaved,
  onClose,
}: {
  stack?: StackDetail;
  expectedRevision?: number;
  components: StackComponentMetadata[];
  onSaved: (stack: StackDetail) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(stack?.name ?? ""),
    [description, setDescription] = useState(stack?.description ?? ""),
    [note, setNote] = useState(stack?.note ?? ""),
    [frame, setFrame] = useState(stack?.definition.reference_frame ?? "ego");
  const [guard] = useState(expectedRevision ?? stack?.revision ?? 1);
  const [kernel, setKernel] = useState<StackKernelVersion>(
    stack?.kernel_version ?? "stack-kernel/1",
  );
  const [modules, setModules] = useState<Record<ComponentRole, DraftModule>>(
    () =>
      Object.fromEntries(
        componentRoles.map((role) => [
          role,
          toDraft(
            stack?.definition[role] ?? defaultModule(components, role, kernel),
          ),
        ]),
      ) as Record<ComponentRole, DraftModule>,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  function update(role: ComponentRole, key: string, value: string) {
    setModules((previous) => ({
      ...previous,
      [role]: {
        ...previous[role],
        parameters: { ...previous[role].parameters, [key]: value },
      },
    }));
  }
  function selectedMetadata(role: ComponentRole) {
    return components.find(
      (item) =>
        item.role === role &&
        componentKernel(item) === kernel &&
        item.id === modules[role].id &&
        item.version === modules[role].version,
    );
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const selections = Object.fromEntries(
        componentRoles.map((role) => {
          const metadata = selectedMetadata(role),
            draft = modules[role];
          if (!metadata || metadata.status !== "available")
            throw new Error(
              `Select an available ${roleLabels[role].toLowerCase()} component.`,
            );
          const parameters = Object.fromEntries(
            Object.entries(draft.parameters).map(([key, value]) => {
              const { schema, nullable } = parameterShape(
                metadata.parameter_schema.properties?.[key],
              );
              if (nullable && !value.trim()) return [key, null];
              if (schema?.type === "string") return [key, value];
              if (!value.trim())
                throw new Error(
                  `Provide ${roleLabels[role]} parameter ${key}.`,
                );
              const parsed: unknown = JSON.parse(value);
              if (typeof parsed === "number" && !Number.isFinite(parsed))
                throw new Error(`${key} must be finite.`);
              return [key, parsed];
            }),
          );
          return [role, { id: draft.id, version: draft.version, parameters }];
        }),
      ) as Record<ComponentRole, ModuleSelection>;
      const body: StackCreate = {
        name,
        description,
        note,
        definition: {
          ...(kernel === "stack-kernel/2"
            ? { schema_version: "2" as const, kernel_version: kernel }
            : { schema_version: "1" as const }),
          reference_frame: frame,
          ...selections,
        },
      };
      setBusy(true);
      onSaved(
        await api<StackDetail>(
          stack
            ? `/stacks/${encodeURIComponent(stack.id)}/revisions`
            : "/stacks",
          {
            method: "POST",
            body: JSON.stringify({
              ...body,
              ...(stack ? { expected_revision: guard } : {}),
            }),
          },
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Stack definition could not be published.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={stack ? "Create stack revision" : "Create component stack"}
      subtitle="Pin replaceable observation, policy, and control modules with their declared parameters."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="stack-editor-modal"
    >
      <form className="stack-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <label>
            Stack kernel
            <select
              aria-label="Stack kernel"
              value={kernel}
              onChange={(event) => {
                const next = event.target.value as StackKernelVersion;
                if (
                  !stackKernels.includes(next) ||
                  !kernelAvailable(components, next)
                )
                  return;
                setKernel(next);
                if (next === "stack-kernel/2") setFrame("ego");
                setModules(
                  Object.fromEntries(
                    componentRoles.map((role) => [
                      role,
                      toDraft(defaultModule(components, role, next)),
                    ]),
                  ) as Record<ComponentRole, DraftModule>,
                );
                setError("");
              }}
            >
              {stackKernels.map((value) => (
                <option
                  key={value}
                  value={value}
                  disabled={!kernelAvailable(components, value)}
                >
                  {kernelLabels[value]}
                  {kernelAvailable(components, value)
                    ? ""
                    : " · components unavailable"}
                </option>
              ))}
            </select>
            <small>
              Changing the kernel explicitly replaces all draft components and
              parameters with compatible defaults. Existing revisions remain
              unchanged.
            </small>
          </label>
          <div className="stack-form-grid two">
            <label>
              Stack name
              <input
                aria-label="Stack name"
                required
                maxLength={180}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              Reference frame
              <input
                aria-label="Stack reference frame"
                readOnly={kernel === "stack-kernel/2"}
                required
                maxLength={180}
                value={frame}
                onChange={(e) => setFrame(e.target.value)}
              />
              <small>
                {kernel === "stack-kernel/2"
                  ? "Kernel 2 uses the declared ego frame: "
                  : "User-declared ego frame: "}
                x forward, y left, z up. A matching frame name or rig transform
                does not certify axes or calibration.
              </small>
            </label>
          </div>
          <label>
            Description
            <textarea
              aria-label="Stack description"
              maxLength={4000}
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <div className="stack-module-editor">
            {componentRoles.map((role, index) => {
              const metadata = selectedMetadata(role),
                draft = modules[role];
              return (
                <section
                  key={role}
                  className="stack-module-fields"
                  data-testid={`stack-module-${role}`}
                >
                  <div className="stack-stage-heading">
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <h3>{roleLabels[role]}</h3>
                    <Boxes size={16} />
                  </div>
                  <label>
                    Component version
                    <select
                      aria-label={`Stack ${role} component`}
                      value={`${draft.id}\u0000${draft.version}`}
                      required
                      onChange={(e) => {
                        const selected = components.find(
                          (item) =>
                            item.role === role &&
                            `${item.id}\u0000${item.version}` ===
                              e.target.value,
                        )!;
                        setModules((previous) => ({
                          ...previous,
                          [role]: toDraft({
                            id: selected.id,
                            version: selected.version,
                            parameters: selected.default_parameters,
                          }),
                        }));
                      }}
                    >
                      {!metadata && (
                        <option value={`${draft.id}\u0000${draft.version}`}>
                          {draft.id
                            ? `${draft.id}/${draft.version} · unavailable`
                            : "Choose a component"}
                        </option>
                      )}
                      {components
                        .filter(
                          (item) =>
                            item.role === role &&
                            componentKernel(item) === kernel,
                        )
                        .map((item) => (
                          <option
                            key={`${item.id}/${item.version}`}
                            value={`${item.id}\u0000${item.version}`}
                            disabled={item.status !== "available"}
                          >
                            {item.name} · {item.version} · {item.status}
                          </option>
                        ))}
                    </select>
                  </label>
                  <div className="stack-parameter-grid">
                    {Object.entries(draft.parameters).map(([key, value]) => {
                      const { schema, nullable } = parameterShape(
                        metadata?.parameter_schema.properties?.[key],
                      );
                      const label = schema?.title ?? key.replaceAll("_", " ");
                      return (
                        <label key={key}>
                          {label}
                          {schema?.enum ? (
                            <select
                              aria-label={`Stack ${role} ${key}`}
                              value={value}
                              onChange={(e) =>
                                update(role, key, e.target.value)
                              }
                            >
                              {schema.enum.map((choice) => (
                                <option
                                  key={String(choice)}
                                  value={
                                    typeof choice === "string"
                                      ? choice
                                      : JSON.stringify(choice)
                                  }
                                >
                                  {String(choice)}
                                </option>
                              ))}
                            </select>
                          ) : schema?.type === "boolean" ? (
                            <select
                              aria-label={`Stack ${role} ${key}`}
                              value={value}
                              onChange={(e) =>
                                update(role, key, e.target.value)
                              }
                            >
                              <option value="true">true</option>
                              <option value="false">false</option>
                            </select>
                          ) : schema?.type === "number" ||
                            schema?.type === "integer" ? (
                            <input
                              aria-label={`Stack ${role} ${key}`}
                              required={!nullable}
                              type="number"
                              placeholder={
                                nullable ? "Unknown · leave blank" : undefined
                              }
                              min={schema.minimum}
                              max={schema.maximum}
                              step={schema.type === "integer" ? 1 : "any"}
                              value={value}
                              onChange={(e) =>
                                update(role, key, e.target.value)
                              }
                            />
                          ) : (
                            <input
                              aria-label={`Stack ${role} ${key}`}
                              required={Boolean(schema?.minLength)}
                              minLength={schema?.minLength}
                              maxLength={schema?.maxLength}
                              value={value}
                              onChange={(e) =>
                                update(role, key, e.target.value)
                              }
                            />
                          )}
                          {schema?.description && (
                            <small>{schema.description}</small>
                          )}
                          {nullable && (
                            <small>
                              Blank preserves an unknown value; no numeric
                              default is inferred.
                            </small>
                          )}
                        </label>
                      );
                    })}
                  </div>
                  {metadata && (
                    <details className="stack-disclosure">
                      <summary>Implementation & scope</summary>
                      <code>
                        {metadata.id}/{metadata.version}
                      </code>
                      {metadata.limitations.map((text) => (
                        <p key={text}>{text}</p>
                      ))}
                      <pre>
                        {JSON.stringify(metadata.implementation, null, 2)}
                      </pre>
                    </details>
                  )}
                </section>
              );
            })}
          </div>
          <label>
            Revision note
            <textarea
              aria-label="Stack revision note"
              required
              maxLength={4000}
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
        </fieldset>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <footer className="stack-form-footer">
          <p>
            {stack
              ? `Expected authoring head r${guard}. Existing replay snapshots keep their original definition.`
              : "A registered module and source-declared configuration do not establish vehicle safety or real-world performance."}
          </p>
          <div className="stack-actions">
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={
                busy ||
                componentRoles.some(
                  (role) => selectedMetadata(role)?.status !== "available",
                )
              }
            >
              {busy && <LoaderCircle size={14} className="spin" />}
              {busy
                ? "Publishing stack…"
                : stack
                  ? "Publish stack revision"
                  : "Publish stack"}
            </button>
          </div>
        </footer>
      </form>
    </Modal>
  );
}
