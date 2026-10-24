# web/ (Owner D)

The UI. Stack is your call (Lovable, or Vite + React + three.js). Talks to the API only:
`GET /buildings`, `GET /buildings/{id}/scene`, `POST /buildings/{id}/chat` (Vercel AI SDK `useChat`).
Contract: `src/app/building/view.py`. Start the API with `uv run uvicorn app.api.main:app --reload`.
