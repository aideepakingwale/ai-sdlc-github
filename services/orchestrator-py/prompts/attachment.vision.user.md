---
id: attachment.vision.user
version: 1
description: 'Attachment vision extraction: transcribe all visible text verbatim, then describe the image as software-requirements context.'
---
You are extracting software-requirements context from an uploaded image (a screenshot, diagram, whiteboard photo, or scanned document).
1. Transcribe ALL text visible in the image VERBATIM, preserving structure (lists, tables, labels, field names, numbers, IDs).
2. Then, under a 'Description:' heading, briefly describe any UI layout, diagram, flow, or data model shown and what it implies for the software.
Be faithful to the image; do not invent details that are not visible.
