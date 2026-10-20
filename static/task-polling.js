(function exposeTaskPolling(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.TaskPolling = api;
})(globalThis, function createTaskPollingApi() {
  function createTaskResultTracker() {
    const emittedReplies = new Set();
    const emittedFailures = new Set();
    const emittedSummaryIds = new Set();

    return {
      consume(task) {
        const models = task.models || {};
        const events = [];
        const thinkingModels = [];

        for (const [model, result] of Object.entries(models)) {
          if (result.state === 'thinking') {
            thinkingModels.push(model);
          } else if (result.state === 'completed' && !emittedReplies.has(model)) {
            emittedReplies.add(model);
            events.push({ type: 'reply', model, content: result.message || '' });
          } else if (
            (result.state === 'failed' || result.state === 'timed_out') &&
            !emittedFailures.has(model)
          ) {
            emittedFailures.add(model);
            events.push({ type: 'failure', model, state: result.state });
          }
        }

        const summary = task.summary;
        if (summary && summary.id && !emittedSummaryIds.has(summary.id)) {
          emittedSummaryIds.add(summary.id);
          events.push({
            type: 'summary',
            id: summary.id,
            model: summary.model,
            content: summary.content,
            message_type: summary.message_type,
          });
        }

        const allModelsTerminal = Object.values(models).every(
          (result) => result.state !== 'thinking'
        );
        return {
          events,
          thinkingModels,
          isTerminal: task.state === 'done' && allModelsTerminal,
        };
      },
    };
  }

  return { createTaskResultTracker };
});
