// FalseHorizon's equation language: the formulas in a preset ("zoom = 1.02 + 0.05 * bass; rot =
// sin(time) * 0.01"). Our own parser: no eval, no Function, no page access. A formula is read into
// small closures that only do arithmetic on a plain object of numbers, so a preset from anyone can
// run without trust. Statements end with ";" and look like name = formula (also += -= *= /=). A
// name never set reads as 0; no loops, no strings; dividing by zero gives 0; non-numbers become 0
// and huge results clamp to +-1e6; LIMITS bound the formula size. Runs under Node too; in the
// browser it is window.FalseHorizonExpr.
(function (root) {
  const LIMITS = {
    source: 6000,
    tokens: 3000,
    statements: 250,
    depth: 24,
    names: 120,
    nameLength: 32,
  }; // big enough for long community presets, small enough that one frame stays quick

  class ExprError extends Error {}

  const fix = x => (Number.isFinite(x) ? (x > 1e6 ? 1e6 : x < -1e6 ? -1e6 : x) : 0);
  const truthy = x => Math.abs(x) > 1e-5;

  // name -> [number of arguments, function]. "if" is special: it only evaluates the branch it
  // takes.
  const FUNCTIONS = {
    sin: [1, Math.sin],
    cos: [1, Math.cos],
    tan: [1, Math.tan],
    asin: [1, Math.asin],
    acos: [1, Math.acos],
    atan: [1, Math.atan],
    atan2: [2, Math.atan2],
    sqrt: [1, x => Math.sqrt(Math.abs(x))],
    abs: [1, Math.abs],
    exp: [1, x => Math.exp(Math.min(x, 50))],
    log: [1, x => Math.log(Math.abs(x) || 1e-9)],
    log10: [1, x => Math.log10(Math.abs(x) || 1e-9)],
    band: [2, (a, b) => (truthy(a) && truthy(b) ? 1 : 0)],
    bor: [2, (a, b) => (truthy(a) || truthy(b) ? 1 : 0)],
    bnot: [1, a => (truthy(a) ? 0 : 1)],
    floor: [1, Math.floor],
    ceil: [1, Math.ceil],
    int: [1, Math.trunc],
    frac: [1, x => x - Math.floor(x)],
    sign: [1, Math.sign],
    sqr: [1, x => x * x],
    min: [2, Math.min],
    max: [2, Math.max],
    pow: [2, Math.pow],
    mod: [2, (a, b) => (b === 0 ? 0 : a - b * Math.floor(a / b))],
    above: [2, (a, b) => (a > b ? 1 : 0)],
    below: [2, (a, b) => (a < b ? 1 : 0)],
    equal: [2, (a, b) => (Math.abs(a - b) < 1e-5 ? 1 : 0)],
    sigmoid: [2, (x, k) => 1 / (1 + Math.exp(-Math.max(-50, Math.min(50, x * k))))],
    clamp: [3, (x, lo, hi) => Math.min(Math.max(x, Math.min(lo, hi)), Math.max(lo, hi))],
    mix: [3, (a, b, t) => a + (b - a) * t],
    smoothstep: [
      3,
      (a, b, x) => {
        if (a === b) return x < a ? 0 : 1;
        const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
        return t * t * (3 - 2 * t);
      },
    ],
    rand: [1, null], // filled in per program, so tests can give it a fixed sequence
    if: [3, null],
  };

  const OPERATORS = [
    '<=',
    '>=',
    '==',
    '!=',
    '&&',
    '||',
    '+=',
    '-=',
    '*=',
    '/=',
    '+',
    '-',
    '*',
    '/',
    '%',
    '^',
    '(',
    ')',
    ',',
    ';',
    '=',
    '<',
    '>',
    '!',
  ];
  const NUMBER = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/;
  const NAME = /^[A-Za-z_][A-Za-z0-9_]*/;

  function tokenize(src) {
    const tokens = [];
    let i = 0;
    while (i < src.length) {
      const ch = src[i];
      if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r') {
        i++;
        continue;
      }
      const rest = src.slice(i, i + 40);
      let m = NUMBER.exec(rest);
      if (m) {
        tokens.push({ t: 'num', v: Number(m[0]), pos: i });
        i += m[0].length;
      } else if ((m = NAME.exec(rest))) {
        if (m[0].length > LIMITS.nameLength)
          throw new ExprError(
            `the name "${m[0].slice(0, 12)}..." is too long (at character ${i + 1})`,
          );
        tokens.push({ t: 'id', v: m[0].toLowerCase(), pos: i });
        i += m[0].length;
      } else {
        const op = OPERATORS.find(o => rest.startsWith(o));
        if (!op) throw new ExprError(`unexpected "${ch}" at character ${i + 1}`);
        tokens.push({ t: 'op', v: op, pos: i });
        i += op.length;
      }
      if (tokens.length > LIMITS.tokens) throw new ExprError('this formula is too long');
    }
    return tokens;
  }

  // Turns the source text into { run(vars), reads, writes }. `vars` is a plain object (no
  // prototype) of numbers; run() changes it in place.
  function compile(source, options) {
    source = String(source == null ? '' : source);
    if (source.length > LIMITS.source)
      throw new ExprError(`this formula is longer than ${LIMITS.source} characters`);
    const rng = (options && options.rng) || Math.random;
    const tokens = tokenize(source);
    const names = new Set(),
      writes = new Set();
    let at = 0,
      depth = 0;

    const peek = () => tokens[at] || { t: 'end', v: '', pos: source.length };
    const fail = (what, tok) => {
      throw new ExprError(`${what} at character ${(tok || peek()).pos + 1}`);
    };
    const isOp = v => {
      const t = peek();
      return t.t === 'op' && t.v === v;
    };
    const take = v => {
      if (!isOp(v)) fail(`expected "${v}"`);
      at++;
    };
    const deeper = () => {
      if (++depth > LIMITS.depth) fail('this formula is nested too deeply');
    };
    const note = name => {
      if (name.startsWith('__') || name === 'constructor' || name === 'prototype')
        fail(`"${name}" is not a name you can use`);
      names.add(name);
      if (names.size > LIMITS.names) fail(`more than ${LIMITS.names} different names`);
    };

    // precedence, loosest first: ||  &&  comparisons  + -  * / %  unary - !  ^ (right to left)
    // atoms
    function orExpr() {
      let a = andExpr();
      while (isOp('||')) {
        at++;
        const l = a,
          r = andExpr();
        a = v => (truthy(l(v)) || truthy(r(v)) ? 1 : 0);
      }
      return a;
    }
    function andExpr() {
      let a = compare();
      while (isOp('&&')) {
        at++;
        const l = a,
          r = compare();
        a = v => (truthy(l(v)) && truthy(r(v)) ? 1 : 0);
      }
      return a;
    }
    function compare() {
      let a = sum();
      for (;;) {
        const t = peek();
        if (t.t !== 'op' || !['==', '!=', '<', '>', '<=', '>='].includes(t.v)) return a;
        at++;
        const l = a,
          r = sum();
        switch (t.v) {
          case '==':
            a = v => (Math.abs(l(v) - r(v)) < 1e-5 ? 1 : 0);
            break;
          case '!=':
            a = v => (Math.abs(l(v) - r(v)) >= 1e-5 ? 1 : 0);
            break;
          case '<':
            a = v => (l(v) < r(v) ? 1 : 0);
            break;
          case '>':
            a = v => (l(v) > r(v) ? 1 : 0);
            break;
          case '<=':
            a = v => (l(v) <= r(v) ? 1 : 0);
            break;
          default:
            a = v => (l(v) >= r(v) ? 1 : 0);
        }
      }
    }
    function sum() {
      let a = product();
      while (isOp('+') || isOp('-')) {
        const plus = peek().v === '+';
        at++;
        const l = a,
          r = product();
        a = plus ? v => fix(l(v) + r(v)) : v => fix(l(v) - r(v));
      }
      return a;
    }
    function product() {
      let a = unary();
      while (isOp('*') || isOp('/') || isOp('%')) {
        const op = peek().v;
        at++;
        const l = a,
          r = unary();
        if (op === '*') a = v => fix(l(v) * r(v));
        else if (op === '/')
          a = v => {
            const d = r(v);
            return d === 0 ? 0 : fix(l(v) / d);
          };
        else
          a = v => {
            const d = r(v);
            if (d === 0) return 0;
            const x = l(v);
            return fix(x - d * Math.floor(x / d));
          }; // x once: working out l twice doubled the time at every % in a chain
      }
      return a;
    }
    function unary() {
      if (isOp('-')) {
        at++;
        deeper();
        const a = unary();
        depth--;
        return v => -a(v);
      }
      if (isOp('+')) {
        at++;
        return unary();
      }
      if (isOp('!')) {
        at++;
        deeper();
        const a = unary();
        depth--;
        return v => (truthy(a(v)) ? 0 : 1);
      }
      return power();
    }
    function power() {
      const base = atom();
      if (!isOp('^')) return base;
      at++;
      deeper();
      const exponent = unary(); // right to left, and  2 ^ -1  works
      depth--;
      return v => fix(Math.pow(base(v), exponent(v)));
    }
    function atom() {
      const t = peek();
      if (t.t === 'num') {
        at++;
        const n = t.v;
        return () => n;
      }
      if (isOp('(')) {
        at++;
        deeper();
        const a = orExpr();
        depth--;
        take(')');
        return a;
      }
      if (t.t !== 'id') fail(t.t === 'end' ? 'the formula ends too soon' : `unexpected "${t.v}"`);
      at++;
      if (isOp('(')) {
        const entry = Object.prototype.hasOwnProperty.call(FUNCTIONS, t.v) ? FUNCTIONS[t.v] : null;
        if (!entry) fail(`there is no function called "${t.v}"`, t);
        at++;
        deeper();
        const args = [];
        if (!isOp(')')) {
          do {
            args.push(orExpr());
          } while (isOp(',') && ++at);
        }
        depth--;
        take(')');
        if (args.length !== entry[0])
          fail(
            `${t.v}() needs ${entry[0]} value${entry[0] === 1 ? '' : 's'}, not ${args.length}`,
            t,
          );
        if (t.v === 'if') {
          const [c, a, b] = args;
          return v => (truthy(c(v)) ? a(v) : b(v));
        }
        if (t.v === 'rand') {
          const [n] = args;
          return v => fix(rng() * n(v));
        }
        const fn = entry[1];
        if (args.length === 1) {
          const [a] = args;
          return v => fix(fn(a(v)));
        }
        if (args.length === 2) {
          const [a, b] = args;
          return v => fix(fn(a(v), b(v)));
        }
        const [a, b, c] = args;
        return v => fix(fn(a(v), b(v), c(v)));
      }
      if (Object.prototype.hasOwnProperty.call(FUNCTIONS, t.v))
        fail(`"${t.v}" is a function and needs ( )`, t);
      note(t.v);
      const name = t.v;
      return v => {
        const x = v[name];
        return x === undefined ? 0 : x;
      };
    }

    const statements = [];
    while (peek().t !== 'end') {
      if (isOp(';')) {
        at++;
        continue;
      }
      const target = peek();
      const op = tokens[at + 1];
      if (
        target.t !== 'id' ||
        !op ||
        op.t !== 'op' ||
        !['=', '+=', '-=', '*=', '/='].includes(op.v)
      )
        fail('each statement must look like  name = formula', target);
      if (Object.prototype.hasOwnProperty.call(FUNCTIONS, target.v))
        fail(`"${target.v}" is a function name and cannot be set`, target);
      note(target.v);
      writes.add(target.v);
      at += 2;
      const value = orExpr();
      if (!isOp(';') && peek().t !== 'end') fail(`unexpected "${peek().v}"`);
      const name = target.v;
      if (statements.length >= LIMITS.statements)
        fail(`more than ${LIMITS.statements} statements`, target);
      if (op.v === '=')
        statements.push(v => {
          v[name] = fix(value(v));
        });
      else if (op.v === '+=')
        statements.push(v => {
          v[name] = fix((v[name] || 0) + value(v));
        });
      else if (op.v === '-=')
        statements.push(v => {
          v[name] = fix((v[name] || 0) - value(v));
        });
      else if (op.v === '*=')
        statements.push(v => {
          v[name] = fix((v[name] || 0) * value(v));
        });
      else
        statements.push(v => {
          const d = value(v);
          v[name] = d === 0 ? 0 : fix((v[name] || 0) / d);
        });
    }
    const run = vars => {
      for (let i = 0; i < statements.length; i++) statements[i](vars);
    };
    return { run, reads: [...names], writes: [...writes], size: statements.length };
  }

  // For checking a formula that came from a file: { ok: true } or { ok: false, error }.
  function check(source) {
    try {
      compile(source);
      return { ok: true };
    } catch (e) {
      if (e instanceof ExprError) return { ok: false, error: e.message };
      throw e;
    }
  }

  const api = {
    compile,
    check,
    tokenize,
    LIMITS,
    ExprError,
    FUNCTION_NAMES: Object.keys(FUNCTIONS),
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FalseHorizonExpr = api;
})(typeof window !== 'undefined' ? window : globalThis);
