# Device API, persistence and limits

Everything goes through WLED's existing JSON API. The usermod adds no HTTP routes of its own, so WLED's normal request handling and JSON buffer locking apply unchanged. **Security:** like the rest of WLED's JSON API, this has no authentication of its own. Anyone who can reach the device on the network can upload a formula. A formula cannot touch memory or the network (it can only produce colours and use up its time budget), but treat it like any other control of your lights and keep the device off the public internet.

## Slots

The usermod registers `LSF_SLOTS` effects (default 4) named **LS Formula 1** to **LS Formula 4**. Each *slot* holds one formula. The slots are registered once at boot because WLED reads an effect's metadata string at registration. What an upload changes is the **formula inside the slot** and the slot's **name**, never the effect's WLED id. A preset or segment that uses "effect 171" keeps working after the formula in that slot is replaced.

The slot's WLED effect id is returned in the state as `fx`. It depends on the WLED version and the other usermods in the build (it was 142, 169, 170 and 171 on the test build), so read it rather than hard-coding it.

The slider labels are fixed for every slot: **Speed, Intensity, P1, P2, P3**, which are `sp`, `ix`, `c1`, `c2`, `c3` in a formula. Colour slots 1..3 are `k0..k2` and the palette is `pal()`.

## Requests

All requests are `POST /json/state`. Add `"v": true` to get the new state back in the reply.

| Body | Effect |
|---|---|
| `{"lsf":{"slot":0,"src":"...","name":"Wave"}}` | compile, check the budget, install and persist |
| `{"lsf":{"slot":0,"src":"...","dry":true}}` | everything except install and persist |
| `{"lsf":{"slot":0,"clear":true}}` | empty the slot |

`name` is optional; only letters, digits, space, `-` and `_` are kept (the metadata format reserves `@`, `;` and `,`), up to 20 characters. To run a slot, set the segment's effect to its `fx`, for example `{"seg":[{"id":0,"fx":171}]}`.

### The reply

The reply contains an `lsf` object:

```json
{
  "lsf": {
    "slots": [
      {"slot": 3, "fx": 171, "name": "Falling Leaves", "ok": true,
       "instrs": 161, "cost": 224, "prev": false,
       "estUs": 3410, "maxPx": 219, "avgUs": 2400, "worstUs": 2582,
       "src": "..."}
    ],
    "last":   {"slot": 3, "ok": true, "dry": false, "instrs": 161, "cost": 224, "estUs": 3410, "maxPx": 219},
    "limits": {"src": 1024, "instrs": 256, "locals": 16, "slots": 4, "budgetUs": 15000, "pixels": 50}
  }
}
```

- `slots[].ok` is false when the slot is empty, was switched off, or is over budget; `err` then says why.
- `last` is the result of the **most recent request**, including dry runs. On failure it has `err`, and for compile errors `line` and `pos`; `line` is `0` for errors that are not about the source, such as an over-budget rejection.
- `estUs` is the estimated frame time for the whole strip, `maxPx` how many LEDs the formula fits in the budget, `avgUs` a moving average of the real frame time and `worstUs` the largest single frame since install. `worstUs` includes preemption by WiFi and HTTP work, so judge a formula by `avgUs`.

An upload that fails leaves the previous formula running.

## Button control

The state JSON also reports the one-button control (see the README), under `lsf.button`: `{"pin": 9, "ok": true, "stepPct": 5}`. `pin` is `-1` when the feature is off; `ok` is `true` only when the usermod owns the pin and is reading it. The settings live in `cfg.json` under `um.ls_formula`: `button pin`, `button active low`, `brightness step`.

## Persistence

An install sets WLED's `configNeedsWrite`, and WLED writes the source into `cfg.json` under `um.ls_formula` shortly afterwards. At boot the usermod recompiles each stored source, so bytecode is never stored: a future change to the language or limits cannot leave stale bytecode on flash. A stored formula that no longer compiles stays visible (with `err`) but inactive.

Four 1 KB sources add about 4 KB to `cfg.json`. The segment's current effect is **not** saved by this usermod; save a WLED preset for that.

## How a frame is drawn

For each frame WLED calls the slot's effect function with the current segment:

1. If the slot is empty, disabled or over budget, the segment is filled black.
2. The segment is 2D only when `strip.isMatrix` is true and the segment itself is 2D; a segment on a matrix's trailing strip runs as 1D.
3. If the formula uses `prev`/`prev2`, two RGB frame buffers are allocated in the segment's effect data.
4. For every pixel the inputs are filled in and the bytecode runs once. The colour is written with `setPixelColor` (1D) or `setPixelColorXY` (2D).
5. The real frame time updates `avgUs`/`worstUs`.

A new program is swapped in with the strip suspended (`strip.suspend()` then `waitForIt()`), so a frame is never drawn with half of the old program and half of the new one.

## Speed budget (admission control)

What decides whether a strip keeps up is **cost x pixels**. The compiler gives each program a `cost`, a weighted per-pixel operation count (simple operators 1, `hsv` 8, `noise` 8, `blend` 10, ...). The estimate is

```text
estimated frame time (us) = pixels x (0.3 x cost + 1)
```

calibrated on an ESP32-C6 at 160 MHz against eight formulas: the estimate came out 0.99x to 1.35x the measured worst frame, and `tests/test_lsf.cpp` fails if it drifts outside 0.9x..1.5x.

- **At upload.** A formula whose estimate for the strip's total LED count exceeds `LSF_BUDGET_US` (default **15000**) is rejected with `over budget: ~22 ms for 50 LEDs, this formula fits 35 LEDs`, and the old formula keeps running.
- **Every frame.** The check repeats for the segment actually being drawn. If you later add LEDs or widen a segment, the slot turns black with an `over budget` message rather than stalling WiFi, and resumes by itself when it fits again. This is also why the upload check is skipped at boot: the strip is not set up yet.
- **Last resort.** If a frame really takes more than 60 ms three times in a row (`LSF_SLOW_FRAME_US`), the slot is switched off until it is uploaded again.

The constants (`LSF_US_PER_COST_X10`, `LSF_US_PER_PIXEL_X10`) are for the ESP32-C6. Other chips run at different speeds; measure `avgUs` against `estUs` on yours and adjust.

## Trying it without WiFi (serial)

WLED also accepts JSON on the USB serial port at 115200 baud (it replies when you include `"v": true`). Two traps found on the ESP32-C6:

- Opening the port **resets the board**. Wait about 9 seconds before sending anything.
- A request longer than about 250 bytes sent in one write is silently dropped by WLED's serial reader. Send it in chunks of about 48 bytes with a short pause between them.

HTTP has neither problem, so prefer `tools/lsfx.py`.
