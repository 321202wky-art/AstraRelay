import { history, historyKeymap, defaultKeymap, indentWithTab } from '@codemirror/commands';
import { javascript } from '@codemirror/lang-javascript';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers } from '@codemirror/view';

export function mount(host, initialValue = '', onChange = () => {}) {
  const view = new EditorView({
    state: EditorState.create({
      doc: initialValue,
      extensions: [
        lineNumbers(),
        history(),
        javascript(),
        keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChange(update.state.doc.toString());
          }
        }),
      ],
    }),
    parent: host,
  });

  return {
    getValue: () => view.state.doc.toString(),
    setValue: (value) => {
      view.dispatch({
        changes: {
          from: 0,
          to: view.state.doc.length,
          insert: value,
        },
      });
    },
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}
