// The radio's testable logic: the song history behind the Previous button, and (for the classic
// photo-smear visuals) turning a frequency reading into bass/mid/treble and a beat. (Volume, speed
// and the equalizer are DejaVuAmp's: dejavuamp-core.js; the main visuals' settings and logic are
// FalseHorizon's: falsehorizon-core.js.) No page access here, so tests/test_radio.py can run it
// under Node. In the browser it is window.RadioCore.
(function (root) {
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // The songs this radio session has played. Previous steps back through them; Next steps forward
  // again before a new random song is picked.
  class SongHistory {
    constructor(max = 200) {
      this.items = [];
      this.i = -1;
      this.max = max;
    }
    get current() {
      return this.items[this.i] || null;
    }
    canBack() {
      return this.i > 0;
    }
    canForward() {
      return this.i < this.items.length - 1;
    }
    push(song) {
      // a newly picked song, always at the end
      this.items.length = this.i + 1;
      this.items.push(song);
      if (this.items.length > this.max) this.items.shift();
      this.i = this.items.length - 1;
    }
    back() {
      if (!this.canBack()) return null;
      this.i -= 1;
      return this.current;
    }
    forward() {
      if (!this.canForward()) return null;
      this.i += 1;
      return this.current;
    }
  }

  // Frequency bytes (0-255 per bin, as the browser's analyser gives them) -> how much bass, mid and
  // treble, 0..1.
  function bandEnergies(bytes, sampleRate) {
    const binHz = sampleRate / 2 / bytes.length;
    const mean = (fromHz, toHz) => {
      const a = Math.max(0, Math.floor(fromHz / binHz)),
        b = Math.min(bytes.length, Math.max(a + 1, Math.ceil(toHz / binHz)));
      let sum = 0;
      for (let k = a; k < b; k++) sum += bytes[k];
      return sum / (b - a) / 255;
    };
    const bass = mean(20, 250),
      mid = mean(250, 2500),
      treble = mean(2500, 12000);
    return { bass, mid, treble, level: (bass * 2 + mid + treble) / 4 };
  }

  // A beat is a bass reading well above its recent average, with a minimum gap so effects can never
  // flash faster than two and a half times a second.
  class BeatDetector {
    constructor(minGapMs = 400, ratio = 1.35, floor = 0.25) {
      this.avg = 0;
      this.last = -1e9;
      this.minGapMs = minGapMs;
      this.ratio = ratio;
      this.floor = floor;
    }
    update(bass, nowMs) {
      const beat =
        bass > this.floor && bass > this.avg * this.ratio && nowMs - this.last >= this.minGapMs;
      this.avg += (bass - this.avg) * 0.08;
      if (beat) this.last = nowMs;
      return beat;
    }
  }

  // Smooth a value toward a target: quick to rise, slower to fall, so visuals pulse instead of
  // flicker.
  const follow = (current, target, up = 0.5, down = 0.08) =>
    current + (target - current) * (target > current ? up : down);

  Object.assign(root, { clamp, SongHistory, bandEnergies, BeatDetector, follow });
})(typeof module !== 'undefined' && module.exports ? module.exports : (window.RadioCore = {}));
