import { useEffect, useRef } from 'react';
import {
  Background, Controls, MiniMap, ReactFlow, ReactFlowProvider,
  Handle, Position, MarkerType, useReactFlow, useNodesState, useEdgesState,
  type Node, type Edge, type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { StageConfig } from '../api/flow';

/** A predefined standard-SDLC stage for the palette (owned here so both the
 *  palette list and the canvas drop handler share one definition). */
export interface StagePreset {
  id: string; label: string; group: string; template: number; role: string;
  team: string[]; inputs: string[]; outputs: string[]; persona?: string;
}
export const STAGE_PALETTE: StagePreset[] = [
  { id: 'requirements', label: 'Requirements & Product', group: 'Discovery', template: 1, role: 'PO', team: ['PO'], inputs: ['requirements'], outputs: ['EPIC', 'FEATURE', 'USER_STORY', 'PRD'] },
  { id: 'architecture', label: 'Solution Architecture', group: 'Design', template: 2, role: 'SA', team: ['SA'], inputs: ['PRD'], outputs: ['HLD', 'ADR', 'ARCH_DIAGRAM'] },
  { id: 'design', label: 'Technical Design', group: 'Design', template: 3, role: 'TA', team: ['TA'], inputs: ['HLD'], outputs: ['LLD', 'OPENAPI', 'COMPONENT_DIAGRAM'] },
  { id: 'security', label: 'Security Review', group: 'Design', template: 7, role: 'SA', team: ['SA', 'TA'], inputs: ['HLD'], outputs: ['THREAT_MODEL', 'SECURITY_REVIEW'], persona: 'Security Architect' },
  { id: 'testing', label: 'Test Engineering', group: 'Quality', template: 4, role: 'QA', team: ['QA'], inputs: ['LLD'], outputs: ['TEST_STRATEGY', 'XRAY_TESTS', 'RTM'] },
  { id: 'implementation', label: 'Implementation & Delivery', group: 'Build', template: 6, role: 'DEV', team: ['DEV'], inputs: ['LLD'], outputs: ['APP_CODE', 'UNIT_TESTS', 'PULL_REQUEST'] },
  { id: 'cicd', label: 'CI/CD & Observability', group: 'Build', template: 5, role: 'DEVOPS', team: ['DEVOPS'], inputs: ['LLD'], outputs: ['GITHUB_ACTIONS', 'DOCKERFILE', 'GRAFANA_DASHBOARD'] },
  { id: 'uat', label: 'UAT Sign-off', group: 'Release', template: 7, role: 'QA', team: ['QA', 'PO'], inputs: ['APP_CODE'], outputs: ['UAT_SIGNOFF'], persona: 'UAT Lead' },
  { id: 'deployment', label: 'Deployment & Release', group: 'Release', template: 7, role: 'DEVOPS', team: ['DEVOPS'], inputs: ['APP_CODE'], outputs: ['DEPLOYMENT_PLAN', 'RELEASE_NOTES', 'ROLLBACK_PLAN'], persona: 'DevOps Engineer' },
  { id: 'maintenance', label: 'Maintenance & Monitoring', group: 'Operate', template: 7, role: 'DEVOPS', team: ['DEVOPS'], inputs: ['DEPLOYMENT_PLAN'], outputs: ['RUNBOOK', 'MONITORING_PLAN', 'SLO_REPORT'], persona: 'SRE' },
  { id: 'custom', label: 'Custom stage', group: 'Other', template: 7, role: 'DEV', team: ['DEV'], inputs: ['requirements'], outputs: ['ARTIFACT'], persona: 'Specialist' },
];

type StageNodeData = { title: string; tag: string; reviewers: string; issues: number };

/** A workflow stage rendered as a React Flow node, with input/output handles. */
function StageNode({ data, selected }: NodeProps) {
  const d = data as StageNodeData;
  return (
    <div
      className={`w-56 rounded-xl border-2 bg-white px-3 py-2 shadow-sm transition ${
        selected ? 'border-brand-500 ring-2 ring-brand-200'
          : d.issues ? 'border-red-300' : 'border-slate-200'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!h-3 !w-3 !border-2 !border-white !bg-slate-400" />
      <div className="flex items-center justify-between">
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-slate-500">{d.tag}</span>
        {d.issues > 0 && <span className="text-[10px] font-semibold text-red-500">⚠ {d.issues}</span>}
      </div>
      <div className="mt-1 truncate text-sm font-semibold text-slate-800" title={d.title}>{d.title || 'Untitled'}</div>
      <div className="mt-1 truncate text-[10px] text-brand-600" title={d.reviewers}>⭑ {d.reviewers}</div>
      <Handle type="source" position={Position.Right} className="!h-3 !w-3 !border-2 !border-white !bg-brand-500" />
    </div>
  );
}

const nodeTypes = { stage: StageNode };

interface Props {
  projectId: string;
  stages: StageConfig[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  onConnectDep: (from: string, to: string) => void;
  onDisconnectDep: (from: string, to: string) => void;
  onAddPreset: (preset: StagePreset) => void;
  onDeleteStage: (key: string) => void;
  getIssues: (s: StageConfig) => string[];
  templateShort: (t: number) => string;
}

type XY = { x: number; y: number };

function loadPositions(projectId: string): Record<string, XY> {
  try { return JSON.parse(localStorage.getItem(`wf-pos-${projectId}`) || '{}'); } catch { return {}; }
}

/** Auto-layout by dependency depth (columns) — used to place stages that don't
 *  yet have a saved position. */
function layoutByDepth(stages: StageConfig[]): Record<string, XY> {
  const byKey = new Map(stages.map((s) => [s.key, s]));
  const depthOf = new Map<string, number>();
  const seen = new Set<string>();
  const depth = (k: string): number => {
    if (depthOf.has(k)) return depthOf.get(k)!;
    if (seen.has(k)) return 0;
    seen.add(k);
    const deps = byKey.get(k)?.dependsOn ?? [];
    const d = deps.length ? Math.max(...deps.map(depth)) + 1 : 0;
    depthOf.set(k, d);
    return d;
  };
  stages.forEach((s) => depth(s.key));
  const rowInCol: Record<number, number> = {};
  const pos: Record<string, XY> = {};
  stages.forEach((s) => {
    const c = depthOf.get(s.key) ?? 0;
    const r = rowInCol[c] ?? 0;
    rowInCol[c] = r + 1;
    pos[s.key] = { x: c * 320, y: r * 160 };
  });
  return pos;
}

function CanvasInner(props: Props) {
  const { projectId, stages, selectedKey, onSelect, onConnectDep, onDisconnectDep, onAddPreset, onDeleteStage, getIssues, templateShort } = props;
  const rf = useReactFlow();
  // React Flow owns node/edge state so its change handler applies measurements
  // (without measured dimensions, edges can't compute endpoints and won't render).
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const posRef = useRef<Record<string, XY>>(loadPositions(projectId));
  const pendingDrop = useRef<XY | null>(null);

  // Track live positions from drags, and persist them per project.
  useEffect(() => {
    let changed = false;
    for (const n of nodes) {
      const p = posRef.current[n.id];
      if (!p || p.x !== n.position.x || p.y !== n.position.y) { posRef.current[n.id] = n.position; changed = true; }
    }
    if (changed) { try { localStorage.setItem(`wf-pos-${projectId}`, JSON.stringify(posRef.current)); } catch { /* ignore */ } }
  }, [nodes, projectId]);

  // Reconcile React Flow nodes to the stage list — preserving each node's live
  // position (and React Flow's measured size) so drags stick and edges route.
  useEffect(() => {
    setNodes((prev) => {
      const prevById = new Map(prev.map((n) => [n.id, n]));
      const missing = stages.filter((s) => !posRef.current[s.key] && !prevById.get(s.key));
      if (missing.length) {
        const layout = layoutByDepth(stages);
        for (const s of missing) {
          posRef.current[s.key] = pendingDrop.current ?? layout[s.key] ?? { x: 0, y: 0 };
          pendingDrop.current = null;
        }
      }
      return stages.map((s) => {
        const ex = prevById.get(s.key);
        const base = ex ?? { id: s.key, type: 'stage', position: posRef.current[s.key] ?? { x: 0, y: 0 } };
        return {
          ...base,
          id: s.key,
          type: 'stage',
          position: ex ? ex.position : (posRef.current[s.key] ?? { x: 0, y: 0 }),
          selected: s.key === selectedKey,
          // Explicit dimensions so edges route deterministically even before the
          // node's ResizeObserver reports a measured size (the node is fixed-width).
          width: 224,
          height: 92,
          data: {
            title: s.name,
            tag: templateShort(s.template),
            reviewers: (s.reviewerRoles?.length ? s.reviewerRoles : [s.reviewerRole]).join('/'),
            issues: getIssues(s).length,
          } satisfies StageNodeData,
        } as Node;
      });
    });
  }, [stages, selectedKey, getIssues, templateShort, setNodes]);

  // Reconcile edges from dependsOn.
  useEffect(() => {
    setEdges(stages.flatMap((s) => (s.dependsOn ?? []).map((dep) => ({
      id: `${dep}->${s.key}`, source: dep, target: s.key,
      markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18, color: '#94a3b8' },
      style: { stroke: '#94a3b8', strokeWidth: 1.5 },
    }))));
  }, [stages, setEdges]);

  return (
    <div className="h-full w-full"
      onDrop={(e) => {
        e.preventDefault();
        const id = e.dataTransfer.getData('application/wf-preset');
        const preset = STAGE_PALETTE.find((p) => p.id === id);
        if (!preset) return;
        pendingDrop.current = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
        onAddPreset(preset);
      }}
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={(_e, n) => onSelect(n.id)}
        onConnect={({ source, target }) => { if (source && target) onConnectDep(source, target); }}
        onEdgesDelete={(eds) => eds.forEach((ed) => onDisconnectDep(ed.source, ed.target))}
        onNodesDelete={(nds) => nds.forEach((n) => onDeleteStage(n.id))}
        fitView
        minZoom={0.2}
        maxZoom={1.75}
        proOptions={{ hideAttribution: true }}
        className="bg-[radial-gradient(#e2e8f0_1px,transparent_1px)] [background-size:18px_18px]"
      >
        <Background gap={18} color="#e2e8f0" />
        <Controls />
        <MiniMap pannable zoomable className="!bg-white" />
      </ReactFlow>
    </div>
  );
}

/** React Flow canvas for the workflow: smooth drag, free node placement (saved
 *  per project), standard pan/zoom, connect by dragging handles, disconnect by
 *  selecting an edge and pressing delete, and drop palette presets anywhere. */
export default function WorkflowCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}
