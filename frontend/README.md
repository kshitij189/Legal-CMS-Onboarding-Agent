# Onboarding dashboard

Next.js 15 + TypeScript dashboard for the Legal CMS Onboarding Agent: projects, schema upload and
inspection, mapping review and approval, execution with live status, verification reports and the
audit log. Every status and code is explained in plain language (`lib/explain.ts`).

The browser calls `/api/*`, which Next.js proxies to the backend (`BACKEND_URL`, default
`http://localhost:8200`), so the API needs no CORS setup.

```powershell
npm install
npm run dev                       # http://localhost:3000
npm run build; npx next start -p 3000
npx tsc --noEmit; npm run lint
```

Demo sign-in (synthetic): `a.admin` / `a.specialist`, password `demo-password`.
