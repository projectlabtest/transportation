LHC Transport - Android APK over the local Wi-Fi network

Use this when the phone cannot reach the temporary public Cloudflare link.
This helper shares only the APK file; it does not expose the booking app,
database, or login page.

On the server PC:
1. Extract this ZIP into a new folder.
2. Double-click ENABLE-LOCAL-APK-DOWNLOAD.cmd.
3. Approve the Windows administrator prompt. The firewall rule permits only
   devices on the same local subnet, and only for the APK download port.
4. The success window displays the server PC's Wi-Fi/LAN address.

On the phone:
1. Connect the phone to the same Wi-Fi network as the server PC.
2. Open Chrome and type one of the exact URLs shown in the success window,
   including /download/android at the end.
3. Open Chrome Downloads to install LHC-Transport.apk. Allow Chrome to install
   unknown apps if Android asks.

If the phone cannot connect, check the request log shown in the server's
success window. A phone request should be recorded there. If there is no
request, the phone's connection is not reaching this server; verify the exact
displayed address, that both devices are on the same reachable network, and
that the Wi-Fi does not isolate connected devices. The helper also verifies
each displayed address from the server PC before showing the success window.

When finished, double-click STOP-LOCAL-APK-DOWNLOAD.cmd to stop the server
and remove its firewall rule.

The local address works only while both devices are on the same Wi-Fi/LAN and
the server PC remains on. Do not use a public or untrusted Wi-Fi network.
