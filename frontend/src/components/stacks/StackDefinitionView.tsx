import { Boxes, GitBranch, Play } from "lucide-react";
import { formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { componentRoles, roleLabels, type StackDetail } from "./StackTypes";
export default function StackDefinitionView({
  stack,
  onEdit,
  onReplay,
}: {
  stack: StackDetail;
  onEdit: () => void;
  onReplay: () => void;
}) {
  return (
    <div
      className="stack-definition-view"
      data-testid="stack-definition-detail"
    >
      <section className="panel stack-definition-heading">
        <div className="stack-section-heading">
          <div>
            <span className="stack-eyebrow">
              IMMUTABLE DEFINITION · R{stack.revision}
            </span>
            <h2>{stack.name}</h2>
            <p>{stack.description || stack.note}</p>
          </div>
          <div className="stack-actions">
            <button type="button" className="button secondary" onClick={onEdit}>
              <GitBranch size={14} />
              Create stack revision
            </button>
            <button type="button" className="button primary" onClick={onReplay}>
              <Play size={14} />
              Replay recording
            </button>
          </div>
        </div>
        <div className="stack-definition-context">
          <span>Kernel {stack.kernel_version}</span>
          <span>Reference frame: {stack.definition.reference_frame}</span>
          <span>Published {formatDate(stack.created_at)}</span>
        </div>
      </section>
      <div className="stack-module-cards">
        {componentRoles.map((role, index) => {
          const selection = stack.definition[role],
            metadata = stack.component_metadata.find(
              (item) => item.role === role,
            );
          return (
            <section className="panel stack-module-card" key={role}>
              <div className="stack-stage-heading">
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{roleLabels[role]}</h3>
                <Boxes size={17} />
              </div>
              <h4>{metadata?.name ?? selection.id}</h4>
              <code>
                {selection.id}/{selection.version}
              </code>
              <dl>
                {Object.entries(selection.parameters).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>
                      {value === null
                        ? "Unknown · null"
                        : value === ""
                          ? "Not declared"
                          : typeof value === "object"
                            ? JSON.stringify(value)
                            : String(value)}
                    </dd>
                  </div>
                ))}
              </dl>
              {metadata && (
                <details className="stack-disclosure">
                  <summary>Captured implementation & limitations</summary>
                  {metadata.limitations.map((text) => (
                    <p key={text}>{text}</p>
                  ))}
                  <pre>{JSON.stringify(metadata.implementation, null, 2)}</pre>
                </details>
              )}
            </section>
          );
        })}
      </div>
      <section className="panel stack-definition-evidence">
        <p>{stack.note}</p>
        <div className="stack-hash">
          <span>Manifest SHA-256</span>
          <code>{stack.manifest_hash}</code>
        </div>
        <DownloadLink
          className="button secondary small"
          href={stack.download_url}
          download
        >
          Download stack definition
        </DownloadLink>
        <p className="stack-subtle">
          Modules are replaceable through the versioned stack contract. The
          configured corridor observation and control policy are bounded pilot
          logic, not a complete autonomous driving stack.
        </p>
      </section>
    </div>
  );
}
