---
version: 1
slug: "highschoolphysics-assets-learning-graph-js"
primary_target: "highschoolphysics/assets/learning-graph.js"
related_targets: ["highschoolphysics/assets/learning-graph.css","highschoolphysics/learning_graph.py","highschoolphysics/learning_regions.py","highschoolphysics/learning_views.py"]
---

# Knowledge atlas

Mode: Operate. Scope: existing teacher graph and student graph component, within the established HighSchoolPhysics design system. The user asks for a visually compelling, legible graph with ordered layout and easier exploration. The user confirmed curriculum-first browsing; preserve the hidden student navigation and the existing evidence rules.

## Direction contract

THESIS: Make the curriculum structure visible before showing individual evidence or editing forms. A textbook overview leads to a chapter, then a knowledge point and its explicit relationships.

OWN-WORLD: Inherit the existing light classroom surfaces, system Chinese type, pine green selection, and amber uncertainty. The canvas uses intentional branching lines and stable positions; selection traces the active neighborhood. No decorative image assets.

STORY: Choose a textbook or search all content; open a chapter; inspect a node, its directional relationships and source evidence; return by breadcrumb or textbook control. Teacher editing remains in a separate native disclosure.

FIRST VIEWPORT: Compact title and search toolbar above a textbook selector. A large readable branching curriculum diagram occupies the main area, with a contextual detail panel on desktop. At viewport widths of 600px or less, a labeled native textbook select replaces the button row and the duplicate overview breadcrumb is hidden. The first phone viewport shows the selected textbook, a complete first chapter, and their connecting branch. Mobile uses a vertical diagram and stacked details with an explicit jump. Search never forces a navigation before selection. A list alternative and view reset remain available.

FORM: Code-led refinement of existing graph component and its placement, not a replacement of the product identity. Seed key: not applicable to a scoped existing component. Signature move: the selected node becomes a stable center with incoming and outgoing branches, and its detail updates immediately.

FINISH: The independent finish review and verdict are recorded under `.impeccable/review/knowledge-atlas-20261015/`. F1–F3 are resolved and the local synthetic browser review has disposition `ship`. This ordinary extension preserves the existing `PRODUCT.md`, `DESIGN.md`, and `.impeccable/design.json`; its implementation details belong in this surface brief. No raster is used by the graph UI. Browser captures are review evidence with the synthetic-fixture provenance recorded in `docs/knowledge-atlas-20261015.md`. Final isolated release and remote verification remain separate gates.

## Implemented surface rules

- **Curriculum first.** The confirmed entry sequence is textbook → chapter → knowledge point → directional relationships and source evidence. Textbooks and chapters come from the enabled school catalogue and retain curriculum order. Teacher diagnosis editing and student learning evidence remain separate native disclosures.
- **Complete neighborhood.** The SVG height follows its displayed node rows in normal document flow. At the initial and reset scale the first and last displayed boxes remain complete; the map is not cropped into a fixed 500/560px inner viewport. Large neighborhoods still disclose their displayed/total count and a “show more” action; the list contains every direct neighbor. Zoomed content uses native scrolling.
- **Stable context and focus.** Deterministic placement keeps the current node at the center of its incoming/outgoing neighborhood. Keyboard activation from a textbook button, search result, breadcrumb or node link transfers focus to the selected SVG node in graph mode or the stable detail panel in list mode. The native textbook select retains its own focus. Replacing an activated source/return control with relationship or node detail transfers focus to the detail panel. Selected SVG nodes use the incumbent amber focus convention (`#704000`, 4px stroke); ordinary controls use a 3px pine-green outline with 3px offset.
- **Responsive task layout.** The desktop detail column is 290px, becomes 264px at 1100px or less, and stacks below the map at 1000px or less. Diagram geometry becomes vertical when its measured canvas is below 700px. Teacher module navigation collapses at 850px or less. The phone textbook select at 600px or less remains a native control with at least 44px height; detail/map jump actions are visible in the stacked layout.
- **Evidence and roles.** Directory containment does not assert a learning prerequisite. Solid/dashed lines, text statuses, direction and source details explain relationships. Node kind uses shape and text as well as color. Shared curriculum carries no personal observations; student evidence is scoped to the current student. Existing uncertain evidence is not converted into mastery. The student default remains the wrong book, and the graph remains absent from student module navigation while its explicit route remains available.

## Scoped visual variants

These describe this graph component, not new system-wide tokens. The existing white/cool-gray classroom surface, pine-green actions, system Chinese sans, flat panels, 6px controls and 8px panels remain the visual authority.

| Variant | Implemented values and use |
| --- | --- |
| Knowledge | Pine green `#08766f` and a square; selected nodes use pine fill with white labels. |
| Ability | Blue `#3a5b90`, pale label background `#e9eef8`, and a circle; this denotes node kind, not achievement. |
| Literacy | Purple symbol `#785389`, label text `#674274`, pale label background `#f1eaf6`, and a diamond; this denotes node kind, not achievement. |
| Objective | Amber symbol `#94611a` with a rounded-square shape; label text/background reuse `#704000` / `#fff2d6`. Pending review also retains explicit status text. |
| Interaction tones | Dark pine `#065f59` for selected hover and related label text; pale pine `#c6e8e0` for active controls. Search results use `#edf5f3`, placeholder text `#526477`, and disabled controls `#edf0f3`. |
| Type and shape | Node names are 16px; node metadata is 12px on the wide layout and 14px at 1000px or less. Detail titles use the existing 24px step, then 18px in the stacked layout. Fact counts and list headings use 18px; badges and list rows use 4px corners. |

The single detector pass reported advisory color variants and intermediate 20/22px type and 3px CSS radius values. The implementation now uses existing 18/24px type steps and 4px CSS radii for those cases; the detector was not rerun. Semantic node-kind and interaction tones above are deliberately local variants. No root palette, font family, global type scale or corner system was changed.

## Existing drift left outside this scope

`DESIGN.md` and its sidecar still describe four student navigation links including the graph; `student_learning.py` currently implements the user-confirmed three-link navigation and default wrong book. This pre-existing documentation drift is recorded without restoring the hidden entry or modifying the root system files. The root design document also predates the current canonical heading format. Neither discrepancy is canonized as a rule for this extension.
