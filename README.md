# Smriti — personal memory and daily assistant

Smriti is an early-alpha, voice-friendly assistant for daily organization. It combines natural-language chat with structured notes, tasks, reminders, calendar events, expenses, payment reminders, and habits.

Core records and phone notifications are designed not to depend on an AI model. Ollama or Groq interprets language; Smriti validates the result and controls database writes and reminders.

## Development status

- The Flask web application is functional and stores data in local SQLite.
- Android source has been generated and synchronized with Capacitor.
- Native Android storage, notifications, speech recognition, and calendar handoff are implemented in source but not yet validated on a physical phone.
- A debug APK has not yet been built because the Android SDK still needs to be installed.
- Google Drive backup, Google sign-in, full Calendar sync, WhatsApp, OpenClaw, and Google Pay/UPI handoff are planned, not implemented.
- This is a private development build, not a Play Store-ready release.

## Implemented capabilities

### Daily organization

- Today dashboard for tasks, events, pinned notes, payments due, and habits
- Tasks with due date, priority, completion status, and optional reminders
- Calendar events with start/end time and reminder offset
- Notes with optional pinning
- Expenses with amount, category, date, payment method, and description
- Payment reminders with amount, due date, recurrence, and paid status
- Daily or weekly habits with target counts and check-ins

### Discover and reading habit

- Manual refresh from official OpenAI News, Google DeepMind, arXiv AI, and arXiv Materials feeds
- SQLite caching so previously fetched updates remain searchable offline
- Search across cached titles, summaries, sources, and topics
- Unread and saved filters
- Direct links to original sources rather than copied full articles
- Optional `Read AI & science updates` daily habit
- Marking an item read increments that day's reading-habit count

Discover is currently a laptop/Mac-hosted feature. It also works when the web app is opened from a phone through the Mac, but it is not yet synchronized into the standalone Android data store.

### Smriti Finance MVP

- Exact-decimal Indian mutual-fund transaction records
- Duplicate-safe CSV import and import audit history
- Manual purchase, SIP, redemption, switch, reinvestment, and distribution entry
- Official AMFI NAV refresh for tracked scheme codes
- FIFO remaining-cost reconstruction
- Invested cost, current value, realized/unrealized gain, and XIRR
- Missing or stale NAV warnings
- Read-only fund-wise portfolio dashboard

Download the transaction CSV template from the Finance screen. Required columns are
`transaction_date`, `scheme_name`, `transaction_type`, `amount`, and `units`. Include
the numeric `amfi_code` for automatic NAV matching. Invalid rows are reported rather
than guessed, and repeated files or transactions do not duplicate the portfolio.

Password-protected PDF CAS parsing is intentionally not enabled yet. A real,
anonymized CAMS/Kfintech detailed CAS sample is required to build and regression-test
that parser against the exact statement layout. Android finance storage, tax estimates,
redemption recommendations, automatic trades, and Hermes access remain disabled until
financial reconciliation tests pass.

### Assistant chat

The Assistant distinguishes ordinary conversation from actions. The Flask web version uses the selected LLM first, followed by deterministic validation and an offline fallback.

Example:

```text
You: Remind me to log in to Jira for BHP demo planning.
Smriti: When should I remind you? Please give a date and time.
You: Tomorrow at 10 AM.
Smriti: Reminder created — Log in to Jira for BHP demo planning.
```

Created conversational reminders provide **Edit** and **Undo** controls.

Other supported examples:

```text
Note that sample 14 showed better stability.
Schedule a meeting with Rahul tomorrow at 3 PM.
I spent ₹450 on groceries today.
The electricity bill is due on 15 November at 9 AM.
Start a daily walking habit.
```

Capability questions are treated as conversation. `Can you take notes?` receives an explanation; `Take a note` asks what should be recorded.

Tasks with complete timing can be saved conversationally. Notes, events, expenses, payments, and habits currently open populated forms for review before saving.

### Voice capture

The global microphone and page-specific voice buttons can transcribe speech, detect a likely record type, and prefill the corresponding form.

- Short commands stop automatically when the speech service detects silence.
- Browser voice requires permission and a secure context such as `localhost` or HTTPS.
- Plain LAN HTTP such as `http://192.168.x.x:8000` may not receive microphone access on a phone.
- The Android build uses native speech recognition, but physical-device testing is pending.
- Long dictation with a manual Stop button is not implemented.

## Architecture

```text
Text or voice
     ↓
Ollama/Groq structured interpretation
     ↓
Schema, field, date, and safety validation
     ↓
Clarification or review
     ↓
Existing Smriti data API
     ↓
Database record and notification
```

The model never writes directly to the database. Smriti validates supported actions and required fields. Deterministic language/date parsing remains as an offline fallback and validation layer.

### Web version

```text
Browser → Flask → SQLite
                └→ Ollama on the Mac or optional Groq
```

