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
  a Retry button instead of an empty white canvas; the placeholder also names the situations where
  nothing is being loaded at all (the graph list still arriving, the list failing to load, no graphs
  existing yet, or no graph selected), so an empty canvas never claims to be loading;
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

`builder.jsx` `handleSave()` sends the layout **inside** every node of the `buildGraph` input as
`position: { x, y }` (`NodeInput.position`, input type `PositionInput`), so the definition and the node
coordinates are persisted in one write (`GraphPostgresService.saveGraph()` derives the `coordinates`
JSONB column from those per-node positions instead of hardcoding `{}`). There is no separate positions
list that could drift away from the nodes. The same `saveGraphCoordinates` mutation is still fired right
afterwards as a redundant confirmation write - it takes the flat `positions: [{ nodeId, x, y }]` pair, is
idempotent on the server, and remains the path used by the canvas buttons above, which save a layout
without rebuilding the graph.

## Document attachments

The chat panel can hand a run a file — an `.xlsx` or `.docx` — so a graph containing a
`DOCUMENT` node gets real rows instead of asking a model to pretend it read a spreadsheet:

- the 📎 file input sits in its own row (`components/ChatPanel.jsx`) and uploads the file the
  moment it is chosen to `POST /api/documents` (multipart with `credentials: 'include'`). The
  upload is REST rather than GraphQL on purpose — a binary body has no business in GraphQL
  JSON — so it needs the `/api` entry in `nginx.conf` and `vite.config.js`, both of which
  answer `{ documentId, filename, sizeBytes, sha256, expiresAt }`;
- each row is a **slot**: a `DOCUMENT` node of the loaded graph, named by its label (or its
  context key) and showing the variable its text lands in on hover. The slots come from
  `documentSlots(view)` in `constants.js`, built from the same raw `getGraphView` payload that
  carries each node's metadata, so "which file is which" is read from the **graph** rather
  than guessed. A graph with no `DOCUMENT` node gets the single default slot, so a file can
  still be picked and the warning below can explain that nothing will read it;
- the result is kept per slot in `attachments` state in `app.jsx` and rendered as a chip: the
  file name, an in-flight ⏳, or the reason the upload was refused (too large, wrong type)
  with a ✕ to remove it. Uploading at pick time rather than at run time is what makes that
  feedback arrive while the user still remembers which file it was about;
- a run sends `{ <refKey>: documentId, … }` on its `context` for every filled slot — the only
  channel the engine's `DOCUMENT` nodes read — and the attachments are cleared once the run
  finishes, so a follow-up message never silently re-attaches the same files;
- the references are **yours**: the server refuses one for any other account, and refuses one
  for an anonymous visitor, answering `DOCUMENT_NOT_FOUND` — so a reference the caller did not
  upload never reaches the engine. That check runs for every slot key, not just the default;
- a file reaches a model only through a `DOCUMENT` node, so a graph without one would accept
  the upload and then have nothing to read it — the model still sees the reference in its
  own context and answers that it has no access to the file. The panel therefore shows an
  amber warning next to the rows whenever a file is attached to a graph containing no
  `DOCUMENT` node, with a link to the builder for that graph (the graph's name travels in the
  link, because the builder falls back to "Untitled Graph" and would otherwise rebrand the
  graph on the next save);
- the panel also warns **before** the run about the one mistake it can see in the slots
  themselves: several `DOCUMENT` nodes reading the same context key. They fetch the same
  reference, so the graph reads one file twice and can never hold two — whichever second file
  you pick overwrites the first's reference. The fix is one field in the builder.

In the builder, a `DOCUMENT` node carries no prompt (it never calls a model): it is a slot —
its **name** (the label shown next to the 📎 picker and prefixed to its extracted text), the
**context key** holding the reference (`document_ref_key`, `document_ref` by default), the
**variable** its text lands in (`output_variable`, `document_text` by default) and the
**variable** for its metadata (`meta_variable`, `<output_variable>_meta` by default).

## Personal AI keys

The signed-in user can save their own provider keys so their runs bill their own account
(`components/LlmSettings.jsx`, opened from the **🔑 Keys** button in the sidebar footer or from
the chat's warning banner):

- the key goes browser -> server once, is encrypted there (AES-256-GCM, bound to user+provider)
  and is **never returned**; the list shows `maskedKey` (`••••xxxx`), which the server builds from a
  short hint stored at save time and can render without decrypting anything;
- `llmCredentials` is fetched into `app.jsx` so the UI knows *which* providers the account holds
  a key for. The browser never sees a key, so nothing about the CSP/session story changes.

Two warnings keep a subsidised run visible instead of silent (policy is FALLBACK by default, so
nothing is ever blocked):

1. **before a run** - `collectGraphProviders(getGraphView)` gives the providers the graph explicitly
   asks for (node metadata beats graph config, aliases normalised like the engine), and any of them
   the account has no key for produces *"⚠ No personal key for … these nodes will run on the shared
   service key"* above the composer, with an **Add keys** button. On a deployment running
   `PERSONAL_ONLY` the same banner turns red and says *"this run will be refused (the deployment
   spends personal keys only)"* instead: the wording comes from the session-free
   `llmBillingSettings` query (`serviceKeyForbidden`), because there the shared key would never pay;
2. **after a run / per answer** - `llmBilling` on `executeGraph` and `askCopilot` (`"user"` /
   `"service"` / `"mixed"`) renders *"⚠ Served by the shared service key"* under the message, so a
   run is never attributed to the wrong payer.

There are also two informational notes (blue and green, no button), for the case the two
warnings above cannot reach: a graph that names **no** provider anywhere. The engine is asked
for its default (`GetLlmSettings`) so the panel can say the provider out loud instead of
guessing it:

- **blue** when the default is *not* among the account's saved keys - *"This graph runs on the
  server default provider **yandex**; your saved keys (openrouter) don't cover it — the shared
  service key pays"* (or *"the run will be refused"* when neither the policy nor
  `LLM_SERVICE_KEY_ENABLED` allows the shared key). With an unreachable engine the provider is
  simply not named, and the wording stays conditional rather than promising something unknown;
- **green** when it *is* among them - *"runs on the server default provider **yandex**, and you
  hold a personal key for it — expect llmBilling: user below."*

Anonymous visitors never see the first warning: they have no account to hold a key in.
