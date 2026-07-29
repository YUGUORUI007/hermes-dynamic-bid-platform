# Design QA — project workspace

## References checked

- Selected direction: `/Users/mac/.codex/generated_images/019fa7dd-0fe3-78f3-9470-249ead136731/exec-4752f83e-7135-485c-8945-63eb74eabca4.png`
- Desktop implementation: `../audit/repo-projects-desktop.png`
- Mobile implementation: `../audit/repo-projects-mobile.png`

## Visual match

- Preserved: the forest-green application bar, warm paper canvas, editorial serif hierarchy, restrained borders, and one authoritative project ledger.
- Intentionally changed: the reference’s extra “阶段分布” summary was removed. It repeated the project state already shown in each project row, which conflicts with the product rule that a project/node should not be presented twice under a different label.
- Responsive behavior: the 390px view keeps the information hierarchy, changes the summary to a two-column grid, and moves project navigation to the fixed bottom bar.

## Functional checks

- `/workspace` renders the unified project home.
- `/workspace/projects` redirects to `/workspace`, so no duplicate project-list destination remains.
- A project row navigates to its Hermes-backed detail page (`/projects/1`).
- Desktop and mobile screenshots were inspected after render.

## Findings

No remaining P0, P1, or P2 visual mismatches for the selected direction and the revised information architecture.
