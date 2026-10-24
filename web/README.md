# web/ (Owner D: Maximilian)

The app people talk to: an indoor Google Maps for any building. Phone first (map + bottom sheet), split view on
iPad and laptop (map + sidebar). 2D only, on purpose: floors are switched with a level picker, not drawn in 3D.

Vite + React + TypeScript + Tailwind v4 + zustand. Chat uses the Vercel AI SDK (`@ai-sdk/react`) against the
backend's `POST /buildings/{id}/chat`.

## Run

```bash
uv run uvicorn app.api.main:app --reload     # backend on :8000 (repo root)
cd web && npm install && npm run dev         # app on :5173, /api is proxied to :8000
```

Open http://localhost:5173/?b=<building-id> (defaults to `proc-factory-dev-003`). `npm run dev` listens on
the LAN too, so you can open it on your phone at http://<your-ip>:5173.

## Layout

| Path | What |
|---|---|
| `src/api/` | `client.ts` (fetch helpers), `types.ts` (Scene + view commands), `schema.d.ts` (generated: `npm run gen:types`) |
| `src/store.ts` | One zustand store: building, floor, mode (explore/ask), selection, highlight, route, sheet height |
| `src/lib/sceneIndex.ts` | Lookup tables over a scene (by id, by floor, things per room) |
| `src/components/map/` | SVG floor map, level picker, map controls |
| `src/components/browse/` | Search bar, category chips, place sheet, building picker |
| `src/components/chat/` | Chat with the building agent, "understood" chips, directions card, view sync |
| `src/components/layout/` | App shell: bottom sheet on phones, sidebar on md+ |

The contract with the backend is `src/app/building/view.py`: tool results carry `view` commands (`highlight`,
`route`, `focus_floor`, `scene_changed`) that the map follows. Until every tool sends them, the chat falls back
to highlighting the room and asset ids it finds in tool output.
