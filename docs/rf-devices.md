# RF devices: setup and lockdown

The scanner app is a web app at `https://<your-site>/wms`. Any device with a
current browser runs it: a rugged Android terminal (Zebra, Honeywell,
Datalogic), an Android or iOS phone with a Bluetooth scanner, or a
fixed-mount PC at a packing table. This guide covers setup, locking a device
to the app, and scanner settings.

## Before the first device

| Requirement | Why |
|---|---|
| HTTPS with a valid certificate | Camera scanning and installing the app only work on a secure origin |
| One Frappe user per person, never shared | Every posting records its user; shared logins break the audit trail |
| The user's WMS roles (Receiver, Picker, Packer, Loader, Operator, Inventory Controller) | The server checks the role on every action |
| A **User Permission** on *WMS Warehouse* for people who work in one warehouse | Their lists, tasks and documents then show only that warehouse |
| At least one **WMS Resource** per warehouse, one per device or person working at the same time | Operators log on to a resource; tasks are queued to resources |
| *System Settings > Session Expiry* at least one shift (for example `12:00`) | A shorter session makes people log in again mid-shift. Work in progress survives the login, but it costs time |
| Wi-Fi coverage in docks, aisles and the yard | The app retries safely after a dropped connection, but it needs the network to post |

Browser minimums: Chrome/Edge 89+ on Android, Safari on **iOS 16.4+**
(import maps), Chrome/Edge/Firefox on a PC.

## Install the app on the device

- **Android (Chrome):** open `https://<site>/wms`, log in, then use the menu's
  **Install app** option. It opens full screen with its own icon, like a
  native app. The app publishes a web app manifest (`/assets/frappe_wms/wms-manifest.json`).
- **iOS (Safari):** open `https://<site>/wms`, then **Share > Add to Home
  Screen**. It launches full screen from the home screen.
- **PC at a table:** a browser window or an installed Chrome/Edge app. A USB
  scanner in keyboard mode works as-is.

On first launch the operator logs on to a WMS Resource. *Device & session*
has the per-device switches for sound, vibration and theme.

## Lock the device to the app

Choose the tightest option your devices support.

### Android: dedicated device (recommended for terminals)

Use your EMM/MDM (Google Android Management, Microsoft Intune, SOTI
MobiControl, Zebra StageNow / 42Gears, …) to enrol terminals as **dedicated
devices** (Android Enterprise "COSU"):

1. Publish the WMS as a **web app** in managed Google Play (URL
   `https://<site>/wms`), or deploy Chrome with the policies below.
2. Put the device in **kiosk / lock task mode** with that one app (plus the
   scanner configuration app, if the vendor needs one).
3. Chrome managed policies when Chrome is the kiosk app:
   - `URLAllowlist`: `https://<site>/*`
   - `URLBlocklist`: `*`
   - `IncognitoModeAvailability`: `1` (disabled)
   - `PasswordManagerEnabled`: `false`
   - `DefaultNotificationsSetting`: `2`
   - `VideoCaptureAllowedUrls`: `https://<site>` (camera scanning without asking)
4. Device policies: screen timeout 5–10 minutes, status bar and settings
   locked, system updates in a maintenance window, Wi-Fi profiles pushed by
   the EMM.

Zebra devices can also run **Enterprise Browser** in kiosk mode pointing at
`/wms`. Keep DataWedge in keystroke mode (below).

### iOS / iPadOS

- **Managed fleet (recommended):** supervise the devices through Apple
  Business Manager + your MDM. Push the WMS as a **Web Clip** (full screen,
  not removable) and lock the device with **Single App Mode** or **Autonomous
  Single App Mode**. Set Auto-Lock to 5–10 minutes. Disable the App Store and
  Safari outside the web clip if your MDM allows it.
- **A few devices, no MDM:** add the app to the home screen, open it, then
  **Settings > Accessibility > Guided Access** and triple-click to start a
  session with a passcode. The device stays in the app until the supervisor
  ends the session.

### PC at a packing or gate station

Run the browser in kiosk mode, for example
`chrome --kiosk --app=https://<site>/wms` (the desk Repack Center is
`/app/wms-packing-station`). Use a Windows *Assigned Access* or Linux kiosk
user so the station cannot open anything else.

## Scanner configuration

The app treats a scan as a fast burst of characters ending in **Enter**. It
routes the burst to the field the screen is waiting for, even when nothing is
focused, and pulls a scan back out of a quantity box.

| Setting | Value |
|---|---|
| Output mode | Keyboard / keystroke / HID (DataWedge "Keystroke output", Honeywell "Keyboard wedge") |
| Suffix | Enter (CR). Without it every scan needs a tap |
| Prefix / AIM symbology identifier | Optional. The app strips `]C1`, `]E0`, `]d2`, `]Q3`-style prefixes |
| Inter-character delay | 0 ms (bursts are how scans are told apart from typing) |
| GS1-128 / DataMatrix | Enable, and transmit FNC1 as the GS character (ASCII 29, `Ctrl+]`). Without GS, a label parses only when its variable-length fields (batch, serial) come last |
| Symbologies | Code 128/GS1-128, EAN-13/UPC-A, DataMatrix, QR. Disable the ones you do not print, to avoid misreads |
| Bluetooth scanners on iOS | Pair in **HID keyboard** mode. The on-screen keyboard then stays hidden |

The camera button next to every scan field is a fallback for devices without
a scanner. It reads the same symbologies.

## Printing from the floor

Labels and packing lists print through WMS printers (WMS Resource of type
Printer). Use direct network printing (raw TCP 9100) where the server
reaches the printer, or run the print agent
(`frappe_wms/print_agent/wms_print_agent.py`) on a PC next to the printers.
The devices themselves never print directly.

## Checklist for a new device

1. Enrol it in the MDM, or set up Guided Access.
2. Push Wi-Fi and the app (web app or web clip); lock it to the app.
3. Configure the scanner: keystroke output, Enter suffix, GS1 with GS.
4. Log in as the operator, log on to a WMS Resource, and scan a bin label and
   a product label on the *Lookup* screen.
5. Scan a GS1 pallet label on *Receive*: GTIN, batch, expiry and SSCC should
   fill in by themselves.
6. Put the device in airplane mode, confirm a task, turn the network back on
   and press **Retry**. The task is confirmed once.
