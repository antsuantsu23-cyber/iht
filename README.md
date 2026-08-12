# Team Feedback Hub PWA

This is the installable team version of the feedback tracker built from the ThingsToDo document.

## What this version is

It is a Progressive Web App, or PWA. After it is hosted online, team members open one HTTPS address in a browser and can install it to their Mac, Windows PC, iPhone, iPad, or Android device. They do not install Python or use Terminal.

The server itself uses Python and FastAPI. That runs on the hosting service, not on each team member's computer.

## Included

- Shared dashboard
- My Tasks, Active Tickets, All Tickets, and Completed views
- 41 imported tickets from the supplied ThingsToDo PDF
- Search and filters
- Create and edit feedback
- Assign and reassign team members
- Priority, status, area, and due dates
- Reproduction, expected behavior, and actual behavior fields
- Photos and document uploads
- Authenticated attachment downloads
- Related links
- Comments
- Activity history
- Root cause, impact, technical notes, PR and commit references
- Resolution tracking
- Admin, manager, team member, and viewer roles
- User creation, activation/deactivation, role changes, and password reset
- Personal password changes
- Installable PWA manifest and app icons
- Offline fallback page without caching private ticket data
- CSRF protection on data-changing forms
- Login rate limiting
- Secure-cookie mode in production
- Security headers
- Private upload storage
- 20 MB file limit and a restricted upload allowlist
- SQLite WAL mode for a small shared team deployment
- Docker deployment support

## Important production setup

Set these environment variables on the hosting service.

```text
APP_ENV=production
ADMIN_NAME=Robert
ADMIN_EMAIL=your-real-email@example.com
ADMIN_PASSWORD=a-unique-password-with-at-least-12-characters
SESSION_SECRET=a-long-random-secret
APP_DATA_DIR=/data
APP_UPLOAD_DIR=/data/uploads
```

`ADMIN_EMAIL` and `ADMIN_PASSWORD` are required in production. The sample `@example.com` assignee profiles remain visible so imported assignments are preserved, but their demo passwords are automatically invalidated in production. Update each profile with the real team member email and a new password from the Users page.

The hosting service should provide HTTPS and persistent storage mounted at `/data`. The database and uploads then survive restarts and deployments.

Use one application instance when using SQLite. For a larger organization, migrate the database to PostgreSQL and uploads to object storage.

## Installing after deployment

Open the hosted HTTPS address. In browsers that expose the PWA install prompt, use the Install app button inside Feedback Hub. On browsers that use their own menu, choose the browser's Install, Add to Dock, or Add to Home Screen option.

Everyone uses the same hosted application and shared data. Each person should have a separate account created from the Users page.

## Local developer preview

Local development still works with Python, but normal team members do not need this step.

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000
```

The local preview account is `robert@example.com` with password `welcome123`. These demo credentials are not enabled in production.
