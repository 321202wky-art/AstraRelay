const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function operations() {
  const context = {};
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'renderer', 'conversation-operations.js'),
    'utf8',
  );
  vm.runInNewContext(source, context);
  return context.AstraRelayConversationOperations;
}

test('active deletion allocates exactly one fallback selection epoch', () => {
  const transition = operations().beginActiveConversationDeletion(12, { id: 'next' });

  assert.deepEqual({ ...transition }, {
    fallbackSelectionEpoch: 13,
    activeId: 'next',
    loading: true,
  });
});

test('active deletion recovery yields to a later user selection', () => {
  const api = operations();
  const transition = api.beginActiveConversationDeletion(3, { id: 'next' });

  assert.equal(
    api.canRestoreDeletedActiveConversation(
      transition.fallbackSelectionEpoch,
      transition.fallbackSelectionEpoch,
    ),
    true,
  );
  assert.equal(
    api.canRestoreDeletedActiveConversation(
      transition.fallbackSelectionEpoch + 1,
      transition.fallbackSelectionEpoch,
    ),
    false,
  );
});
