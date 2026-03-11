# AchoDetect

AchoDetect is a voice-authenticity web app with a forensic-style Python backend.

## Run locally

```bash
python3 server.py
```

Then open:

- `http://127.0.0.1:8080/` for the main upload UI
- `http://127.0.0.1:8080/result.html` for the analysis report page (opened automatically after Analyze)
- `http://127.0.0.1:8080/health` for service health

## Audio format support (including phone recordings)

- **Best quality:** PCM `.wav` (full forensic features)
- **Phone recordings/call exports:** `.mp3`, `.m4a`, `.aac`, `.ogg`

- **Missing extension filenames:** backend now infers extension from content/header (e.g., raw uploads named without `.mp3`).
  - If `ffmpeg` is available on server, these are auto-converted to WAV and analyzed.
  - Without `ffmpeg`, they are accepted but return a low-confidence fallback.

## If frontend and backend are on different domains

If your frontend is on Netlify and backend is on Render/Railway, open frontend with:

```text
https://your-frontend.netlify.app/?apiBase=https://your-backend.onrender.com
```

This sets the backend base URL for API calls and fixes `Failed to fetch` from cross-host deployment mismatch.

## How it works

1. Upload an audio file on the main page.
2. Click **Analyze**.
3. App calls `POST /api/detect`.
4. You are redirected to `result.html` with:
   - final label (Real Voice / AI Voice)
   - percentage chart report
   - downloadable PDF report via print dialog.

## API

### `POST /api/detect`

Upload audio with `multipart/form-data` field name `audio`.

```bash
curl -X POST -F "audio=@sample.wav" http://127.0.0.1:8080/api/detect
```

The response includes:
- file metadata
- forensic features (`zero_crossing_rate`, `energy_variability`, `clipping_ratio`, etc.)
- authenticity/synthetic scores with confidence band and rationale

## Model notes

`AchoDetect-Forensic-v2` is an interpretable forensic-style model built on transparent signal features. It should support investigation and human review, not replace legal or expert judgment.

## Troubleshooting: "Failed to fetch" for uploaded conversation files

If you upload files like `freesound_community-002145_a-conversation-with-a-neighbor-53032` and see `Failed to fetch`, the issue is usually connectivity (frontend cannot reach backend), not file content.

Check in this order:

1. Verify backend health endpoint is reachable:

```bash
curl https://your-backend.onrender.com/health
```

2. If frontend is HTTPS (Netlify), backend URL must also be HTTPS.
3. Set **Backend API Base URL** in the main page UI, then click **Test Backend**.
4. For non-WAV files (`.mp3`, `.m4a`, call recordings), install `ffmpeg` on backend for full analysis.

Note: this model is for **human voice authenticity**. Non-speech audio (SFX, barking, noise-only clips) may return low-confidence or inconclusive output.
