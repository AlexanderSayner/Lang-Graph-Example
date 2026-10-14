# Graph view web client
Vite web client for Lang Graph back end

## Development
Start project
```bash
npm run dev
```
Will be available at http://localhost:5173  

## GraphQL endpoint

`src/constants.js` resolves the API endpoint at runtime instead of hardcoding it:

1. `http://<current origin>/graphql` - used when the frontend is served with a proxy in front of it
   (the vite dev server, or the nginx config in `nginx.conf`, which proxies to `spring-service:9191`);
2. `http://<current host>:9191/graphql` - fallback for plain static hosting without any proxy,
   the Spring backend allows credentialed CORS for that.

The first endpoint that really answers `application/json` is remembered for the rest of the session.
This way a static server that answers `POST /graphql` with the SPA `index.html` (the nginx
`try_files` fallback) no longer breaks the UI with `Unexpected token '<' ... "<!DOCTYPE ..."`.
If no endpoint answers, the raised error lists every URL that was tried.

Note: if the UI itself is served over HTTPS, the browser blocks the plain-HTTP fallback as mixed
content - in that case the `/graphql` proxy of the web server has to be used.

## Graph rendering

`app.jsx` keeps the sidebar selection and the canvas in sync:

- every graph open is a fresh canvas (`key = <graphId>#<seq>`) and it is mounted only once its nodes
  have arrived, so `fitView` is always calculated for the graph that is really displayed;
- the first open of a graph animates a fit (`fitViewOptions: padding 0.2, duration 250, maxZoom 1`),
  while returning to an already opened graph restores the viewport the user left (`viewportsRef`);
- out-of-order GraphQL answers are dropped (`loadSeqRef`), so rapid switching always ends up showing
  the last clicked graph;
- while loading, the canvas is replaced by a placeholder, and a failed load shows the error text with
  a Retry button instead of an empty white canvas;
- "Save Layout" stores the positions of the graph that is on the canvas (which may be a subgraph)
  and drops the remembered viewport of that graph.

Two buttons sit in the top right corner of the canvas:

- **Save Layout** - persists the current node positions;
- **Auto Layout** - re-arranges the nodes with the size-aware dagre layout (`layoutGraph`, which uses
  `estimateNodeSize` so the auto-sized nodes do not overlap), persists the result and refits the view.
  It starts from a readable zoom (~45%) because a long process graph can be thousands of pixels
  across - the fit button in the bottom left controls shows the full overview.
  The `↓ / →` switch next to the button picks the flow direction: **top → bottom** (`rankdir: 'TB'`,
  default) or **left → right** (`rankdir: 'LR'`, i.e. a horizontal process). In the horizontal
  mode the nodes are not vertically centred inside their column - every rank is aligned by the
  header, so all node headers of the graph start on one common line and the remaining nodes of a
  rank are stacked below it (`alignRanksByHeader` in `constants.js`).

Action feedback is shown in a toast in the top centre of the window (`components/Toast.jsx`, auto
dismisses after ~3.5 s, click to dismiss) - the blocking browser `alert()` popups are gone from both
the viewer and the builder. The `window.confirm` questions (delete graph, rewind) were kept on
purpose, because those need an explicit answer.

## Builder save

`builder.jsx` `handleSave()` sends the layout **inside** the `buildGraph` input as
`positions: [{ nodeId, x, y }]`, so the definition and the node coordinates are persisted in one
write (`GraphPostgresService.saveGraph()` derives the `coordinates` JSONB column from that list
instead of hardcoding `{}`). The same `saveGraphCoordinates` mutation is still fired right afterwards
as a redundant confirmation write - it is idempotent on the server, and it remains the path used by
the canvas buttons above, which save a layout without rebuilding the graph.
