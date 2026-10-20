const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('reset code controls call the protected endpoint and start the returned cooldown', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'reset_password.js'), 'utf8');

  assert.match(source, /\/api\/auth\/password-reset-code/);
  assert.match(source, /X-CSRF-Token/);
  assert.match(source, /startResetCodeCountdown/);
});
