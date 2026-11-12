# Frontend regression checks

Run `npm run test:ui` in `web` for frontend checks without Python. Run `node scripts/check_frontend.mjs` from the repository root for all 32 checks. The full suite includes three Python integration checks and requires `.venv/Scripts/python.exe`. The runner writes failure details to `logs/frontend-check-failures.log`.
