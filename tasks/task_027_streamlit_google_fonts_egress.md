# Task 027 — Stop the Streamlit GUI from reaching out to fonts.googleapis.com

**Severity:** MEDIUM (cosmetic on graceful failure; but contradicts the
"air-gapped" claim made by the very file that ships the workaround.)
**Feature:** G3 (no egress on golden paths)
**Air-gap relevance:** **Yes.** On a `<Networking>Disable>` sandbox the
browser will fail the CSS @import silently; system fonts are substituted.

## Reproduction

`app/web/batch_gui/style_utils.py:3` declares:
```python
# Embedded SVG Icons (Air-Gapped / Zero-Dependency)
ICONS = { ... }
```
Twenty-three lines later, `inject_custom_css` ships this to every Streamlit
page:
```python
st.markdown(
    """
    <style>
    /* Modern Typography & Spacing Overrides */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Space+Grotesk:wght@500;700&display=swap');
    ...
""")
```

When the GUI loads inside the sandbox, the browser sends a request to
`fonts.googleapis.com`. With `<Networking>Disable>` the request fails after
a TCP timeout; Streamlit's "Modern Typography & Spacing" intent is silently
unfulfilled. The headline font (Space Grotesk) and body font (Inter) never
load -- the page is rendered in the browser's default sans-serif.

`grep` of the egress audit (`tests/audit_threads/harness_02_*.py`)
confirms this is the **only** non-localhost / non-env-configured outbound
reference in `app/`.

## Root cause

`@import url(...)` to a public CDN inside an `<style>` block injected via
`st.markdown`. The CSS the @import is trying to pull in just declares
`@font-face` rules referencing further Google Fonts URLs. No part of this is
recoverable on an air-gap deploy.

## Required behavior

Either (a) bundle the fonts as static assets in `app/web/batch_gui/assets/`
(.woff2 files plus a hand-written `@font-face`), or (b) drop the @import
entirely and let the browser default fonts (Segoe UI on Windows) do the
job. The comment on `style_utils.py:3` already labels this module
"air-gapped" -- option (b) is the lightest fix that matches the stated
intent.

## TDD plan

RED -- `tests/test_task_027.py`:

1. Import `app.web.batch_gui.style_utils` and call `get_icon("bolt")` so the
   module loads without crashing.
2. Read the source file as text; assert no occurrence of
   `https://fonts.googleapis.com`, no occurrence of `https://fonts.gstatic.com`,
   and no `@import url(http`. Fails today on line 26.
3. (Stretch) Generate the page CSS string the way `inject_custom_css` would
   (capture via patching `st.markdown` to a list-append) and assert the
   captured CSS contains no `http://` or `https://`.

GREEN -- option (b):

- Delete the `@import` line and any `font-family: 'Inter' / 'Space Grotesk'`
  references (or wrap them as fallback after `system-ui, -apple-system,
  "Segoe UI", sans-serif`). Verify visually that the page still looks
  reasonable.

OR option (a):

- Add `app/web/batch_gui/assets/fonts/{Inter, SpaceGrotesk}-{Regular, SemiBold, Bold}.woff2`
- Write a hand-rolled `@font-face` block in `style_utils.py` pointing at
  data: URLs (preferred for Streamlit) or a Streamlit static-file mount.
- Confirm the bundled font files are referenced via relative paths only.

## Acceptance criteria

- [ ] `style_utils.py` contains no `https://fonts.googleapis.com` and no
      `https://fonts.gstatic.com` literal.
- [ ] The Streamlit GUI loads without making any outbound HTTP request
      observable from a packet sniffer / `netstat` (manual verification on
      the dev box is sufficient).
- [ ] Module docstring/comment claim "Air-Gapped / Zero-Dependency" is
      truthful.
- [ ] Full `pytest` suite green.
- [ ] ASCII-only test code/messages.

## Constraints for the implementer (Sonnet)

TDD only. No destructive ops. Fix exactly this defect -- do not rework the
rest of `style_utils.py`'s styling. Behavior identical on Python 3.12 and
3.14.
