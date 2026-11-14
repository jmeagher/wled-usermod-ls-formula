// Host tests for lsf_button.h: the gesture state machine, the brightness steps and the preset id set.
#include "../lsf_button.h"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <utility>
#include <vector>

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)
#define CHECK_EQ(a, b) do { long long x_ = (a), y_ = (b); if (x_ != y_) { \
  std::printf("FAIL %s:%d  %s == %s  (%lld vs %lld)\n", __FILE__, __LINE__, #a, #b, x_, y_); failures++; } } while (0)

struct Ev { uint32_t at; LsfBtnEvent e; };

// `levels` = (time, pressed) changes in order; the button is polled every `pollMs` until `endMs`. `t0` offsets the clock.
static std::vector<Ev> run(const std::vector<std::pair<uint32_t, bool>>& levels, uint32_t endMs, uint32_t t0 = 1000,
                           bool initial = false, uint32_t pollMs = 1) {
  LsfButton b;
  std::vector<Ev> out;
  size_t k = 0;
  bool level = initial;
  for (uint32_t t = 0; t <= endMs; t += pollMs) {
    while (k < levels.size() && levels[k].first <= t) level = levels[k++].second;
    LsfBtnEvent e = b.poll(level, t0 + t);
    if (e != LSFB_NONE) out.push_back({t, e});
  }
  return out;
}
static int count(const std::vector<Ev>& v, LsfBtnEvent e) { int n = 0; for (auto& x : v) n += x.e == e; return n; }

static void testClick() {
  auto ev = run({{100, true}, {180, false}}, 2000);
  CHECK_EQ(ev.size(), 1u);
  CHECK(ev[0].e == LSFB_CLICK);
  CHECK(ev[0].at >= 180 + LSF_BTN_DOUBLE_MS);          // only reported once a second press can no longer follow
  CHECK(ev[0].at < 180 + LSF_BTN_DOUBLE_MS + 5);
}

static void testDouble() {
  auto ev = run({{100, true}, {160, false}, {300, true}, {360, false}}, 2000);
  CHECK_EQ(ev.size(), 1u);
  CHECK(ev[0].e == LSFB_DOUBLE);
  CHECK_EQ(ev[0].at, 360);                              // immediately on the second release
}

static void testTwoSlowClicksAreTwoClicks() {
  auto ev = run({{100, true}, {160, false}, {900, true}, {960, false}}, 3000);
  CHECK_EQ(count(ev, LSFB_CLICK), 2);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 0);
}

static void testTripleIsDoublePlusClick() {
  auto ev = run({{100, true}, {160, false}, {300, true}, {360, false}, {500, true}, {560, false}}, 3000);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 1);
  CHECK_EQ(count(ev, LSFB_CLICK), 1);
}

static void testHoldStepsThenStopsOnRelease() {
  auto ev = run({{100, true}, {2000, false}}, 4000);   // held 1.9 s
  CHECK_EQ(count(ev, LSFB_CLICK), 0);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 0);
  CHECK_EQ(count(ev, LSFB_HOLD_END), 1);
  CHECK(ev.back().e == LSFB_HOLD_END);
  CHECK_EQ(ev.back().at, 2000);
  int steps = count(ev, LSFB_HOLD_STEP);
  // first step at 500 ms, then one per 250 ms: 500, 750, ..., 1750 (+ possibly 2000 is the release instead)
  CHECK_EQ(steps, 6);
  CHECK_EQ(ev[0].at, 100 + LSF_BTN_HOLD_MS);
  CHECK_EQ(ev[1].at - ev[0].at, LSF_BTN_STEP_MS);
  for (auto& x : ev) CHECK(x.at <= 2000);              // nothing after the release
}

static void testShortPressJustUnderHoldIsClick() {
  auto ev = run({{100, true}, {100 + LSF_BTN_HOLD_MS - 20, false}}, 2000);
  CHECK_EQ(count(ev, LSFB_CLICK), 1);
  CHECK_EQ(count(ev, LSFB_HOLD_STEP), 0);
}

static void testClickThenHoldCancelsTheClick() {
  auto ev = run({{100, true}, {160, false}, {300, true}, {1200, false}}, 3000);
  CHECK_EQ(count(ev, LSFB_CLICK), 0);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 0);
  CHECK(count(ev, LSFB_HOLD_STEP) >= 2);
  CHECK_EQ(count(ev, LSFB_HOLD_END), 1);
}

static void testBounceIsIgnored() {
  // contact bounce on press and release (edges 5 ms apart) must still be one click
  auto ev = run({{100, true}, {105, false}, {110, true}, {200, false}, {205, true}, {210, false}}, 2000);
  CHECK_EQ(ev.size(), 1u);
  CHECK(ev[0].e == LSFB_CLICK);
}

