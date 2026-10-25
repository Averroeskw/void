# Omarchy agent summon preview

This branch includes the native Quickshell summon integration plus a full Void installation and optional Claude/Codex/Hermes backend selection.

## What was inspected

- `app/window.py`: GTK3 / WebKit2GTK 4.1, Wayland layer-shell, transparent input regions, Unix-socket commands and edge docking.
- `app/static/index.html`: animated eyes, damped springs, orb/notch morph, conversation/terminal panels and voice state.
- `app/server.py`: local voice capture, transcription/speech and streaming Claude Code subprocess. This is not a general adapter for the existing Field agents.
- Installed `local.archie-notch`: QML AgentFace, myzk-agents summary, field-status.py and open-field.py.
- Installed Field adapter: Archie voice and Hermes jobs have local status; Zima and Jetson entries explicitly report an unconnected live job feed.

## Integration

Existing face click -> summonAgent -> animated overlay -> Open agent controls -> existing open-field.py -> existing dashboard.

The overlay reads the existing summary snapshot once per second. It adds no model, microphone capture, credentials, gateway or agent process. Calm mode removes the bounce. Dismiss animates upward. Only the character/card rectangle receives pointer input; keyboard focus stays with the app.

## Files and deployment

`SummonedAgent.qml` is the new component. `archie-notch.patch` applies to the inspected local Notch.qml, not the upstream Void app. Keep the installed notch's existing MIT license and dependencies.

Back up the entire user plugin first. Apply the patch to Notch.qml, copy the component into that same directory, and load the result. On the tested host the shell cached the old QML component and directory listing: the deployed patched entry is named SummonNotch.qml, manifest entryPoints.overlay points to it, and a standard Omarchy shell restart cleared the cache. No packaged Omarchy files were changed.

Local work and original backup: /Users/architectonic/Code/archie-void-integration/.
Remote backup: ~/Archie/exchange/archie-notch-before-void-20261025/.
To roll back, restore original Notch.qml and manifest.json from that backup and restart the shell. New component files are inert once the original manifest is restored.

## Manual controls (on Omarchy)

```sh
qs ipc --any-display -p /usr/share/omarchy/shell call myzk.notch summon archie
qs ipc --any-display -p /usr/share/omarchy/shell call myzk.notch summon hermes
qs ipc --any-display -p /usr/share/omarchy/shell call myzk.notch summon remote:jetson
qs ipc --any-display -p /usr/share/omarchy/shell call myzk.notch close
```

## Validation and limits

2026-10-25: QML lint completed with import/type warnings from the standalone validator. Live Quickshell loaded the component after cache reset. Archie overlay was visually inspected on DP-2 over workspace 8. Hermes switch reported summoned=true and summonedAgent=hermes; close reported summoned=false. No workspace switch was needed for the preview.

This is a summon/status/control-entry preview. Conversation still opens in the existing dashboard, not inside the floating card. Voice-wake events are not wired to summon yet. No task was sent to an agent as part of validation. The command routes are preserved; UI button activation was not separately exercised. Full Void voice/runtime installation is not part of this integration.


## Full Void runtime and answering-agent selector

The full app is installed on archie-field under ~/.local/share/void, with the fork checkout at ~/Archie/void. `archie-void-open` launches it inside the existing unlocked desktop session. The Void roster entry reports actual runtime state, and its Open agent controls button launches the real companion.

`setup-void` installs the app, Python 3.12 environment via uv, faster-whisper/base.en, Kokoro and Piper. `install-void-native` resolves missing native dependencies through the host pacman repository, downloads and verifies each package signature using the installed keyring, and extracts only usr/ into Void's private native prefix. The launcher scopes PATH, library, GObject and GStreamer search paths to Void. No privileged system package installation was performed.

The native dependencies include gtk-layer-shell, espeak-ng, GStreamer good plugins, playerctl, maim, xclip and their resolved dependencies. System PyGObject, Cairo, GTK and WebKit are reused.

Settings > Brain > Answering agent selects Claude, Codex or Hermes. `/agent claude`, `/agent codex`, `/agent hermes` also work. Claude uses its original Void streaming integration. Codex uses exec JSON events with workspace-write (or read-only) sandbox. Hermes uses its existing CLI configuration and approvals, with no model/provider override or yolo flag. Non-Claude conversations have isolated, bounded in-memory context per backend and directory; New chat clears the selected context. These contexts reset when Void restarts. Stop targets the active CLI process group. Attachments, saved-session resume and compact currently require Claude; unsupported operations return explicit errors.

Installed on 2026-10-25: all three backends answered VOID_OK through the authenticated Void API, with an instruction to use no tools. Python compile, JavaScript syntax and whitespace checks passed. Speech state reports ready, voice engine kokoro. The native layer-shell orb was visually inspected on DP-2, workspace 8; the roster reports five entries. One WebKit allocator failure was observed during the initial stop/restart; the subsequent native renderer remained visible through backend testing. Extended renderer soak testing and live microphone validation were not performed.

Desktop microphone policy is preserved: wake_word=false, barge_in=false, follow_up=0, listen_on_show=false. Autostart is off; app-menu and notch launching work. Automatic upstream update checks are off to avoid replacing this fork's integration. No microphone was enabled and no existing Hermes model choice was changed.

Archie voice and Zima/Jetson retain their existing controls. They are not selectable conversation backends in this version. Zima/Jetson do not have verified job feeds. This is not a claim of full remote-agent support.


## Returning characters to Agents

Void now has a visible ↑ Agents button and the former Hide menu entry uses the same label. The button hides the companion window without deleting it or stopping its backend. Void returns automatically after 30 seconds without interaction, provided it is not busy, speaking, recording, transcribing, awaiting approval, holding a draft/attachment, hovered, or displaying an open settings/history/menu panel. It stays available in the persistent Omarchy agent roster.

All SummonedAgent cards have the same ↑ Agents button and 30-second idle return. Hover and active working/thinking/waiting/upload/restart states reset the timer. This applies to roster character cards, not unrelated windows or persistent dashboard decorations. The native orb keeps its existing drag/dock behavior.

Validation: idle policy regression checks cover the timeout boundary, hover, hidden state, active state, recent interaction and post-task grace period; JavaScript syntax checked. Live visual and inactivity checks are recorded in the task session.
