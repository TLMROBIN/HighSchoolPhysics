# HighSchoolPhysics Visual Design System

This system deepens the current product style rather than replacing it. The
source of truth is the existing `highschoolphysics/assets/app.css`: quiet light
surfaces, teal primary actions, amber/red/green learning states, dense tables,
and graph-first student navigation.

## Design Tokens

- Color: `--color-primary` maps to the existing teal action color; semantic
  state tokens keep mastery, warning, error, success, and disabled states
  consistent across graph, cards, forms, and operations screens.
- Type: system Chinese UI stack, compact table labels at 13px, body copy at
  14-16px, dashboard numbers at 18-28px.
- Spacing: `--space-1` through `--space-8` define dense daily-use rhythm.
  Teacher/admin tables use tighter spacing; student cards keep more air.
- Shape and elevation: `--radius-control` for inputs/buttons,
  `--radius-card` for panels, `--shadow-card` for elevated login and job
  surfaces.
- Focus: `--focus-ring` is visible on buttons, form controls, graph nodes, and
  navigation cards.

## Component Inventory

- Buttons: primary teal, secondary white/teal, warning amber, disabled muted.
- Forms: field groups, JSON textareas, inline save forms, admin config panels.
- Cards and panels: `panel`, `wrong-card`, `runtime-health-card`,
  `provider-ops-panel`, `sso-settings-panel`, `pdf-export-panel`.
- Tables: dense teacher/admin data tables with sticky headers in scroll zones.
- Status: `status-chip`, `taxonomy-badge`, `provenance-badge`, mastery states.
- Operations: `job-timeline`, provider usage ledger, runtime health grid,
  SSO state records, PDF preview panels.
- Graph: teal default nodes, semantic mastery fills, amber focus, zoom detail
  states for classroom tablet use.

## Production Operations Screens

- Runtime health uses card status color to separate ready, disabled, missing,
  degraded, and failed capabilities.
- Provider operations show secret masks only, never raw key material. Budgets
  are displayed as daily calls, monthly cents, and per-call caps.
- OIDC SSO settings use the same admin form density as taxonomy management,
  with the binding policy visible next to provider metadata.
- PDF export jobs show type, status, file name, byte size, engine version, and
  failure reason when present.

## Page Templates

- Student: graph-first navigation, bottom tabs, mastery cards, wrong-book
  actions, redo history.
- Teacher: assessment operations first, question bank and parser/OCR queues
  second, analytics below.
- Admin: production readiness and identity/provider/PDF operations above
  ontology maintenance, because those decide whether production features are
  actually usable.

## Interaction States

- Hover keeps the quiet palette; focus uses the high-contrast teal ring.
- Low-confidence OCR and degraded provider checks use amber until a teacher or
  admin resolves them.
- Failed health checks and blocked budgets use red-soft backgrounds with clear
  reason text.
- Completed jobs use green accents and preserve audit metadata.

## Accessibility

- Keep focus visible on all keyboard-reachable controls.
- Avoid color-only meaning: status chips keep text labels.
- Dense tables must remain horizontally scrollable rather than squeezing text
  below readability.
- Graph nodes expose labels and remain keyboard focusable.

## Print And PDF Rules

- Hide navigation, buttons, and sticky status bars in print.
- Use A4 margins of roughly 16mm x 14mm.
- Preserve answers/analysis visibility according to export profile policy.
- PDF preview panels should show generation status, engine version, byte size,
  and a clear download affordance when a file is available.
