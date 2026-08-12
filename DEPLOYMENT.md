# Deployment checklist

The app is ready for a Docker-compatible web host.

1. Create one web service from this project or repository.
2. Give the service persistent storage and mount it at `/data`.
3. Set `APP_DATA_DIR=/data` and `APP_UPLOAD_DIR=/data/uploads`.
4. Set `APP_ENV=production`.
5. Set a real `ADMIN_EMAIL` and a unique `ADMIN_PASSWORD` of at least 12 characters.
6. Generate and set a long random `SESSION_SECRET`.
7. Expose the app through HTTPS.
8. Keep the service at one running instance while it uses SQLite.
9. Open `/health` after deployment. It should return `ok: true`.
10. Sign in as the production admin, create real accounts for the team, and reassign imported tickets as needed.
11. Install the PWA from the hosted address using the browser's Install or Add to Dock option.

The supplied source PDF and imported ticket database are included in this package. Private attachments are served only after login.
