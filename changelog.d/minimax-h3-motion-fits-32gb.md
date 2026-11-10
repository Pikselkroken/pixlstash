- MiniMax H3 · motion now fits a 32 GB GPU: its text encoder stays in RAM while
  clips are encoded, and **Save memory** keeps 90% of the model's layers in RAM
  instead of repeating what Auto picks.
- A training run that does not fit the GPU now says what it peaked at when it
  was measured, and **Advanced** still shows its settings.