static void testHeldAtBootIsIgnoredUntilReleased() {
  auto ev = run({{3000, false}}, 6000, 1000, true);    // pressed from the start, released at 3 s
  CHECK_EQ(ev.size(), 0u);
  auto ev2 = run({{3000, false}, {3500, true}, {3560, false}}, 6000, 1000, true);   // then a normal click works
  CHECK_EQ(ev2.size(), 1u);
  CHECK(ev2[0].e == LSFB_CLICK);
}

static void testClockWrap() {
  // millis() wraps at 2^32; a double click across the wrap must still be a double click
  const uint32_t t0 = 0xFFFFFFFFu - 200;
  auto ev = run({{100, true}, {160, false}, {300, true}, {360, false}}, 2000, t0);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 1);
  auto hold = run({{100, true}, {1500, false}}, 3000, t0);
  CHECK(count(hold, LSFB_HOLD_STEP) >= 3);
  CHECK_EQ(count(hold, LSFB_HOLD_END), 1);
}

static void testSlowPolling() {
  // a busy main loop polling every 20 ms still gets the right gestures
  auto ev = run({{100, true}, {200, false}, {400, true}, {500, false}}, 3000, 1000, false, 20);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 1);
  auto hold = run({{100, true}, {1700, false}}, 3000, 1000, false, 20);
  CHECK(count(hold, LSFB_HOLD_STEP) >= 4);
  CHECK_EQ(count(hold, LSFB_HOLD_END), 1);
}

static void testBrightnessSteps() {
  CHECK_EQ(lsfNextBrightness(0, 5), 13);               // from off: the first step (5 %)
  CHECK_EQ(lsfNextBrightness(13, 5), 26);              // 5 % -> 10 %
  CHECK_EQ(lsfNextBrightness(128, 5), 140);            // 50 % -> 55 %
  CHECK_EQ(lsfNextBrightness(242, 5), 255);            // 95 % -> 100 %
  CHECK_EQ(lsfNextBrightness(255, 5), 13);             // wraps to the first step
  // 20 presses from the lowest step walk every 5 % level and come back around
  unsigned b = 13; int n = 0;
  do { b = lsfNextBrightness(uint8_t(b), 5); n++; } while (b != 13 && n < 100);
  CHECK_EQ(n, 20);
  // always strictly increasing until the wrap, never 0, for every starting value and several step sizes
  const uint8_t steps[] = {1, 5, 10, 25, 50, 0, 200};
  for (uint8_t st : steps) {
    for (unsigned v = 0; v <= 255; v++) {
      unsigned r = lsfNextBrightness(uint8_t(v), st);
      CHECK(r >= 1);
      CHECK(r > v || r < v);                          // always changes
    }
  }
  for (unsigned v = 1; v < 255; v++) CHECK(lsfNextBrightness(uint8_t(v), 5) > v || lsfNextBrightness(uint8_t(v), 5) == 13);
}

static void testIdSet() {
  LsfIdSet s;
  CHECK_EQ(s.count(), 0u);
  CHECK_EQ(s.next(0), 0u);                             // empty set
  s.add(3); s.add(1); s.add(250); s.add(7);
  s.add(0); s.add(251); s.add(255); s.add(1000);       // out of range: ignored
  CHECK_EQ(s.count(), 4u);
  CHECK_EQ(s.next(0), 1u);
  CHECK_EQ(s.next(1), 3u);
  CHECK_EQ(s.next(3), 7u);
  CHECK_EQ(s.next(7), 250u);
  CHECK_EQ(s.next(250), 1u);                           // wraps
  CHECK_EQ(s.next(255), 1u);                           // a temporary preset (255) is not in the list: start over
  CHECK_EQ(s.next(4), 7u);                             // current preset need not be in the set
  s.clear();
  CHECK_EQ(s.count(), 0u);
  s.add(2);
  CHECK_EQ(s.next(2), 2u);                             // a single preset stays itself
}

int main() {
  testClick(); testDouble(); testTwoSlowClicksAreTwoClicks(); testTripleIsDoublePlusClick();
  testHoldStepsThenStopsOnRelease(); testShortPressJustUnderHoldIsClick(); testClickThenHoldCancelsTheClick();
  testBounceIsIgnored(); testHeldAtBootIsIgnoredUntilReleased(); testClockWrap(); testSlowPolling();
  testBrightnessSteps(); testIdSet();
  if (failures) { std::printf("%d failure(s)\n", failures); return 1; }
  std::printf("button tests: ok\n");
  return 0;
}
