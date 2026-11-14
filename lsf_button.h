// One-button control for local-only displays: the pure logic, with no WLED or Arduino dependency
// (so tests/test_button.cpp can run it on the desktop).
//
//   click        -> next preset
//   double click -> power on / off
//   hold         -> brightness steps up every LSF_BTN_STEP_MS while the button is down, wrapping from the top
//                   back to the lowest level; releasing the button stops it
//   double click and hold (press, release, press again and keep holding)
//                -> brightness steps down the same way, stopping at the lowest level; release stops it
// The levels are 1, 2, 3, 4 %, then every `step` percent (5, 10, ... 100 for the default step of 5). The caller
// pauses LSF_BTN_END_HOLD_MS on the brightest and dimmest level with delayNextStep().
//
// ls_formula.cpp feeds this the pin level and acts on the events.
#pragma once
#include <stdint.h>

#ifndef LSF_BTN_DEBOUNCE_MS
#define LSF_BTN_DEBOUNCE_MS 30    // a level change is ignored for this long after the previous one
#endif
#ifndef LSF_BTN_DOUBLE_MS
#define LSF_BTN_DOUBLE_MS 350     // second press must start within this long after the first release
#endif
#ifndef LSF_BTN_HOLD_MS
#define LSF_BTN_HOLD_MS 500       // pressed this long = a hold
#endif
#ifndef LSF_BTN_STEP_MS
#define LSF_BTN_STEP_MS 250       // time between brightness steps while held
#endif
#ifndef LSF_BTN_END_HOLD_MS
#define LSF_BTN_END_HOLD_MS 1500  // pause on the brightest and the dimmest level while held
#endif

enum LsfBtnEvent : uint8_t {
  LSFB_NONE = 0,
  LSFB_CLICK,       // one short press (reported once the double-click window has passed)
  LSFB_DOUBLE,      // two short presses
  LSFB_HOLD_UP,     // held: do one brightness step up (the first when the hold is recognised, then one per step time)
  LSFB_HOLD_DOWN,   // double-click-and-hold: one brightness step down
  LSFB_HOLD_END     // a hold was released
};

// Call poll() often (every loop pass) with the debounced-by-us raw state and a millisecond clock.
// It returns at most one event per call; time comparisons are wrap-safe for a 32-bit millis().
class LsfButton {
 public:
  LsfBtnEvent poll(bool pressed, uint32_t now) {
    if (!started_) {
      started_ = true;
      edgeAt_ = now - LSF_BTN_DEBOUNCE_MS;
      waitRelease_ = pressed;   // held while powering up (e.g. the BOOT button): not a gesture, wait for the release
    }
    if (waitRelease_) {
      if (pressed) return LSFB_NONE;
      waitRelease_ = false;
      edgeAt_ = now;
    }

    // A lone click is only a click once no second press can follow.
    if (!down_ && clicks_ && (uint32_t)(now - upAt_) >= LSF_BTN_DOUBLE_MS) { clicks_ = 0; return LSFB_CLICK; }

    if (pressed != down_ && (uint32_t)(now - edgeAt_) >= LSF_BTN_DEBOUNCE_MS) {
      edgeAt_ = now;
      down_ = pressed;
      if (down_) {
        downAt_ = now;
        holding_ = false;
        second_ = clicks_ != 0;   // pressed again inside the double-click window: this may become a "dim" hold
      } else if (holding_) {
        holding_ = false;
        clicks_ = 0;
        return LSFB_HOLD_END;
      } else if (clicks_) {
        clicks_ = 0;
        return LSFB_DOUBLE;
      } else {
        clicks_ = 1;
        upAt_ = now;
      }
    }

    if (down_) {
      if (!holding_ && (uint32_t)(now - downAt_) >= LSF_BTN_HOLD_MS) {
        holding_ = true;
        clicks_ = 0;            // a press that turns into a hold cancels a pending click
        nextAt_ = now + LSF_BTN_STEP_MS;
        return second_ ? LSFB_HOLD_DOWN : LSFB_HOLD_UP;
      }
      if (holding_ && (int32_t)(now - nextAt_) >= 0) {
        nextAt_ = now + LSF_BTN_STEP_MS;
        return second_ ? LSFB_HOLD_DOWN : LSFB_HOLD_UP;
      }
    }
    return LSFB_NONE;
  }

