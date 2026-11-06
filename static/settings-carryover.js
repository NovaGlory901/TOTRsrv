// DejaVuAmp and FalseHorizons used to keep their browser settings under "totr."-prefixed keys. They
// no longer know those names, so this copies a person's old settings to the new key once, the first
// time they load. The old key is left alone. No page access, so tests/test_radio.py can run it
// under Node. In the browser it is window.SettingsCarryOver.
(function (root) {
  const OLD_PREFIX = 'totr.';

  function copy(storage, key) {
    try {
      if (storage.getItem(key) !== null) return false;
      const old = storage.getItem(OLD_PREFIX + key);
      if (old === null) return false;
      storage.setItem(key, old);
      return true;
    } catch (e) {
      return false;
    }
  }

  // keys: the current storage keys for this person (DejaVuAmpCore.storageKey(name),
  // FalseHorizonCore.storageKey(name)).
  function run(storage, keys) {
    if (!storage || !Array.isArray(keys)) return 0;
    return keys.filter(key => typeof key === 'string' && copy(storage, key)).length;
  }

  const api = { run };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SettingsCarryOver = api;
})(typeof window !== 'undefined' ? window : globalThis);
