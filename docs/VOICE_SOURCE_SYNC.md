# Voice source sync — 2026-11-16

This branch preserves the reviewed Voice module, Vue components, isolated
scripts, and tests from the local YOSAKI workspace. It excludes databases,
secrets, real provider receipts, private conversation records, generated Vue
JavaScript, and the parallel Memory/H5 work.

The current product entry points in `backend/app/main.py` and
`frontend/src/App.vue` contain intertwined Voice and parallel Memory/H5
changes. Those shared files are intentionally not included here. This branch
is a **source snapshot for review**, not a deployable replacement for the
currently running local application. Integrating it with the shared entry
points requires a separate reviewed merge.

Daily Scribe budget behavior: the server reserves 15 seconds for each user
initiated recording. The available start count is the minimum of remaining
attempts, remaining connections, and whole 15 second reservations supported
by remaining audio capacity. The settings and chat UI display that count.
The isolated `tests.test_voice_scribe_daily` suite passed 11/11 for this
snapshot. No provider call is needed for these checks.
