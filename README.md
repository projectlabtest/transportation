# LHC Transport Booking

Flask transportation booking app with coordinator review, fleet assignment, and driver trip tracking. The existing server-rendered app is also an installable Progressive Web App (PWA) for Android.

## Local development

1. Install Python 3.10 or later.
2. From this folder, create/activate a virtual environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   py -m pip install -r requirements.txt
   ```

3. Run `py app.py` and open `http://127.0.0.1:5001`.

The first run creates the SQLite database in this folder and seeds demo accounts, including `admin` / `admin123`. Change seeded passwords before using real data.

## Shared server deployment

Run one app instance behind a reverse proxy with HTTPS. PWA installation on Android requires HTTPS (except for localhost); the proxy should preserve the `/service-worker.js` path and serve the app on its root URL.

Set these environment variables in the service manager:

- `TRANSPORT_ENV=production`
- `TRANSPORT_SECRET_KEY` to a long, randomly generated value. The app refuses production startup without it.
- `TRANSPORT_DB_PATH` to an absolute path on persistent server storage, for example `C:\ProgramData\LHCTransport\transport.db` or `/var/lib/lhc-transport/transport.db`. The parent directory is created if needed; include the database in regular backups.

Install `requirements.txt`, then initialize and serve the app using Waitress, for example:

```powershell
waitress-serve --listen=127.0.0.1:5001 wsgi:app
```

Keep the database on durable storage and run a single app instance while using SQLite. Do not put the database on an ephemeral deployment filesystem or share one SQLite file between multiple app hosts. On first initialization, the existing seed routine creates demo accounts; immediately replace their passwords and remove any accounts that should not be active.

Deactivated accounts remain in the database so existing booking and activity history stays intact. The admin Users page hides them by default; use **Show Deactivated Users** to review or reactivate one.

## Android installation and offline behavior

Open the HTTPS app URL in Chrome on Android and choose **Install app** or **Add to Home screen** from the browser menu. The PWA uses the same login and role permissions as the web app. The service worker caches only the stylesheet, manifest, and app icon. Booking pages, account data, and all actions require a network connection and are never cached for offline access.

## Native Android app and PC access

The separate Kotlin Android app is in `driver-app/` and supports active driver, requester, and coordinator/admin accounts. Drivers see assigned trips and can accept, start, mark on-the-way, confirm arrival/pickup, and complete trips (odometer readings are recorded by the guard). Requesters can submit, view, and cancel their own bookings. Coordinators/admins can review the queue, approve/reject with a reason, and assign or reassign vehicles and drivers. The existing Flask web app remains available on PC with its existing role-based pages, including the coordinator print-ticket workflow; both clients use the same server and booking database.

Open `driver-app/` in Android Studio with Android SDK 34 and JDK 17 installed, let Gradle sync, then build/install the `app` debug variant or use **Build > Generate App Bundles or APKs > Build APK(s)**. On Windows, the project also includes a Gradle wrapper; from `driver-app/`, run `.\gradlew.bat assembleDebug`. The APK is written to `app/build/outputs/apk/debug/app-debug.apk`. Enter the deployed **HTTPS** Flask URL on the sign-in screen. Cleartext HTTP is intentionally blocked by Android.

After building the APK on the server, download it from the web app's **DOWNLOAD ANDROID APP** link or directly at `/download/android`. Open the web app's HTTPS address on the Android phone and tap the link; the APK is served as `LHC-Transport.apk`. The phone must be able to reach that server, and the APK must be present at the build output path above. For a local/LAN deployment, use the server's reachable LAN address and configure HTTPS; `127.0.0.1` on the phone refers to the phone itself, not the PC.

For a temporary public test without a domain, run `deployment/remote-server/START-PUBLIC-TEST-TUNNEL.cmd` on the separate server desktop. It verifies Cloudflare's signed `cloudflared` binary, secures the seeded demo accounts with random passwords, creates a test requester, and opens an ephemeral HTTPS tunnel after confirmation. Traffic is relayed through Cloudflare; use dummy data only. The generated APK URL and test credentials are written to `PUBLIC-TEST-ACCESS.txt`. Stop the tunnel with `STOP-PUBLIC-TEST-TUNNEL.cmd`; the generated URL is temporary and changes on each run.

