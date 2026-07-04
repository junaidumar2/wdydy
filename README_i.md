# My Soundboard

A personal soundboard styled like a broadcast cart wall: tap a cart, hear the clip, and the ON AIR lamp lights while anything is playing. Carts are grouped into **David** and **Max** zones, every cart shows a mini sonogram of its clip (bar height = loudness, bar brightness = pitch), and a circular play/pause transport floats at the bottom of the screen. A **Table** toggle in the header switches to a sortable list showing each clip's length, source episode, and exact timestamps. It's a static web app that installs to an iPhone home screen and works offline, plus a command-line tool that cuts soundbites straight out of podcast episodes.

There are only three moving parts, and understanding them is 90% of maintenance:

- `soundbites.json` — the list of carts. Each entry records id, label, emoji, file, `who` (david/max), duration, the date it was added, and a `source` block: the episode title plus the exact start–end timestamps it was cut from, so any clip can be re-found in the original episode. The app reads this on every load.
- `sounds/` — the actual audio clips.
- `tools/soundbite.py` — the tool that edits both for you, so they never drift out of sync.

Everything else (`index.html`, `sw.js`, `manifest.webmanifest`, the icons) is the app itself and rarely needs touching.

## One-time setup

On WSL2 Ubuntu (or any Linux/macOS), you need `python3` and `ffmpeg` — nothing else, no pip installs:

```bash
sudo apt update && sudo apt install -y ffmpeg
cd soundboard
python3 tools/soundbite.py --help
```

## Making a soundbite from the podcast

Using *What Did You Do Yesterday?* as the worked example — its public RSS feed is `https://feeds.acast.com/public/shows/6a2be260126ad95c2367834c`. (For any other show, paste its name into podnews.net, which links the raw feed.)

**1. See what's in the feed** (1 = newest episode):

```bash
FEED=https://feeds.acast.com/public/shows/6a2be260126ad95c2367834c
python3 tools/soundbite.py episodes $FEED
python3 tools/soundbite.py episodes $FEED --search "Humid"   # find a specific one
```

**2. Download the episode.** It lands in `episodes/`, which is git-ignored so full episodes never get published with your board:

```bash
python3 tools/soundbite.py fetch $FEED --episode 1
```

**3. Find the moment.** Open the file in any player and note when the laugh starts and ends — `mpv episodes/001-*.mp3` is handy because arrow keys seek in 5s steps and it prints the current timestamp, but VLC or anything with a visible clock works.

**4. Cut it:**

```bash
python3 tools/soundbite.py clip episodes/001-*.mp3 \
    --start 23:41.5 --end 23:44 \
    --id dod-laugh --label "DOD laugh" --emoji 😂 --who david
```

That writes `sounds/dod-laugh.mp3` (with a 25 ms fade at each end and loudness-normalized so every cart plays at the same volume) and registers it in `soundbites.json` — including which zone it belongs to (`--who david` or `--who max`; omit it and the cart lands in an Extras zone), the source episode's title (read automatically from the sidecar that `fetch` writes, or from the file's own tags — override with `--source-title "..."`), and the exact `--start`/`--end` you cut, so the moment can always be re-found in the episode. Re-running with the same `--id` replaces the clip, which is the easy way to fix timing: nudge `--start`/`--end` and run it again. Useful extras: `--length 2.5` instead of `--end`, `--gain 3` for a quiet source, `--no-normalize` to keep original dynamics, and `--fade 0` for an abrupt cut.

**5. Preview it:**

```bash
python3 -m http.server -d . 8000
```

Open http://localhost:8000, tap the cart. The speed buttons and "Keep pitch" toggle live in the header — 0.5× with pitch off is the classic slowed-down deep-voice laugh. The round buttons at the bottom pause and resume whatever's playing (spacebar does the same on a keyboard), and the lamp reads PAUSED while held. Each cart's sonogram strip is computed from the actual audio the first time the board loads and a dark sweep tracks playback across it.

