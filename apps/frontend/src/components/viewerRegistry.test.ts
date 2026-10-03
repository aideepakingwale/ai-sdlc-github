// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { normalizeDataUri, renderDrawioSvg, sanitizeMermaid } from './viewerRegistry';

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


const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

describe('draw.io preview of generated (provider-icon) diagrams', () => {
  const GENERATED = `<mxfile provider="azure"><diagram name="Orders"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="title" value="&lt;b&gt;Orders&lt;/b&gt;&lt;br&gt;&lt;font color=&quot;#6B7280&quot; style=&quot;font-size:12px&quot;&gt;Target platform: Microsoft Azure&lt;/font&gt;" style="text;html=1;strokeColor=none;fillColor=none;fontSize=20;" vertex="1" parent="1"><mxGeometry x="40" y="30" width="400" height="60" as="geometry"/></mxCell>
<mxCell id="frame" value="Microsoft Azure" style="rounded=1;fillColor=#F3F9FD;strokeColor=#0078D4;strokeWidth=2;container=1;fontStyle=1;" vertex="1" parent="1"><mxGeometry x="200" y="100" width="600" height="300" as="geometry"/></mxCell>
<mxCell id="c_net" value="Virtual network" style="rounded=1;fillColor=none;strokeColor=#0078D4;dashed=0;container=1;" vertex="1" parent="frame"><mxGeometry x="30" y="50" width="400" height="200" as="geometry"/></mxCell>
<mxCell id="n_a" value="&lt;b&gt;Orders DB&lt;/b&gt;&lt;br&gt;&lt;font color=&quot;#6B7280&quot; style=&quot;font-size:10px&quot;&gt;Azure SQL Database&lt;/font&gt;" style="shape=image;image=data:image/png,${PNG_B64};verticalLabelPosition=bottom;html=1;" vertex="1" parent="c_net"><mxGeometry x="40" y="40" width="64" height="64" as="geometry"/></mxCell>
<mxCell id="n_b" value="API" style="shape=image;image=data:image/png,${PNG_B64};verticalLabelPosition=bottom;html=1;" vertex="1" parent="c_net"><mxGeometry x="250" y="40" width="64" height="64" as="geometry"/></mxCell>
<mxCell id="e1" value="SQL" style="edgeStyle=orthogonalEdgeStyle;strokeColor=#44546A;dashed=1;" edge="1" parent="1" source="n_b" target="n_a"><mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="560" y="204"/><mxPoint x="560" y="150"/><mxPoint x="270" y="150"/><mxPoint x="270" y="204"/></Array></mxGeometry></mxCell>
</root></mxGraphModel></diagram></mxfile>`;

  it('makes draw.io data URIs displayable (adds the base64 marker browsers require)', () => {
    expect(normalizeDataUri(`data:image/png,${PNG_B64}`)).toBe(`data:image/png;base64,${PNG_B64}`);
    expect(normalizeDataUri(`data:image/png;base64,${PNG_B64}`)).toBe(`data:image/png;base64,${PNG_B64}`);
    expect(normalizeDataUri('https://x/y.png')).toBe('https://x/y.png');
  });

  it('draws the embedded icons, nested containers (child coords relative to the parent), title and routed edge', () => {
    const { svg, error } = renderDrawioSvg(GENERATED);
    expect(error).toBeUndefined();
    const s = svg!;
    expect((s.match(/<image /g) ?? []).length).toBe(2);              // real icons, not coloured chips
    expect(s).toContain('href="data:image/png;base64,');
    expect(s).not.toContain('#ED7100');                              // no AWS-orange chip for an Azure diagram
    expect(s).toContain('Azure SQL Database');                       // muted official service name under the label
    expect(s).toContain('Target platform: Microsoft Azure');         // title block
    expect(s).toContain('Microsoft Azure');                          // cloud frame title
    expect(s).toContain('stroke-dasharray="7 4"');                   // async edge dashed
    expect(s).toContain('dio-arrow-44546A');                         // arrowhead in the edge colour
    // the icon sits at parent chain frame(200,100) + c_net(30,50) + (40,40) = (270,190) in page coordinates;
    // the viewBox starts at the title (40,30) minus the 24 padding → x = 270 - 40 + 24
    expect(s).toMatch(/<image [^>]*x="254" y="184"/);
  });

  it('routes through the generator\'s waypoints instead of a straight centre-to-centre line', () => {
    const s = renderDrawioSvg(GENERATED).svg!;
    const path = /<path d="(M [^"]+)" fill="none" stroke="#44546A"/.exec(s)![1]!;
    expect((path.match(/L /g) ?? []).length).toBeGreaterThanOrEqual(5);   // port + 4 waypoints + port
  });

  it('still shows native cloud stencils as brand-coloured chips', () => {
    const azure = SAMPLE.replace('mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.application_load_balancer',
      'shape=mxgraph.azure.application_gateway');
    expect(renderDrawioSvg(azure).svg).toContain('#0078D4');
  });
});
