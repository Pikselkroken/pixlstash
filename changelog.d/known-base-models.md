- The Models shelf now recognises the base model of files that do not say
  what they were trained on, from their filename or the model-spec block many
  trainers write, so far fewer rows read "not set". A base model worked out
  that way is marked "guessed"; setting one yourself always wins and is never
  overwritten by a rescan.
- Grouping, filtering and sorting by base model on the Models shelf now agree:
  spellings of one base model are one group whichever you pick.
- Recipe chips on a picture and the Checkpoint and LoRA filter menus show the
  name you gave a model on the shelf instead of its filename.