- Data is stored in `data/assistant.db` on the Mac.
- Ollama defaults to `http://127.0.0.1:11434`.
- If accessed from a phone, the Mac must remain awake and Flask must be running.

### Android version

```text
Android UI → phone-local storage → native notifications
          └→ future secure cloud endpoint for Groq
```

- Phone records currently use Capacitor Preferences as local JSON.
- Web SQLite and Android-local data do not synchronize.
- The production plan is a proper phone database plus encrypted Google Drive backup.
- Native structured LLM interpretation is not deployed; Android currently falls back to offline parsing.

## Start the web application

1. Confirm Ollama and the model are available:

   ```bash
   ollama list
   ```

2. Activate the existing Conda environment and install the dependency if required:

   ```bash
   conda activate ai-agent
   python -m pip install -r requirements.txt
   ```

3. Start Smriti:

   ```bash
   python app.py
   ```

4. Open <http://127.0.0.1:8000>.

After Python changes, stop with `Control+C` and restart `python app.py`. After frontend changes, hard-refresh Chrome with `Command+Shift+R`.

Run the deterministic finance tests with:

```bash
python -m unittest discover -s tests -v
```

The default model is `llama3.2:1b`. Select another installed model from Settings. Small models may require more clarification and fallback normalization.

## Temporary phone access through the Mac

On the same trusted Wi-Fi:

```bash
conda activate ai-agent
HOST=0.0.0.0 python app.py
```

Open the Mac's LAN address, for example `http://192.168.2.101:8000`. The Mac must remain awake. Stop LAN access with `Control+C`. Prefer private Tailscale HTTPS for continued remote access, and never expose Ollama directly to the internet.

## Optional Groq connection

The Flask version supports Groq when a key is provided through the process environment:

```bash
conda activate ai-agent
export GROQ_API_KEY="your-key-from-the-groq-console"
python app.py
```

Choose **Groq cloud** in Settings. Never commit the key or put it directly in an APK.

`worker/` contains an undeployed Cloudflare Worker proxy scaffold intended to keep the Groq key out of Android. Its secrets must eventually be configured with `wrangler secret put`.

## Android project

The Capacitor project is in `android/` with application ID `in.rahulkarmakar.smriti`.

Native dependencies:

- `@capacitor/preferences` — current phone-local data
- `@capacitor/local-notifications` — reminders while the app is closed
- `@capacitor-community/speech-recognition` — voice capture
- `@ebarooni/capacitor-calendar` — Android Calendar editor handoff

Install Android Studio for Apple silicon, allow it to install the SDK/platform tools, and accept the SDK licences. Android Studio should create `android/local.properties`, or `ANDROID_HOME` must point to the SDK.

Use the existing Conda environment:

```bash
conda activate ai-agent
npm run android:sync
npm run android:apk
```

Expected APK:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

Open Android Studio with:

```bash
conda activate ai-agent
npm run android:open
```

## PWA behaviour

The web interface can be added to a phone home screen. It remains a web client of the Mac-hosted Flask application; installing the PWA does not move Flask, SQLite, or Ollama onto the phone.

Browser background notifications are less reliable than native Android notifications. The Android build is the intended long-term phone application.

## Security and safety boundaries

- Never expose Ollama directly to the public internet.
- Never commit Groq, Google, WhatsApp, or payment credentials.
- Models interpret actions but never write directly to storage.
- Missing dates, times, amounts, recipients, or payees must be clarified rather than invented.
- Expenses, payments, calendar events, and outgoing communications should require review.
- Future UPI support may open a prefilled payment app, but Smriti must never request/store a UPI PIN or silently authorize payment.
- Google Drive should hold encrypted backups, not a live SQLite database.

## Known limitations

- Pending conversational drafts live in the active browser session and can be lost on refresh.
- Conversational action cards are not fully persisted in chat history.
- Natural-language reliability depends on model quality; `llama3.2:1b` can misclassify complex requests.
- Date normalization covers common expressions and needs broader locale testing.
- Android speech, storage, calendar, and background notifications need physical-device testing.
- Editing is most complete for conversational tasks; equivalent flows are needed for other records.
- There is no conflict-safe Mac/phone synchronization.
- Drive restore, Calendar sync, WhatsApp, OpenClaw, and UPI integration are not implemented.
- Automated test coverage remains limited.

## Roadmap

1. Install Android SDK, build the APK, and test on a physical phone.
2. Consolidate actions into a central JSON Schema/action registry.
3. Persist pending actions and conversational action history.
4. Move Android records from Preferences JSON to a proper local database.
5. Add encrypted Google Drive backup and restore.
6. Deploy secure Groq structured interpretation for phone-independent AI.
7. Add Google Calendar synchronization.
8. Evaluate OpenClaw for optional WhatsApp, email, and advanced automation.
9. Add optional reviewed UPI handoff after the Android/security foundations stabilize.
10. Complete privacy documentation, signing, release testing, and Play Store packaging.