The Flask app provides `/api/mobile/login`, `/api/mobile/logout`, `/api/mobile/my-trips`, `/api/mobile/requester/bookings`, and `/api/mobile/coordinator/*` endpoints. Login issues a random bearer token valid for 30 days; only its SHA-256 hash is stored in SQLite. The app keeps the access token in memory for the active session and revokes it on sign-out. Deactivated accounts are denied access. Keep the server URL on HTTPS: bearer tokens must not be sent over an untrusted network.

Trip odometer readings and the Time OUT / Time IN stamps are recorded by a **guard** account in the Approval Queue. The guard sees only the trip number, vehicle type, plate number, and driver; the guard enters the **start odometer** and approves the trip at the gate only when the assigned vehicle matches the type the trip needs. The **end odometer** on return stamps Time IN and computes the distance. The old SSD gate GPS auto-detection has been removed.

## Coordinator booking flow

New requests begin as **Pending**. A **guard** account can approve them to **Approved** (approval queue only), while a coordinator/admin may also approve, reject them with a required reason, or assign a vehicle and driver after approval. The booking activity log records each decision and who made it. Drivers continue to accept, start, and complete assigned trips through their existing account.

After accepting an assignment, the driver can separately confirm **Client Pickup** from My Trips. Coordinators see the pickup state and timestamp in the booking queue; an open queue updates automatically when pickup is confirmed. The pickup action is restricted to the assigned driver and is recorded in the booking activity log.

Coordinators/admins can use **Print Ticket** in the booking queue or **Print Trip Ticket** on booking details to print the trip ticket on the **top half of an A4 page** (210 x 148.5 mm after cutting crosswise; 12 mm top, 6 mm side, 10 mm bottom margins). These dimensions are locked by `tests/test_ticket_layout.py`. It fills in booking details, assigned driver/vehicle, and recorded odometer readings. When recorded, SSD gate **Time OUT** and **Time IN** are printed with map links on screen; nature-of-trip selection, department/account, and signatures remain for completion on paper.

Pickup and drop-off addresses link to Google Maps directions. When both addresses are present, the app also provides a direct pickup-to-drop-off route link. These are standard Google Maps URLs that open the Maps app when available and do not require an API key.

After accepting the trip, the driver may mark **Driver on the way**. Clients see that status and its timestamp in My Bookings and booking details; their page updates through the live status polling. This is a driver-reported status, not GPS tracking or a live ETA.

The driver can then confirm **Arrived at pickup** and separately confirm **Client picked up**. Both events are timestamped and logged. Coordinators see the arrival time and pickup state/time in the booking queue; clients see the corresponding updates in their booking list and detail page.

The guard account approves the trip at the gate in the **Approval Queue**. The queue lists only the trip number, vehicle type, plate number, and driver. If the assigned vehicle type does not match the trip requirement, the row is flagged and **cannot be approved**. Approving records the **start odometer** and stamps **Time OUT**; the approving guard is printed under **Approved by** on the trip ticket. **End Odo** on return stamps **Time IN** and computes the distance. Drivers only accept, start, arrive, pick up, and complete the trip.

While on the way, the driver may explicitly start live GPS sharing. The client who requested that booking and coordinators/admins can see the driver's latest location on its booking details page; the coordinator queue and client booking list include a **Track Driver** link. Sharing is opt-in, stops on arrival/status change or when the driver leaves the page, and stale location fixes are cleared after 60 seconds. Browser geolocation requires HTTPS (or localhost during development); the current plain-HTTP phone URL must be served over HTTPS for GPS sharing to work on a phone.

## Tests

Run focused booking workflow tests with:

```powershell
py -m unittest discover -s tests
```
