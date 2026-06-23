import { useEffect, useRef } from "react";
import ForceGraph3D from "3d-force-graph";
import { Raycaster, Vector2, type Object3D } from "three";
import type { GraphPayload } from "../types";
import { NODE_COLOR, THEME } from "../theme";
import { STRUCTURAL_KINDS } from "../graph/buildGraph";

interface Graph3DProps {
  payload: GraphPayload;
  onSelect: (nodeId: string) => void;
  onBackground: () => void;
}

/** A force-graph node datum after layout — carries id + settled 3D coords. */
type NodeDatum = { id?: string | number; x?: number; y?: number; z?: number } | null;
type GraphObj3D = Object3D & { __graphObjType?: string; __data?: NodeDatum };

/** POC: render the codebase graph in 3D (three.js via 3d-force-graph). Force-directed in 3D
 * space; node colour = type, size = rank, directional arrows = edge direction.
 *
 * Perf scales with graph size — real graphs run ~3k nodes / ~30k edges. The traps:
 *  - linkWidth > 0 renders every link as its own cylinder mesh (30k draw calls → frozen).
 *    linkWidth 0 batches the whole link set into ONE GL-line draw call.
 *  - a directional arrow is a cone mesh PER link — only affordable when links are few.
 *  - the force sim defaults to ~15s of churn; bound it so the layout settles fast.
 * So we size every knob off the link count rather than a fixed config.
 *
 * Clicking: 3d-force-graph's built-in onNodeClick is suppressed by ANY mouse movement during
 * the press (clickAfterDrag=false, no movement threshold in three-render-objects). So we run
 * our OWN raycast on pointer-up (with a small movement tolerance to tell a click from an
 * orbit/pan), and on hit, fly the camera to the node + select it — clicking a node visibly
 * focuses it instead of doing nothing on the canvas. */
export default function Graph3D({ payload, onSelect, onBackground }: Graph3DProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onBackgroundRef = useRef(onBackground);
  onBackgroundRef.current = onBackground;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const nodes = payload.nodes.map((n) => ({
      id: n.id,
      label: n.label,
      color: NODE_COLOR[n.type] ?? "#888888",
      val: 1 + Math.sqrt(Math.max(n.rank, 0)) * 26,
    }));
    const links = payload.edges
      .filter((e) => STRUCTURAL_KINDS.has(e.kind))
      .map((e) => ({ source: e.source, target: e.target }));

    const linkCount = links.length;
    const showArrows = linkCount <= 1500; // one cone mesh each — only cheap when few
    const big = linkCount > 4000;

    const fg = new ForceGraph3D(el, { controlType: "orbit" })
      .backgroundColor(THEME.bgCanvas)
      .showNavInfo(false)
      .graphData({ nodes, links })
      .nodeLabel("label")
      .nodeColor("color")
      .nodeVal("val")
      .nodeRelSize(6) // bigger spheres → tractable click/hover targets at overview zoom
      .nodeResolution(big ? 6 : 8)
      .nodeOpacity(big ? 1 : 0.9) // opaque on big graphs → no transparency sort cost
      .linkColor(() => "#7C7CFF")
      .linkOpacity(0.22)
      .linkWidth(0) // batched GL lines (1 draw call), NOT per-link cylinder meshes
      .linkDirectionalArrowLength(showArrows ? 2.5 : 0)
      .linkDirectionalArrowRelPos(1)
      .enableNodeDrag(false) // orbit-only; dragging a node re-heats the sim every frame
      .warmupTicks(big ? 0 : 40) // pre-settle small graphs; don't block mount on big ones
      .cooldownTicks(big ? 80 : 200) // bound the live sim instead of ~15s of churn
      .d3AlphaDecay(big ? 0.05 : 0.0228) // converge faster on big graphs
      .width(el.clientWidth)
      .height(el.clientHeight)
      .onNodeHover((node: NodeDatum) => {
        el.style.cursor = node ? "pointer" : "";
      });

    // OrbitControls: dolly toward the point under the cursor on scroll, not scene-centre.
    (fg.controls() as { zoomToCursor?: boolean }).zoomToCursor = true;

    /** Fly the camera in to centre on a node, then select it. */
    const focusNode = (node: NonNullable<NodeDatum>) => {
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const z = node.z ?? 0;
      const dist = Math.max(Math.hypot(x, y, z), 1);
      const ratio = 1 + 180 / dist; // pull in to a fixed standoff from the node
      fg.cameraPosition({ x: x * ratio, y: y * ratio, z: z * ratio }, { x, y, z }, 800);
      if (node.id != null) onSelectRef.current(String(node.id));
    };

    // Own click detection + raycast (see header note). A press-release within CLICK_SLOP px
    // is a click; anything larger was an orbit/pan and is ignored.
    const CLICK_SLOP = 5;
    const raycaster = new Raycaster();
    const ndc = new Vector2();
    let downX = 0;
    let downY = 0;

    const nodeAt = (clientX: number, clientY: number): NonNullable<NodeDatum> | null => {
      const rect = el.getBoundingClientRect();
      ndc.set(
        ((clientX - rect.left) / rect.width) * 2 - 1,
        -((clientY - rect.top) / rect.height) * 2 + 1
      );
      raycaster.setFromCamera(ndc, fg.camera());
      const hits = raycaster.intersectObjects(fg.scene().children, true);
      for (const hit of hits) {
        let o: GraphObj3D | null = hit.object as GraphObj3D;
        while (o && o.__graphObjType !== "node") o = o.parent as GraphObj3D | null;
        if (o && o.__data && o.__data.id != null) return o.__data;
      }
      return null;
    };

    const onPointerDown = (e: PointerEvent) => {
      downX = e.clientX;
      downY = e.clientY;
    };
    const onPointerUp = (e: PointerEvent) => {
      if (e.button !== 0) return;
      if (Math.hypot(e.clientX - downX, e.clientY - downY) > CLICK_SLOP) return;
      const node = nodeAt(e.clientX, e.clientY);
      if (node) focusNode(node);
      else onBackgroundRef.current();
    };
    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("pointerup", onPointerUp);

    const onResize = () => fg.width(el.clientWidth).height(el.clientHeight);
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      el.removeEventListener("pointerdown", onPointerDown);
      el.removeEventListener("pointerup", onPointerUp);
      fg._destructor();
      el.replaceChildren();
    };
  }, [payload]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}
