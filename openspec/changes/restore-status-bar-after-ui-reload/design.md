## Context

`JobRuntime.register()` appends one stable draw callback to `bpy.types.STATUSBAR_HT_header` and records `_registered = True`. During in-session Extension installation or replacement, Blender can later rebuild that Header and discard its dynamic `_draw_funcs` list. The runtime, operators, and polling timer remain registered, so jobs run normally while progress and cancellation UI disappear until Blender restarts.

The runtime already polls on Blender's main thread through a persistent application timer. Recovery must use Blender UI APIs only from that thread and must not add another lifecycle mechanism.

## Goals / Non-Goals

**Goals:**

- Keep the status bar callback attached for the lifetime of a registered runtime.
- Recover automatically after Blender replaces the Header draw function.
- Avoid duplicate callbacks during ordinary polling.
- Preserve clean unregistration and workspace owner metadata.

**Non-Goals:**

- Change job progress values, messages, cancellation, or server polling.
- Replace Blender's status bar with a separate panel or notification system.
- Work around unrelated failures that prevent the add-on or its timer from registering.

## Decisions

### Separate runtime registration from UI hook presence

`_registered` will continue to represent the complete Runtime lifecycle. A dedicated status bar helper will inspect the current Header draw callback list and append the runtime's stable callback only when it is absent. This makes UI hook presence independently recoverable without re-registering Operator classes or timers.

The check will use Blender's dynamic UI `_draw_funcs` collection because `Header.append()` has no public membership query. Repeated unconditional remove-and-append was rejected because it mutates draw order every polling interval and performs unnecessary UI registration work.

### Recover from the existing polling timer

The Runtime will ensure the status bar hook during initial registration and on each `_poll_server()` invocation. The first timer tick occurs after add-on registration, allowing recovery after the surrounding Extension installation workflow finishes rebuilding UI classes. Later ticks provide continued recovery without a new timer or handler.

A separate delayed one-shot callback was rejected because the exact ordering and duration of Blender's installation refresh are external to BlendJob, and a one-shot callback could still run too early.

### Reuse the stable draw callable

Recovery will append the existing `self._status_bar_draw` callable rather than create a replacement. Blender assigns owner metadata during initial add-on registration; retaining the callable preserves that metadata when recovery runs outside the add-on registration owner context.

### Stop recovery before removing UI

Unregistration will disable the polling timer before removing the status bar callback. The removal helper will tolerate a callback already lost to a Header rebuild. This prevents a later timer tick from restoring UI after the Runtime has been unregistered.

## Risks / Trade-offs

- **Blender's `_draw_funcs` is an internal implementation detail** → Isolate access in one helper, use defensive `getattr`, and cover missing and populated collections in tests.
- **Polling adds a membership check every interval** → The check is an in-memory identity lookup over a small callback list and does not trigger redraw or mutation when the callback is present.
- **A rebuilt Header can temporarily omit progress until the next tick** → Reuse the existing one-second initial polling interval; no separate high-frequency timer is introduced.
- **Owner filtering could hide a recovered callback** → Reuse the same function object whose owner metadata was assigned during initial registration.
