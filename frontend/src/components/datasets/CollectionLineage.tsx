import { useSensorResource } from "../sensors/SensorData";
import { ErrorState } from "../ui";
import { DownloadLink } from "../ArtifactAccess";

export default function CollectionLineage({ datasetId, revision }: { datasetId: string; revision: number }) {
  const links = useSensorResource<{ items: { id: string; download_url: string; dataset: { revision: number };
    proposal: { collection_id: string; collection_revision: number; denominator: { included: number; excluded: number; collection_messages: number } } }[] }>(
      `/collection-datasets?dataset_id=${encodeURIComponent(datasetId)}`);
  return <>{links.error && <ErrorState message={links.error} retry={links.reload} />}
    {links.data?.items.map(link => <section className="panel dataset-summary" key={link.id} data-testid="dataset-collection-lineage">
      <h3>Collection provenance</h3><p><a href={`#collections?${new URLSearchParams({ collection: link.proposal.collection_id, revision: String(link.proposal.collection_revision) })}`}>Open source collection revision</a></p>
      <p>{link.proposal.denominator.included} included / {link.proposal.denominator.collection_messages} originally selected · {link.proposal.denominator.excluded} explicitly excluded.</p>
      <p>This receipt pins dataset r{link.dataset.revision}.{revision !== link.dataset.revision && ` You are viewing r${revision}; later annotation edits are not part of the original receipt.`}</p>
      <DownloadLink href={link.download_url} download>Download dataset curation receipt</DownloadLink>
    </section>)}</>;
}
