LHC Transport server update

This package adds a network download route for the verified Android APK.
It is intended for the separate server desktop, not the original source folder.

On the server desktop:
1. Extract the package to a temporary folder.
2. Open PowerShell in that folder and run:
   Set-ExecutionPolicy -Scope Process Bypass
   .\INSTALL-ON-SERVER.ps1
3. Enter the existing LHC Transport project folder when prompted. Verify the
   displayed path, then type INSTALL. The script backs up each replaced file.
4. Restart the existing app service/process using its normal service manager.
   Do not start a second copy of the app.
5. Test /download/android from the server before sharing it with phones.

For private HTTPS access from devices on the same Tailscale network, first
inspect the server's existing configuration with:
   tailscale serve status

If Serve is not already configured for another application, expose the local
app with:
   tailscale serve --bg 5001

Use the HTTPS MagicDNS URL shown by Tailscale Serve, followed by
/download/android, on a phone that is signed in to the same tailnet. The
Android app must use that same HTTPS base URL at sign-in. Tailscale Serve is
private to the tailnet; it is not a public website. Do not run `tailscale
serve` blindly if the server already hosts another service through it.

The APK is a debug build and may prompt Android to allow installation from the
browser. Install only on devices authorized to use this private transport app.
