import type {
  InferenceImage,
  InferenceInput,
} from "../perception/PerceptionTypes";

export type DatasetSplit = "train" | "validation" | "test" | "unspecified";
export type AnnotationSource = "human" | "synthetic" | "imported" | "unknown";
export interface DatasetImage {
  id: string;
  created_at: string;
  input: Omit<InferenceInput, "selection_index">;
  image: InferenceImage;
  recording_snapshot: Record<string, unknown>;
}
export interface DatasetAnnotation {
  id: string;
  label: string;
  bbox_xyxy: [number, number, number, number];
  iscrowd: boolean;
}
export interface DatasetMember {
  image_id: string;
  split: DatasetSplit;
  review_status: "unreviewed" | "reviewed";
  reviewer: string;
  review_note: string;
  annotation_source: AnnotationSource;
  annotations: DatasetAnnotation[];
}
export interface DatasetCreate {
  name: string;
  description: string;
  labels: string[];
  source: string;
  source_revision: string;
  license: string;
  note: string;
  images: DatasetMember[];
}
export interface DatasetStats {
  images: number;
  reviewed: number;
  unreviewed: number;
  annotations: number;
  negative_images: number;
  duplicate_image_groups: number;
  splits: Record<DatasetSplit, number>;
}
export interface DatasetDetail extends DatasetCreate {
  schema_version: "1";
  id: string;
  revision: number;
  created_at: string;
  label_map_hash: string;
  image_assets: DatasetImage[];
  stats: DatasetStats;
  manifest_hash: string;
  manifest_size_bytes: number;
  download_url: string;
}
