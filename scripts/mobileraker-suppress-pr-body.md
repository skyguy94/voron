## Problem

State notifications are all-or-nothing per state. A printer that pauses itself as
part of normal operation — a heat soak before the first layer, a filament change, a
tool change — produces an alert indistinguishable from a real fault. The only way to
stop it is to turn off paused notifications in the app, which also loses the alerts
that matter.

The companion already solves this, but only for one plugin:

```python
_OBJECTS_TO_SUBSCRIBE = [ ..., "gcode_macro TIMELAPSE_TAKE_FRAME", ... ]

elif rawObjectKey == 'gcode_macro TIMELAPSE_TAKE_FRAME':
    if is_paused is True:
        self.timelapse_pause = True
```

Clon1998/mobileraker#71 asked for the general version.

## Approach

The printer already has a command channel to the companion: `_on_gcode_response`
listens on `notify_gcode_response`, and custom notifications ride it by prefix
(`MR_NOTIFY:` / `$MR$:`). Suppression fits there. It is a message from the printer,
not state to be polled.

```
MR_NOTIFY_SKIP: <state> [ttl_seconds]
MR_NOTIFY_SKIP: clear <state>
```

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

```gcode
MR_NOTIFY_SKIP STATE=paused
PAUSE
```

### Lifetime

An announcement holds until the state it names has come and gone. For a pause that
means the printer announces, pauses, resumes, and the announcement clears itself
with no cleanup written anywhere.

When the announced state may never arrive, the printer withdraws it:

```gcode
MR_NOTIFY_SKIP STATE=error
    ... a print start that may fail and retry ...
MR_NOTIFY_SKIP CLEAR=error
```

`TTL=<seconds>` is optional and bounds only how long an announcement waits for its
state to arrive. It is deliberately not required: any default short enough to be a
useful guard is too short for a print start that includes a long heat soak, and an
announcement that silently expires half way through is worse than one that is
explicitly withdrawn.

### A dead printer still reports

`klippy_ready` is false when klipper has shut down, and the companion synthesises the
`error` state in that case. A gcode error leaves klippy ready, a thermal runaway or
MCU fault does not, so `is_suppressed_state` refuses to suppress anything while
klippy is down. That keeps `STATE=error` safe to arm across a whole print start:
retryable gcode failures stay quiet, a printer that has actually died still pushes.

That is what makes it safe. A sticky variable has to be cleared on every exit path,
and if a print errors or is cancelled between set and clear the flag stays raised
and the next genuine pause is swallowed silently, with nothing telling the user
their alerts are muted. A self-expiring one-shot cannot fail that way.

### The paired resume stays silent, deliberately

When suppressing, the stored last-notified state is not advanced, so on resume the
evaluator's first equality check returns `None`. This matches the existing timelapse
behaviour. One deliberate pause should produce zero alerts rather than one.

## Demonstrated against the case already special-cased

The clearest test of whether this is general enough is whether it expresses the
behaviour the companion already hardcodes. Today moonraker-timelapse suppresses its
snapshot pause through a subscription to `gcode_macro TIMELAPSE_TAKE_FRAME` and a
`variable_is_paused` that it sets and clears around every layer change. With this
change the same thing is one line inside timelapse's own macro:

```gcode
MR_NOTIFY_SKIP STATE=paused
PAUSE
```

and nothing plugin-specific in the companion.

`test_notify_skip_matches_the_timelapse_special_case` asserts exactly that: three
identical paused snapshots, one plain, one flagged the timelapse way, one announced
with `MR_NOTIFY_SKIP`. The plain one notifies; the other two are silent. Same
verdict by both routes, with a control proving the suppression is doing the work
rather than the test passing vacuously.

The semantics match too, not just the outcome. A timelapse pause is suppressed for
as long as it lasts rather than for a fixed window, and so is this one: the ttl only
governs the wait for the state to arrive, and once it has, suppression holds until
the state ends.

## Compatibility

`is_suppressed_state` subsumes `is_timelapse_pause`, and
`gcode_macro TIMELAPSE_TAKE_FRAME` stays in `_OBJECTS_TO_SUBSCRIBE`. Nothing changes
for existing moonraker-timelapse users. The special case could later be retired by
having timelapse emit `MR_NOTIFY_SKIP` instead.

This is the complement to the app's per-printer `states` list, not a replacement:
that mutes a state permanently, this lets the printer declare one specific upcoming
transition expected while leaving every other pause loud.

## Testing

Twelve tests added, covering the parse path, the ttl and expiry, and that suppression
is state-specific rather than a blanket mute:

```
test_notification_evaluator                36 tests  OK   (was 33)
data_sync_client_test                       26 tests  OK   (was 17)
test_mobileraker_companion_integration      16 tests  OK
```

Specifically: a `MR_NOTIFY_SKIP` response arms suppression and is consumed rather than
surfaced as a custom notification; the ttl is optional and a request without a state
is ignored; an expired suppression is absent from the snapshot; a normal gcode
response is unaffected; and a suppression armed for `paused` does not silence an
`error`.
