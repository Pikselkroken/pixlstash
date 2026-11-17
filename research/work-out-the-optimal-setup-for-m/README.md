# Research notebook

Everything tried towards one question, and how each attempt came out. Plain files, so any reader
or script can follow it without the tool that wrote it.

| File | What it is |
|---|---|
| `question.md` | the question being researched, in its current wording |
| `question-history.jsonl` | earlier wordings, each with the reason it was changed |
| `ledger.jsonl` | one line per idea: its state, verdict and finding. Generated; read this first |
| `story.json` | which settled ideas the write-up tells, and in which order |
| `order.json` | which untested idea to start next: the order, an estimate of each idea's impact and odds, and which places a person chose |
| `decisions.jsonl` | what a person decided when a test asked: the question and the answer |
| `ideas/<n>-<slug>/idea.json` | one idea, where it came from, and each time it was re-opened |
| `ideas/<n>-<slug>/rounds/<r>/outcome.json` | the verdict, the finding and the evidence list of one test |
| `ideas/<n>-<slug>/rounds/<r>/report.md` | the tables and plots behind that outcome |
| `ideas/<n>-<slug>/rounds/<r>/evidence/` | the plots and tables themselves |
| `ideas/<n>-<slug>/rounds/<r>/code/` | the prototype that produced the evidence |
| `ideas/<n>-<slug>/rounds/<r>/requirements.txt` | the Python packages installed when it ran |

An outcome's `status` is `concluded` once a person accepted it, `superseded` when the round was
sent back, and `provisional` until either. An idea's verdict is the verdict of its latest concluded
round. A negative result is a result: it is concluded and kept exactly as a positive one is.
