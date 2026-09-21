# Proposal: general pause-notification suppression for mobileraker_companion

## Problem

State notifications are all-or-nothing per state. A printer that pauses itself as
part of normal operation, a heat soak before the first layer, a filament change, a
tool change, produces an alert indistinguishable from a real fault. The only way to
stop it today is to turn off paused notifications entirely in the app, which also
loses the alerts that matter.

The companion already has an escape hatch, but it is hardcoded to one plugin:

```python
_OBJECTS_TO_SUBSCRIBE = [ ..., "gcode_macro TIMELAPSE_TAKE_FRAME", ... ]

elif rawObjectKey == 'gcode_macro TIMELAPSE_TAKE_FRAME':
    if is_paused is True:
        self.timelapse_pause = True
```

That solves it for moonraker-timelapse and nobody else. Issue Clon1998/mobileraker#71
asked for the general version and is closed with no visible resolution, which
suggests this special case is what shipped instead.

## What it should be

The printer already has a command channel to the companion. `_on_gcode_response` is
a dedicated JRPC listener on `notify_gcode_response`, and custom notifications ride
it by prefix:

```python
prefix = '$MR$:' if is_m117 else 'MR_NOTIFY:'
```

Suppression belongs on that same channel. It is a message from the printer to the
companion, not a piece of state to be polled, and treating it as a message rather
than a flag removes the failure mode that the variable approach has.

## Wire format

```
MR_NOTIFY_SKIP: <state> [ttl_seconds]
MR_NOTIFY_SKIP: clear <state>
```

After the companion strips the leading `// ` that Klipper adds. For example
`MR_NOTIFY_SKIP: paused 60`.

Printer side, no plugin coupling:

```ini
[gcode_macro MR_NOTIFY_SKIP]
description: Tell Mobileraker a state change is expected, or withdraw an announcement
gcode:
    {% if 'CLEAR' in params %}
        { action_respond_info("MR_NOTIFY_SKIP: clear " ~ params.CLEAR) }
    {% else %}
        {% set state = params.STATE|default('paused') %}
        {% set ttl = params.TTL|default(0)|int %}
        { action_respond_info("MR_NOTIFY_SKIP: " ~ state ~ (" " ~ ttl if ttl > 0 else "")) }
    {% endif %}
```

Used immediately before the pause that is being caused deliberately:

```gcode
MR_NOTIFY_SKIP STATE=paused
PAUSE
```

## Why one-shot with a TTL, not a variable

**The TTL covers the arming window, not the pause.** Suppression is consumed at the
moment of the state transition, so the timer only has to span the gap between the
`RESPOND` and the `PAUSE`, which is under a second. A 60 second default is generous.
A 90 minute soak needs no special handling.

That is what makes it safe. A sticky `variable_is_paused` has to be cleared on every
exit path, and if a print errors or is cancelled between set and clear, the flag
stays raised and the next genuine pause is swallowed silently. The user is not told
that their alerts are muted. A one-shot that expires cannot fail that way: the worst
case is a suppression that never gets used.

## The resume is suppressed too, deliberately

The existing timelapse path does this as a side effect. When suppressing, the
companion does not advance its stored last-notified state:

```python
state=printer_snap.print_state if last.state != printer_snap.print_state
      and not printer_snap.is_timelapse_pause else None
```

so the stored state stays `printing`, and when the printer resumes, the evaluator's
first check finds no change and returns None. That behaviour is right, one
deliberate pause should produce zero alerts rather than one, and it should be
documented as intended rather than left as an accident of the implementation.

## Touch points

| file | change |
|---|---|
| `service/data_sync_service.py` | in `_on_gcode_response`, match the `MR_NOTIFY_SKIP:` prefix, parse state and ttl, store `self._suppress = (state, monotonic() + ttl)` |
| `service/data_sync_service.py` | expose it on the snapshot next to `timelapse_pause` |
| `data/dtos/moonraker/printer_snapshot.py` | property `is_suppressed_state`, true when the pending suppression matches `print_state` and has not expired |
| `service/notification_evaluator.py` | replace `if cur_snap.is_timelapse_pause` with `if cur_snap.is_suppressed_state`, and consume the suppression |
| `mobileraker_companion.py` | same substitution in the stored-state guard so the resume stays silent |

Four files, roughly the same size as the hardcoded special case it replaces.

## Migration

`gcode_macro TIMELAPSE_TAKE_FRAME` stays in `_OBJECTS_TO_SUBSCRIBE` and keeps
working, mapped internally onto the same suppression mechanism. Nothing breaks for
existing timelapse users. New callers use `MR_NOTIFY_SKIP`, and timelapse itself could
eventually emit it and let the special case be deleted.

## What this does not do

It does not let a user mute a state permanently. That already exists as the app's
per-printer `states` list, which the evaluator reads as `cfg.settings.state_config`.
This is the complement: the printer declaring that one specific upcoming transition
is expected, while leaving every other pause loud.
