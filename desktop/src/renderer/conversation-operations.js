'use strict';

(function exposeConversationOperations(global) {
  function beginActiveConversationDeletion(selectionEpoch, nextConversation) {
    const currentEpoch = Number.isInteger(selectionEpoch) ? selectionEpoch : 0;
    return Object.freeze({
      fallbackSelectionEpoch: currentEpoch + 1,
      activeId: nextConversation && typeof nextConversation.id === 'string'
        ? nextConversation.id
        : null,
      loading: Boolean(nextConversation && typeof nextConversation.id === 'string'),
    });
  }

  function canRestoreDeletedActiveConversation(currentEpoch, fallbackSelectionEpoch) {
    return currentEpoch === fallbackSelectionEpoch;
  }

  global.AstraRelayConversationOperations = Object.freeze({
    beginActiveConversationDeletion,
    canRestoreDeletedActiveConversation,
  });
}(typeof window === 'undefined' ? globalThis : window));
