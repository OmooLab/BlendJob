## Why

Blender can rebuild `STATUSBAR_HT_header` after installing or replacing an Extension in the current session, which removes BlendJob's dynamic draw callback while `JobRuntime` still considers itself registered. Jobs continue running but their progress and cancel control remain invisible until Blender restarts.

## What Changes

- Make the Blender runtime detect when its status bar callback is no longer attached.
- Restore the callback from the existing main-thread polling lifecycle without creating duplicates.
- Preserve normal registration, unregistration, owner filtering, progress reporting, and cancellation behavior.
- Add regression coverage for a status bar UI reload after runtime registration.

## Capabilities

### New Capabilities

- `recoverable-status-bar`: Runtime status bar integration remains available after Blender rebuilds dynamic UI state during Extension installation or replacement.

### Modified Capabilities


## Impact

- Affects `src/blendjob/runtime.py` and its runtime tests.
- Uses Blender's existing status bar dynamic draw API and application timer; no public BlendJob API or dependency changes.
- Improves in-session Extension installation and upgrade behavior without requiring a Blender restart.