  // Call right after handling a hold step to make the next one come `ms` from `now` instead of one step time.
  void delayNextStep(uint32_t now, uint32_t ms) { nextAt_ = now + ms; }

  bool holding() const { return holding_; }
  void reset() { *this = LsfButton(); }

 private:
  bool started_ = false, waitRelease_ = false, down_ = false, holding_ = false, second_ = false;
  uint8_t clicks_ = 0;
  uint32_t edgeAt_ = 0, downAt_ = 0, upAt_ = 0, nextAt_ = 0;
};

// Brightness levels, in percent: 1, 2, 3, 4 (only those below the step size), then every `stepPct`: 5, 10, ... 100 for the
// default step of 5. `stepPct` is limited to 1..50.
inline unsigned lsfPct(uint8_t bri) { return (unsigned(bri) * 100 + 127) / 255; }   // nearest percent (0 for bri 0)
inline uint8_t lsfBriFromPct(unsigned pct) {
  const unsigned v = (pct * 255 + 50) / 100;
  return uint8_t(v < 1 ? 1 : v > 255 ? 255 : v);
}
inline unsigned lsfClampStep(uint8_t stepPct) { return stepPct < 1 ? 1 : stepPct > 50 ? 50 : stepPct; }

// The level above `bri`: 1% -> 2% -> 3% -> 4% -> 5% -> 10% -> ... -> 100% -> back to 1%. From off (0) it is 1%.
inline uint8_t lsfNextBrightness(uint8_t bri, uint8_t stepPct) {
  const unsigned step = lsfClampStep(stepPct);
  const unsigned fine = step - 1 < 4 ? step - 1 : 4;       // the 1..4 % levels that sit below the first step
  const unsigned pct = bri == 0 ? 0 : lsfPct(bri);
  unsigned next = pct < fine ? pct + 1 : (pct / step + 1) * step;
  if (pct >= 100) next = 1;                                 // past the top: wrap to the lowest level
  else if (next > 100) next = 100;                          // 100 % is always a level, even if the step does not divide it
  return lsfBriFromPct(next);
}

// The level below `bri`: 100% -> 95% -> ... -> 5% -> 4% -> 3% -> 2% -> 1%, and it stays at 1% (it never turns the display
// off). From off (0) it stays 0.
inline uint8_t lsfPrevBrightness(uint8_t bri, uint8_t stepPct) {
  if (bri == 0) return 0;
  const unsigned step = lsfClampStep(stepPct);
  const unsigned fine = step - 1 < 4 ? step - 1 : 4;
  const unsigned pct = lsfPct(bri);
  unsigned prev;
  if (pct > step) prev = (pct % step == 0) ? pct - step : (pct / step) * step;
  else prev = pct > 1 ? (pct - 1 < fine ? pct - 1 : fine) : 1;   // at or below the first step: walk the fine levels
  if (prev < 1) prev = 1;
  return lsfBriFromPct(prev);
}

// True on the brightest and the dimmest level: where a hold pauses.
inline bool lsfAtBrightnessEnd(uint8_t bri) { return bri > 0 && (lsfPct(bri) >= 100 || lsfPct(bri) <= 1); }

// The preset ids (1..250) that exist, kept as a bitmap so "the next one after N" is cheap and always in order.
class LsfIdSet {
 public:
  static const uint8_t kMax = 250;
  void clear() { for (uint8_t k = 0; k < 8; k++) w_[k] = 0; }
  void add(unsigned id) { if (id >= 1 && id <= kMax) w_[id >> 5] |= 1u << (id & 31); }
  bool has(unsigned id) const { return id >= 1 && id <= kMax && (w_[id >> 5] >> (id & 31) & 1u); }
  unsigned count() const { unsigned n = 0; for (unsigned id = 1; id <= kMax; id++) n += has(id); return n; }
  // The smallest id greater than `cur`, wrapping to the smallest id overall; 0 when the set is empty.
  unsigned next(unsigned cur) const {
    for (unsigned id = cur + 1; id <= kMax; id++) if (has(id)) return id;
    for (unsigned id = 1; id <= kMax; id++) if (has(id)) return id;
    return 0;
  }
 private:
  uint32_t w_[8] = {0, 0, 0, 0, 0, 0, 0, 0};
};
