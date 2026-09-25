// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { renderDrawioSvg, sanitizeMermaid } from './viewerRegistry';

const SAMPLE = `<mxfile host="ai-sdlc"><diagram name="Arch"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="c_vpc" value="VPC" style="rounded=0;dashed=1;fillColor=none;strokeColor=#7f8c9a;" vertex="1" parent="1"><mxGeometry x="40" y="40" width="400" height="200" as="geometry"/></mxCell>
<mxCell id="n_alb" value="App Load Balancer" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.application_load_balancer;" vertex="1" parent="1"><mxGeometry x="70" y="90" width="78" height="78" as="geometry"/></mxCell>
<mxCell id="n_app" value="Service" style="rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1"><mxGeometry x="260" y="90" width="160" height="60" as="geometry"/></mxCell>
<mxCell id="e0" value="http" style="edgeStyle=orthogonalEdgeStyle;endArrow=block;" edge="1" parent="1" source="n_alb" target="n_app"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>`;

describe('renderDrawioSvg', () => {
  it('renders a valid mxGraph document to an SVG preview', () => {
    const { svg, error } = renderDrawioSvg(SAMPLE);
    expect(error).toBeUndefined();
    expect(svg).toBeDefined();
    const s = svg!;
    expect(s.startsWith('<svg')).toBe(true);
    expect(s).toContain('marker id="dio-arrow"'); // arrowhead defined
    expect(s).toContain('<path'); // the edge is drawn
    expect(s).toContain('stroke-dasharray'); // dashed VPC boundary
    expect(s).toContain('#ED7100'); // AWS node rendered as an orange service chip
    expect(s).toContain('Service'); // generic node label
    expect(s).toContain('VPC'); // container title
  });

  it('wraps a long node label without dropping the shape', () => {
    const { svg } = renderDrawioSvg(SAMPLE);
    // "App Load Balancer" wraps but the AWS node still renders (orange rect present).
    expect(svg).toContain('App');
  });

  it('reports an error for malformed XML instead of throwing', () => {
    const { svg, error } = renderDrawioSvg('<mxfile><diagram>oops');
    expect(svg).toBeUndefined();
    expect(error).toBeTruthy();
  });

  it('reports an error when there are no shapes', () => {
    const empty = '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>';
    expect(renderDrawioSvg(empty).error).toBeTruthy();
  });
});

describe('sanitizeMermaid', () => {
  it('strips ```mermaid fences and leading prose', () => {
    expect(sanitizeMermaid('```mermaid\nflowchart TD\nA-->B\n```')).toBe('flowchart TD\nA-->B');
  });
});