## Putting it on your phone

The board is plain static files, so anything that serves HTTPS works. GitHub Pages is free and takes about two minutes:

```bash
cd soundboard
git init && git add -A && git commit -m "Soundboard"
git branch -M main
git remote add origin https://github.com/YOURNAME/soundboard.git
git push -u origin main
```

Then on GitHub: repo → Settings → Pages → set Source to "Deploy from a branch", branch `main`, folder `/ (root)`. Your board appears at `https://YOURNAME.github.io/soundboard/` within a minute or two.

On the iPhone, open that URL in Safari, tap Share → **Add to Home Screen**. It launches full-screen like a native app, and after the first visit the service worker has cached the shell and every clip, so it keeps working with no signal. Two iOS quirks worth knowing: sounds play even with the ring/silent switch on silent (web *media* ignores the switch), and "Download mode" saves clips into the Files app.

One note on the clips themselves: podcast audio is someone else's copyrighted work, so treat this as a personal soundboard among friends. A public GitHub Pages URL is technically world-readable — keep it unlisted, or if you'd rather it be genuinely private, serve the folder over Tailscale from your machine instead (`tailscale serve 8000` while `http.server` runs).

## Everyday maintenance

The whole update cycle is clip → preview → publish. `publish` is just a shortcut for add/commit/push:

```bash
python3 tools/soundbite.py list                          # what's on the board
python3 tools/soundbite.py set dod-laugh --emoji 🤣      # rename label/emoji
python3 tools/soundbite.py set dod-laugh --who max       # move a cart between zones
python3 tools/soundbite.py remove old-bit --delete-file  # take a cart off
python3 tools/soundbite.py add ~/some-file.wav --id airhorn --emoji 📯
python3 tools/soundbite.py publish -m "New laughs"
```

The app fetches `soundbites.json` network-first with a cache-buster, so new carts appear on a normal refresh once Pages has deployed. If the home-screen app ever seems stuck on old content, force-quit and reopen it, or pull-to-refresh in Safari; as a last resort bump the `CACHE` name in `sw.js` (e.g. `soundboard-v3`) and publish, which makes every device discard its old cache.

The Table view (header toggle) lists every cart with its length, zone, source episode, timestamps, and date added — click a column header to sort, click again to reverse. It's the quickest way to answer "which episode was that from?": the `At` column gives the exact minute to jump back to. Carts made before this feature existed just show `—` in those columns; re-cut them (same `--id`) or tag them with `set` to fill the gaps.

Order within each zone follows order in `soundbites.json` — reorder the entries in the file to reorder the carts (any text editor; the tool won't mind). Keys 1–0 on a keyboard fire the first ten carts, and Esc is stop-all.

## Why this isn't a native iOS app (yet)

Shipping a real iOS app requires Xcode on a Mac plus either a paid developer account or re-sideloading every 7 days — a lot of friction for a soundboard, and impossible to build from a Windows/WSL2 machine. The PWA gets the things that matter (home-screen icon, full-screen, offline, instant taps) with zero-friction updates. If you later want native, the structure here transfers directly: a SwiftUI grid reading the same `soundbites.json` and mp3s from its bundle would be a weekend project, and the clip tool stays exactly as it is.

## Troubleshooting

**"ffmpeg not found"** — install it (`sudo apt install ffmpeg`); the tool checks before every cut. **A cart shakes and shows "Could not load"** — the mp3 named in `soundbites.json` isn't where the file says it is; run `list` and look for `MISSING`. **Feed won't parse** — you've probably got a webpage URL rather than the RSS feed itself; grab the feed link from podnews.net. **No sound on iPhone in Safari** — check the volume rocker rather than the silent switch, and make sure it wasn't paired to sleeping headphones. **Clip timing is off by a beat** — trust the tool, not the player you scrubbed in: some players show the buffered position, so nudge `--start` by ±0.5 s and re-run with the same `--id` until it's tight.