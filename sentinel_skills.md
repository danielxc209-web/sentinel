# Sentinel Skills

This file is automatically updated as Sentinel learns new techniques.

---

## Skill: Twilio Voice Call Notification
*Learned: manually seeded*

Use this skill whenever Sentinel needs to notify Daniel of something important — a result, a warning, an error, or any message that shouldn't just be printed. Instead of printing, call Daniel's phone using Twilio's TTS. No Gmail involved. Just build the message string and pass it to `make_call()`.

**When to use:** Any time `speak` in the response JSON is non-empty, or the task result is important enough that Daniel needs to hear it. Prefer a call over a print for anything actionable.

**Credentials (already hardcoded — do not change):**
- Twilio account SID and auth token are in the snippet below
- `from_number` is the Twilio number
- `phone_to` is Daniel's number

```python
from twilio.rest import Client

FROM_NUMBER = '+14472514119'
PHONE_TO    = '+15209342069'

def make_call(message_text: str):
    account_sid = 'ACcb3703dd47c9b0422bea6073458e66f1'
    auth_token  = 'd1c23304a3179b7a7b2d38505e862fdc'
    client = Client(account_sid, auth_token)
    twiml = f'<Response><Say voice="alice">{message_text}</Say></Response>'
    call = client.calls.create(
        twiml=twiml,
        to=PHONE_TO,
        from_=FROM_NUMBER,
    )
    return call.sid
```

**Usage pattern:**
```python
# Instead of: print("Task complete. File saved to output.csv")
# Do:
make_call("Task complete. File saved to output dot csv.")
```

**Notes:**
- Keep messages under ~30 seconds of speech (~300 words max)
- Avoid special characters like `&`, `<`, `>` in the message — they break TwiML
- The `<Say voice="alice">` tag uses Twilio's Alice TTS voice
- `call.sid` is returned for logging if needed
- `twilio` must be installed: `pip install twilio`

---

## Skill: Close Web Browser
*Learned: 2026-03-17 13:21:07*

Closes the web browser by executing a system command to terminate the browser process.

```python
os.system('pkill -f chrome')
```

---

## Skill: System Process Termination
*Learned: 2026-03-17 13:21:41*

Closes a system process by executing a system command. Use when terminating a process is necessary.

```python
os.system('pkill -f <process_name>')
```

---

## Skill: Launch External App
*Learned: 2026-03-17 14:11:57*

Launches an external app by executing a system command. Use when opening an app is necessary.

```python
os.system('open /Applications/Band\ App.app'); make_call('Band app opened.')
```

---

## Skill: Open URL in Default Browser
*Learned: 2026-03-17 14:12:56*

Opens a URL in the default web browser. Use when you need to access a website directly.

```python
os.system('open https://www.google.com'); make_call('Google opened in the default web browser.')
```

---

## Skill: Open URL in Default Browser
*Learned: 2026-03-17 15:48:03*

Opens a URL in the default web browser.

```python
import os
os.system('open https://upload.wikimedia.org/wikipedia/commons/8/85/Elon_Musk_Royal_Society.jpg'); make_call('Elon Musk picture opened in the default web browser.')
```
