- Training a person no longer quietly teaches the LoRA that everyone who
  matches their description is them. When most captions describe the person
  the same way ("an elderly man"), the Train dialog says so and turns on Keep
  "an elderly man" generic, which holds those words to what the model drew
  before training. Its steps are slower, and you can turn it off.
- The sample prompts without the trigger word are now one of someone like the
  person and one of someone who is not, and the run screen labels each with
  what it should do: hold still, or drift when the words were left free.
