// FalseHorizon's built-in presets, all original. Each is plain data in the shape of a preset
// file (see sanitizePreset in falsehorizon-core.js), which checks them all on first use. To make
// your own, copy one and change the numbers.
//
// Names you can use in formulas: time, frame, bass, mid, treb, bass_att, mid_att, treb_att, level,
// beat, aspect (inputs, 0..1 except time/frame); zoom, rot, cx, cy, dx, dy, sx, sy, warp, decay,
// wave_r/g/b/a/scale/x/y, gamma, hue, bright, solarize, echo_alpha, echo_zoom, echo_orient, cover
// (set them); in "pixel": x, y, rad, ang plus the warp values; q1..q8 carry numbers from frame to
// pixel, shapes and waves. In waves: sample, value1 (the sound), value2 (the spectrum), x, y, r, g,
// b, a.
(function (root) {
  const LIST = [
    {
      name: 'Slow Tide',
      author: 'FalseHorizons',
      decay: 0.975,
      zoom: 1.004,
      warp: 1.2,
      wave: { mode: 0, r: 0.4, g: 0.8, b: 1, a: 0.9, scale: 1, thick: 2 },
      frame:
        'zoom = 1.004 + 0.012 * bass; rot = 0.004 * sin(time * 0.2); wave_r = 0.5 + 0.5 * sin(time * 0.37); wave_g = 0.5 + 0.5 * sin(time * 0.41 + 2); wave_scale = 0.8 + bass * 0.9; hue = time * 0.02',
    },

    {
      name: 'Phosphor Bloom',
      author: 'FalseHorizons',
      decay: 0.95,
      zoom: 1.01,
      rot: 0.01,
      warp: 0.5,
      wave: { mode: 1, r: 0.2, g: 1, b: 0.35, a: 0.7, scale: 1, thick: 2 },
      color: { gamma: 1.1 },
      frame:
        'rot = 0.01 + 0.01 * sin(time * 0.3); zoom = 1.01 + 0.03 * beat; wave_y = 0.5 + 0.18 * sin(time * 0.5); wave_scale = 0.6 + mid * 1.5',
    },

    {
      name: 'Hallway of Mirrors',
      author: 'FalseHorizons',
      decay: 0.93,
      zoom: 1.02,
      wave: { mode: 0, r: 1, g: 0.5, b: 0.9, a: 0.8, scale: 0.7, thick: 2 },
      color: { echoAlpha: 0.25, echoZoom: 2, echoOrient: 3 },
      frame:
        'zoom = 1.015 + 0.02 * bass; echo_zoom = 1.8 + 0.4 * sin(time * 0.2); wave_r = 0.6 + 0.4 * sin(time * 0.5); wave_b = 0.6 + 0.4 * cos(time * 0.4)',
      shapes: [
        {
          sides: 4,
          rad: 0.12,
          r: 0.9,
          g: 0.6,
          b: 1,
          a: 0.3,
          r2: 0.3,
          g2: 0.2,
          b2: 0.8,
          a2: 0.05,
          frame: 'ang = time * 0.4; rad = 0.08 + 0.14 * bass',
        },
      ],
    },

    {
      name: 'Soft Static',
      author: 'FalseHorizons',
      decay: 0.93,
      zoom: 1.002,
      warp: 0.4,
      wave: { mode: 3, r: 0.9, g: 0.9, b: 1, a: 0.85, scale: 0.9, thick: 2 },
      frame: 'q1 = floor(time * 3); hue = 0.1 * sin(time * 0.1)',
      pixel:
        'dx = (frac(sin(x * 91.7 + y * 57.3 + q1) * 437.5) - 0.5) * 0.012; dy = (frac(sin(x * 41.3 + y * 83.1 + q1 * 1.7) * 437.5) - 0.5) * 0.012',
    },

    {
      name: 'Spiral Lullaby',
      author: 'FalseHorizons',
      decay: 0.97,
      zoom: 1.01,
      wave: { mode: 0, r: 1, g: 0.7, b: 0.5, a: 0.8, scale: 0.55, thick: 2 },
      frame: 'q1 = sin(time * 0.3); hue = time * 0.015; wave_scale = 0.5 + treb',
      pixel: 'rot = 0.06 * (1 - rad) * q1; zoom = 1.01 + 0.015 * sin(ang * 3 + time)',
    },

    {
      name: 'Window Seat',
      author: 'FalseHorizons',
      decay: 0.94,
      dx: 0.004,
      warp: 2,
      wave: { mode: 4, r: 0.7, g: 0.85, b: 1, a: 0.55, scale: 1, thick: 2 },
      frame: 'dx = 0.004 + 0.006 * bass; zoom = 1.002; wave_y = 0.5 + 0.12 * sin(time * 0.25)',
    },

    {
      name: 'Night Bus',
      author: 'FalseHorizons',
      decay: 0.95,
      dx: -0.006,
      zoom: 1.003,
      warp: 0.8,
      wave: { mode: 1, r: 1, g: 0.4, b: 0.7, a: 0.6, scale: 0.8, thick: 3 },
      frame: 'dx = -0.004 - 0.01 * bass; hue = time * 0.03; wave_y = 0.3 + 0.1 * sin(time * 0.6)',
    },

    {
      name: 'Cathedral Fog',
      author: 'FalseHorizons',
      decay: 0.972,
      zoom: 1.006,
      warp: 0.6,
      wave: { mode: 0, r: 0.8, g: 0.8, b: 1, a: 0.35, scale: 1.4, thick: 3 },
      frame:
        'wave_scale = 1.1 + 0.8 * bass; zoom = 1.005 + 0.008 * mid; hue = 0.04 * sin(time * 0.07)',
      pixel: 'zoom = zoom + 0.01 * rad',
    },

    {
      name: 'Kaleidoscope Nap',
      author: 'FalseHorizons',
      decay: 0.96,
      wave: { mode: 2, r: 1, g: 0.8, b: 0.4, a: 0.7, scale: 0.8, thick: 2 },
      frame: 'q1 = time * 0.5; q2 = 0.01 + 0.02 * bass; hue = time * 0.01',
      pixel:
        'zoom = 1 + (0.015 + q2) * sin(ang * 6 + q1) * rad + 0.01; rot = 0.01 * sin(rad * 8 - time)',
      shapes: [
        {
          sides: 6,
          rad: 0.18,
          r: 1,
          g: 0.5,
          b: 0.8,
          a: 0.35,
          r2: 0.4,
          g2: 0.8,
          b2: 1,
          a2: 0.05,
          frame: 'ang = -time * 0.3; rad = 0.12 + 0.1 * bass',
        },
      ],
    },

    {
      name: 'Orbit Lights',
      author: 'FalseHorizons',
      decay: 0.955,
      zoom: 1.006,
      warp: 0.3,
      wave: { a: 0 },
      color: { gamma: 1.05 },
      frame: 'wave_a = 0; q1 = time * 0.7',
      waves: [
        {
          samples: 160,
          thick: 2,
          point:
            'x = 0.5 + 0.3 * sin(sample * 12.566 + time) / aspect * 1.4; y = 0.5 + 0.3 * cos(sample * 18.85 + time * 0.7) + value1 * 0.08; r = 0.5 + 0.5 * sin(sample * 6.283); g = 0.6; b = 1 - r',
        },
      ],
      shapes: [
        {
          sides: 5,
          rad: 0.04,
          r: 1,
          g: 0.9,
          b: 0.5,
          a: 0.9,
          r2: 1,
          g2: 0.4,
          b2: 0.2,
          a2: 0,
          frame:
            'x = 0.5 + 0.28 * cos(q1) / aspect; y = 0.5 + 0.28 * sin(q1); rad = 0.03 + 0.05 * bass',
        },
        {
          sides: 5,
          rad: 0.04,
          r: 0.5,
          g: 0.9,
          b: 1,
          a: 0.9,
          r2: 0.2,
          g2: 0.4,
          b2: 1,
          a2: 0,
          frame:
            'x = 0.5 + 0.2 * cos(-q1 * 1.3 + 2) / aspect; y = 0.5 + 0.2 * sin(-q1 * 1.3 + 2); rad = 0.03 + 0.05 * treb',
        },
      ],
    },

    {
      name: 'Dial Tone',
      author: 'FalseHorizons',
      decay: 0.94,
      zoom: 1.02,
      rot: 0.01,
      wave: { mode: 2, r: 0.5, g: 1, b: 0.9, a: 0.9, scale: 1.1, thick: 2 },
      frame:
        'rot = 0.008 * sin(time * 0.2) + 0.006; zoom = 1.015 + 0.02 * beat; wave_scale = 0.8 + bass; hue = time * 0.01',
    },

    {
      name: 'Rain on Glass',
      author: 'FalseHorizons',
      decay: 0.95,
      dy: -0.008,
      warp: 1.5,
      wave: { mode: 1, r: 0.6, g: 0.8, b: 1, a: 0.55, scale: 0.7, y: 0.85, thick: 2 },
      frame: 'dy = -0.006 - 0.008 * bass; dx = 0.002 * sin(time * 0.3); wave_scale = 0.4 + mid',
      pixel: 'dx = dx + (frac(sin(floor(x * 24) * 12.9) * 437.5) - 0.5) * 0.004',
    },

    {
      name: 'Lava Lamp',
      author: 'FalseHorizons',
      decay: 0.955,
      warp: 3,
      zoom: 1,
      wave: { mode: 0, a: 0 },
      color: { gamma: 1.1 },
      frame: 'wave_a = 0; q1 = time * 0.23; hue = 0.02 * time',
      shapes: [
        {
          sides: 40,
          rad: 0.18,
          r: 1,
          g: 0.4,
          b: 0.3,
          a: 0.7,
          r2: 1,
          g2: 0.2,
          b2: 0.5,
          a2: 0,
          frame:
            'x = 0.5 + 0.28 * sin(q1 * 1.1) / aspect; y = 0.5 + 0.3 * sin(q1 * 0.7 + 1); rad = 0.15 + 0.1 * bass',
        },
        {
          sides: 40,
          rad: 0.2,
          r: 1,
          g: 0.8,
          b: 0.3,
          a: 0.7,
          r2: 1,
          g2: 0.5,
          b2: 0.2,
          a2: 0,
          frame:
            'x = 0.5 + 0.3 * sin(q1 * 0.8 + 2) / aspect; y = 0.5 + 0.3 * sin(q1 * 1.3 + 4); rad = 0.16 + 0.1 * mid',
        },
        {
          sides: 40,
          rad: 0.16,
          r: 0.6,
          g: 0.4,
          b: 1,
          a: 0.7,
          r2: 0.3,
          g2: 0.2,
          b2: 1,
          a2: 0,
          frame:
            'x = 0.5 + 0.25 * sin(q1 * 0.6 + 4) / aspect; y = 0.5 + 0.28 * sin(q1 * 0.9 + 2); rad = 0.14 + 0.1 * treb',
        },
      ],
    },

    {
      name: 'Heartbeat',
      author: 'FalseHorizons',
      decay: 0.93,
      zoom: 1.01,
      warp: 0.2,
      wave: { mode: 0, r: 1, g: 0.3, b: 0.5, a: 0.6, scale: 0.4, thick: 2 },
      frame:
        'zoom = 1.01 + 0.05 * beat; wave_scale = 0.35 + 0.5 * beat; hue = 0.03 * sin(time * 0.1)',
      shapes: [
        {
          sides: 40,
          rad: 0.1,
          r: 1,
          g: 0.4,
          b: 0.6,
          a: 0.8,
          r2: 1,
          g2: 0.1,
          b2: 0.3,
          a2: 0.1,
          frame: 'rad = 0.07 + 0.22 * beat + 0.05 * bass; a = 0.4 + 0.5 * beat',
        },
      ],
    },

    {
      name: 'Polaroid Drift',
      author: 'FalseHorizons',
      decay: 0.97,
      zoom: 1.002,
      rot: 0.001,
      warp: 0.8,
      wave: { mode: 0, r: 1, g: 1, b: 1, a: 0.3, scale: 0.5 },
      color: { cover: 0.85 },
      frame:
        'rot = 0.002 * sin(time * 0.15); zoom = 1.002 + 0.004 * bass; wave_a = 0.15 + 0.3 * beat; hue = 0.02 * sin(time * 0.05)',
    },

    {
      name: 'Aurora Wire',
      author: 'FalseHorizons',
      decay: 0.98,
      zoom: 1.003,
      dy: 0.002,
      warp: 0.5,
      wave: { mode: 0, a: 0 },
      frame: 'wave_a = 0; dy = 0.002 + 0.004 * bass',
      waves: [
        {
          samples: 128,
          thick: 2,
          point:
            'x = sample; y = 0.55 + 0.15 * sin(sample * 9 + time * 0.7) + value1 * 0.08; r = 0.2; g = 0.9; b = 0.7 + 0.3 * sin(sample * 5 + time)',
        },
        {
          samples: 128,
          thick: 2,
          a: 0.6,
          point:
            'x = sample; y = 0.4 + 0.12 * sin(sample * 7 - time * 0.5) + value1 * 0.06; r = 0.7; g = 0.4; b = 1',
        },
      ],
    },

    {
      name: 'Cut-up Tape',
      author: 'FalseHorizons',
      decay: 0.975,
      zoom: 1.002,
      warp: 0.4,
      color: { cover: 0.6 },
      wave: { mode: 3, r: 1, g: 1, b: 1, a: 0.5, scale: 0.7 },
      frame:
        'bc = bc + above(beat, 0.98); q1 = bc * 13.7; q2 = 0.006 + 0.05 * bass; hue = 0.003 * time',
      pixel:
        'h1 = frac(sin(floor(x * 16) * 127.1 + floor(y * 9) * 311.7 + q1) * 43758.5); h2 = frac(sin(floor(x * 16) * 269.5 + floor(y * 9) * 183.3 + q1) * 43758.5); dx = (h1 - 0.5) * q2; dy = (h2 - 0.5) * q2',
    },
  ];

  const api = { LIST };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FalseHorizonPresets = api;
})(typeof window !== 'undefined' ? window : globalThis);
