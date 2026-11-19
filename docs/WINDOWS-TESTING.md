# Installing and testing Backend V2 on Windows

Use the same virtual environment as before (activate it first, for example `..\.venv\Scripts\Activate.ps1`). Always use `python -m pip`, because `pip.exe` can be blocked by Application Control.

## 1. Unpack and install
```powershell
Expand-Archive autopatch-backend2.zip -DestinationPath .
cd autopatch-backend2\autopatch-dev      # or wherever the ZIP's folder is
python -m pip install -r requirements.txt
```
`scripts\check.py` refuses to run when a `.env` file exists, so do step 4 (the checks) **before** creating `.env` in this folder, or run it in a fresh copy of the ZIP.

## 2. Find out what Windows blocks (read-only)
```powershell
python scripts\windows_check.py
```
Each component shows OK, BLOCKED or MISSING. For BLOCKED items the file path is printed: send it to your administrator and ask for it to be allowed. AutoPatch does not bypass the policy. You may paste the output into a chat; it never prints the key.

## 3. Run the checks (no `.env` yet)
```powershell
python scripts\check.py                  # about 2 minutes; run with 'py -3.13' if you use the launcher
```
If `ruff`/`bandit` or a test-time subprocess is blocked, the corresponding items are reported as unavailable, not as passed.

## 4. Configure Gemini
```powershell
copy .env.example .env
```
Edit `.env`: set `GEMINI_API_KEY=` to a **new** key from Google AI Studio (one line, no quotes). Leave `GEMINI_TRANSPORT=auto` (or `rest` if the SDK is blocked).

## 5. Check the key and the connection
```powershell
python scripts\gemini_rest_check.py      # one tiny request, no project code
```
A placeholder or quoted key is reported immediately, without a network call. Google's reason (for example `API_KEY_INVALID`) is shown for rejections.

## 6. Start and try it
```powershell
python app.py
```
Open `http://127.0.0.1:5000` and also `http://localhost:5000`. Both must work. Opening it under any other name (a LAN IP, for instance) returns HTTP 403 on purpose; to allow another name, set `ALLOWED_HOSTS=name` in `.env`.

Things to try:
* Upload a ZIP, scan it, open Repair Studio. With a bad key the page shows the specific message; with a good key, Generate sends one request.
* Put `AWS_KEY = "AKIA` + `ABCDEFGHIJKLMNOP"` (fabricated) into the file being repaired: Generate must refuse with a message naming "AWS access key id" and no value, and nothing leaves your computer. Add `# autopatch: allow-secret` to that line to let it through.
* In the browser developer tools (F12), Console: there should be no "Refused to ..." Content-Security-Policy messages on any page.

## What was and was not tested before delivery
Tested in the Linux build environment: the full automated suite, a real Chrome run of all frontend pages on `127.0.0.1` and `localhost`, and a negative CSP check. Not tested there: Windows itself (including the exact Application Control messages and `taskkill` process-tree cleanup), real Google requests, Bandit, IPv6 in a browser, container isolation.
