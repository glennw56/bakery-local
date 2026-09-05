const fs = require("fs");
const vm = require("vm");
const store = { local: {}, session: {} };
function memory(kind) {
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store[kind], k) ? store[kind][k] : null),
    setItem: (k, v) => { store[kind][k] = String(v); },
  };
}
const ctx = {
  window: {},
  document: { body: { addEventListener: () => {} } },
  localStorage: memory("local"),
  sessionStorage: memory("session"),
};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
ctx.window.kdsRememberCleared("ORDER_A");
if (store.local.kds_cleared !== "ORDER_A") process.exit(2);
if (store.session.kds_cleared !== "ORDER_A") process.exit(3);
ctx.window.kdsRememberCleared("ORDER_A");
if (store.local.kds_cleared !== "ORDER_A") process.exit(4);
ctx.window.kdsRememberCleared("ORDER_B");
if (store.local.kds_cleared !== "ORDER_A,ORDER_B") process.exit(5);
if (store.session.kds_cleared !== "ORDER_A,ORDER_B") process.exit(6);
if (ctx.window.kdsLoadCleared().join(",") !== "ORDER_A,ORDER_B") process.exit(7);
