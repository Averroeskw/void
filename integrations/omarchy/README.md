# Omarchy agent summon preview

This is a native Quickshell integration inspired by Void's orb/edge transition. It does not launch Void's Python server or copy its voice backend. The upstream application remains unchanged.

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
