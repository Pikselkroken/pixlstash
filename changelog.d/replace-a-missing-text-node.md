- A text node your ComfyUI does not have (WAS's **Text Multiline**,
  Comfyroll's **CR Text**, Chibi-Nodes' **Textbox**, or core
  **PrimitiveStringMultiline** on an older ComfyUI) no longer stops a run.
  PixlStash writes its text straight into whatever it fed and runs without it,
  and a prompt typed into the Run popup now reaches such a node. The Run popup
  says so before you press Run. A WAS text holding a `[token]`, or a Textbox
  whose passthrough is set, still asks for the pack.
