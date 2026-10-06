---
id: attachment.vision.figure.user
version: 1
description: 'Describe one figure (diagram, chart, wireframe, scanned page, slide) taken from an uploaded document so its meaning can be used as requirements / design context.'
variables:
- document
- location
- caption
- surrounding
---
You are reading ONE figure from the uploaded document "${document}" (${location}). Caption / alt text: ${caption}. Nearby text: ${surrounding}.

Extract everything a software team needs from it, faithfully and without inventing anything that is not visible:
- TEXT: transcribe all visible text verbatim (labels, titles, legends, field names, numbers, IDs), keeping structure.
- DIAGRAM (architecture, flow, sequence, ER, state, network, org chart): list every component / actor / entity / step, then EVERY connection as "A -> B (label)" with its direction, grouping or swimlane boundaries, and any legend or numbered steps.
- CHART / GRAPH: type, axes and units, series, and the key values or trend.
- TABLE shown as an image: reproduce it as a Markdown table.
- UI MOCKUP / WIREFRAME: screens, fields, buttons, navigation and the user actions they imply.
- SCANNED PAGE: transcribe the page in reading order, keeping headings, lists and tables.
Finish with one or two sentences, headed "Meaning:", on what this implies for the software being specified. Be concise: no preamble, no restating these instructions.
