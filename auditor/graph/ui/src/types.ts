export type NodeType = "class" | "function" | "method" | "module";

export interface GNode {
  id: string;
  label: string;
  type: NodeType;
  lang: string;
  module: string;
  path: string;
  line: number;
  rank: number;
  cluster: number | null;
  role: string;
  findings: string[];
  refined?: boolean;
  annotation?: string | null;
}

export interface GEdge {
  source: string;
  target: string;
  kind: string;
  weight: number;
  provenance?: string;
  confirmed?: boolean;
}

export interface GCluster {
  cluster_id: number;
  label: string;
  member_count: number;
  agg_rank: number;
}

export interface GraphPayload {
  meta: { theme: string; accent: string; node_cap: number; repo?: string };
  clusters: GCluster[];
  nodes: GNode[];
  edges: GEdge[];
}
