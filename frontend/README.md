# Dashboard chart bundle

This folder builds a single JavaScript file at `../static/charts.bundle.js` that
mounts MUI X Charts inside the Flask dashboard.

## One-time setup

Install Node.js LTS from https://nodejs.org/ then:

```bash
cd frontend
npm install
npm run build
```

That produces `../static/charts.bundle.js` (~600 KB) which the Flask page loads
via `<script src="/static/charts.bundle.js">`.

## Editing the charts

Source lives in `src/charts.jsx`. Make changes, then rebuild:

```bash
npm run build
```

Or run `npm run dev` to rebuild automatically on every save (watch mode).

## What the bundle exposes

After loading, it sets two globals on `window`:

- `window._mountMuiChart(id, type, props)` — mounts a chart of `type` (`'bar'` or
  `'pie'`) into the DOM element with the given `id`, using MUI X Charts props.
- `window._unmountMuiChart(id)` — tears down the React root for that element.

The Flask page's `guest_duplicates.html` calls these globals from its
`makeChart` helper, queueing calls if the bundle hasn't finished loading yet.
