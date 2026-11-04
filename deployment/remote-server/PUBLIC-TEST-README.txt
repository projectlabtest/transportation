LHC Transport - temporary public test link

This is not a production deployment. Cloudflare relays the app's public HTTPS
traffic while the Quick Tunnel is running. Use dummy data only.

1. Extract this ZIP in File Explorer.
2. Double-click START-PUBLIC-TEST-TUNNEL.cmd.
3. Read the warning and click Yes only if you agree to a temporary public
   test link and will not enter real or sensitive data.
4. Wait for the success message. Notepad opens the temporary APK URL, app URL,
   and randomly generated passwords for the admin, coordinator, both drivers,
   and test requester.
5. You can click Yes on the optional URL-report prompt to send only the
   public URL to the developer PC over encrypted Tailscale; no credentials or
   booking data are included.
6. On the Android phone, open the APK URL in Chrome to download and install.
   Enter the app URL and the matching role account/password on the sign-in
   screen.
7. To stop sharing, double-click STOP-PUBLIC-TEST-TUNNEL.cmd. This immediately
   stops the tunnel. A new random link is generated if restarted.

The script refuses to publish a database containing bookings or unexpected
users. It downloads cloudflared from Cloudflare's official release URL and
verifies its Authenticode signature before running it. It does not configure
router port forwarding or upload the app source/database to Cloudflare; the
tunnel relays incoming requests to the app running locally on the server.
