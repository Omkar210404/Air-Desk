# AirDesk

Control your computer with hand gestures through an ordinary webcam, including gestures you teach it yourself. Everything runs on your machine: no cloud, no account, no network calls.

## What it does

- **Live view**: a mirrored webcam feed with a glowing hand skeleton, the detected gesture and a confidence bar. A **Hide camera image** switch shows only the skeleton on a dark background, so screen recordings and calls don't show you or your room.
- **Built-in gestures** (rules on MediaPipe's 21 hand landmarks, no training needed):

  | Gesture | Action |
  |---|---|
  | Point with your index finger | Move the cursor (smoothed, small dead zone) |
  | Quick pinch (thumb + index) | Left click |
  | Pinch and hold (about 0.75 s) | Drag |
  | Thumb on middle fingertip, ring/pinky relaxed open | Right click |
  | Two fingers up, move hand up/down | Scroll |
  | Open palm, swipe left / right | Previous / next slide (arrow keys) |
  | Hold a fist | Pause / resume all control |

- **Teach your own gestures**: name a gesture, show it for 20 samples, and AirDesk trains a small k-nearest-neighbours classifier on the spot. You then map it to a shortcut (`ctrl+shift+m`, `win+shift+s`, ...), mute, volume up/down, play/pause, next/previous track, a screenshot, an app or file, or a website.
- **Customise every built-in gesture**: each one in the side panel has a *Customise* button.
  - Keep the default action, switch it to any other action (a shortcut, media key, screenshot, app or website), or turn it off.
  - Two fingers can control volume instead of scrolling.
  - Adjust the sensitivity: cursor feel (smooth / balanced / fast), how long to hold before a drag starts, scroll speed, swipe length, and fist hold time.
  - The fist always stays the safety pause; only its hold time can change.
- **Presenter mode**: large gesture hints at the bottom of the screen and a red laser dot that follows your index finger. Both are click-through overlays. Pinch and scroll are switched off so they can't disturb a slideshow; swipes still change slides.
- **Safety**:
  - Control starts **OFF**.
  - **Ctrl+Alt+G** turns it on or off from any app.
  - A small always-on-top pill shows when control is on.
  - Control pauses when no hand is seen, and a hand must be in view for 0.35 s before it acts.
  - Cooldowns stop repeated triggers.
  - Opening an app or website always asks you first.
- **States**:
  - **OFF**: watching only.
  - **ARMED**: on, but waiting for a hand or paused by fist.
  - **ACTIVE**: your hand is driving.

## Install and run (Windows)

Needs Python 3.11 and a webcam.

```powershell
git clone <this repo> airdesk
cd airdesk
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m airdesk
```

Use `python -m airdesk --camera 1` to pick another camera.

**Linux / macOS**: not tested yet.
- On Linux, use an X11 session. Wayland blocks the global hotkey and synthetic mouse input that pynput relies on.
- On macOS, allow Camera, Accessibility and Input Monitoring for your terminal in System Settings › Privacy & Security.
- On both, the cursor only moves on the primary monitor.

## What is stored, and where

- Camera frames are never written to disk.
- Taught gestures and built-in gesture settings are saved to `~/.airdesk/config.json`. Each sample is 60 numbers (landmark positions relative to the wrist and divided by hand size), not an image. The kNN "model" is those samples, and it is rebuilt in milliseconds at start-up.
- The *Screenshot* action saves PNGs to `~/Pictures/AirDesk/`.

## How custom gestures work

1. **Features**: landmarks are made independent of where the hand is (wrist moved to 0,0) and how big it looks (divided by the wrist-to-middle-knuckle length). Left hands are mirrored to match right hands. Depth counts half, because it is noisier.
2. **Classifier**: scikit-learn `KNeighborsClassifier` (k=5, distance-weighted).
3. **No-match rule**: kNN always names *some* gesture, so a frame only matches when its nearest sample is about as close as your samples are to each other and most neighbours agree. Otherwise it counts as "no gesture".
4. **Firing**: a match must be held for 0.5 s, fires once per hold, and then waits a 1 s cooldown. While a taught gesture is shown, it takes priority over the built-in ones.
5. **Held-out check**: samples are recorded in order. The last 5 of each 20 are kept aside, the model is trained on the first 15, and the Teach window reports how many of the 5 it recognised (and across all your gestures). The list in the main window shows the same check. These numbers come only from your own recordings. That means one person, one camera and similar lighting, and close-together frames, so expect real-world accuracy to be lower.
6. **Clash warning**: if most samples look like a built-in gesture to the rules (say, "Point"), the Teach window warns you. A *live test* line shows what the current frame matches, so you can try other hand shapes before saving.

## Performance

Measured on the development laptop (12-thread CPU, webcam over DirectShow):

- **MediaPipe:** about 18 ms per frame with a hand tracked (9 ms with the lighter model), and about 20 ms per palm search with no hand in view.
- **Camera:** in good light the webcam delivered 30 fps. In dimmer room light it dropped to 15 fps, because it lengthens exposure, and that was the main source of lag. Forcing a short exposure gave 30 fps but an almost black image, so AirDesk leaves exposure on automatic and suggests more light instead.
- **Hand search:** at 30 fps it runs on every other frame; when the camera is slower, it runs on every frame.

Capture and inference run on a background thread. If frames drop below 24 fps while a hand is tracked and processing is the bottleneck, AirDesk steps down resolution and model size by itself.

## Limits

- **No published accuracy.** Neither the built-in gestures nor any custom gesture has been measured across several people.
  - The built-in thresholds were tuned on two recording sessions from **one person** (about 1,500 frames of landmark numbers, no images). For example, a fist used to be mistaken for a pinch, and natural clicks lasting 0.4–0.53 s were turning into drags.
  - On a different hand or camera they may need adjusting. They are at the top of `airdesk/gestures.py`.
- **Single-camera hand tracking breaks down** in dim light, against busy backgrounds, with fast motion, and when fingers hide each other (side-on pinches).
- **One hand at a time**, primary monitor only.
- **Gestures depend on orientation.** Custom gestures aren't rotation-normalised: a gesture taught upright may not match when your hand is tilted far.
- **Presenter overlays may not show on top** of apps that take exclusive full-screen.
- **mediapipe is pinned to 0.10.21**, the last release with the bundled `mp.solutions` Hands model, so nothing downloads at runtime. That also pins numpy below 2.

## Project layout

```
airdesk/
  __main__.py   entry point (python -m airdesk)
  app.py        main window, gesture list, running actions
  tracker.py    camera + MediaPipe on a background thread, adaptive quality
  gestures.py   landmark features, built-in gesture rules, cursor smoothing
  custom.py     taught-gesture features, kNN classifier, held-out check, config file
  teach.py      Teach window and Customise window
  control.py    mouse/keyboard via pynput, shortcut parsing, global hotkey
  widgets.py    dark theme, live view, status pill, presenter overlays
```

## License

MIT
