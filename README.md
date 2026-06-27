# HackerRank Orchestrate: my hackathon projects

This repo holds my entries to **HackerRank Orchestrate**, a monthly 24-hour event where you design
and ship an AI agent. Each month lives in its own folder with its own data, code, and a single README
that walks through the whole thing. If you're here to learn from the code, start with whichever
project sounds more interesting and read its README top to bottom.

| Month | Project | What it does | Model | Result |
|---|---|---|---|---|
| **June 2026** | [Multi-Modal Damage-Claim Verification](./hackerrank-orchestrate-june/) | Looks at the photos on a damage claim and decides whether they back up the claim, contradict it, or aren't enough to tell. Fills in 14 explained columns. | Claude Opus 4.8 (vision) | 🥉 **3rd of 1,773** |
| **May 2026** | [Support Triage Agent](./hackerrank-orchestrate-may/) | Reads support tickets for three help desks (HackerRank, Claude, Visa) and either answers them from local docs or sends them to a human. Fills in 5 columns. | Claude Haiku 4.5 | **60th of 1,349** |

Each project keeps its own scraped final leaderboard:
[`leaderboard-june.json`](./hackerrank-orchestrate-june/leaderboard-june.json) and
[`leaderboard-may.json`](./hackerrank-orchestrate-may/leaderboard-may.json).

---

## June 2026: Multi-Modal Damage-Claim Verification

The whole project rests on one idea: **the vision model only describes what it sees, and plain code
makes the actual decision.** Keeping those two jobs apart pays off in a few ways. You get the same
answer every time (the decision is just code, so you can save the model's observations and re-run the
logic offline with no API calls). It's auditable, since every column traces back to a specific rule.
And it resists trickery: an instruction hidden inside an image can confuse one observation but can
never reach the code that decides. A person's claim history can add caution, but the structure makes
it impossible for history to flip a verdict on its own.

**[Read the June README](./hackerrank-orchestrate-june/README.md)** for the architecture, the
commands, the decisions, and the results.

## May 2026: Support Triage Agent

A command-line agent that answers or escalates support tickets using only a local set of help docs,
with no internet calls while it runs. Each ticket goes through a pipeline: filter out junk, clean up
the search query, search the docs (fast keyword search, then a small local model re-orders the best
hits), score the confidence, check a few hard escalation rules, have Claude draft a reply, validate
and if needed repair it, strip anything the model made up, and log the reasoning. As in June, the
model only writes; the routing decisions are plain code you can read.

**[Read the May README](./hackerrank-orchestrate-may/README.md)** for setup, a file-by-file guide,
the decisions behind it, and the results.

---

## How this repo is organized

```
.
├── hackerrank-orchestrate-june/   June project (code/, dataset/, README.md, leaderboard-june.json)
├── hackerrank-orchestrate-may/    May project  (code/, data/, support_tickets/, README.md, leaderboard-may.json)
├── .gitignore                     one shared ignore file for the whole repo
└── README.md                      you are here
```

## Good to know

- There's **one `.gitignore` at the top** covering both projects (secrets, virtual environments,
  caches, and generated run files). Each project keeps its own `requirements.txt` and `.env.example`.
- **Secrets stay out of git.** The `.env` files are ignored and never committed. To run either agent,
  copy that project's `.env.example` to `.env` and paste in your own `ANTHROPIC_API_KEY`.
- The challenge **datasets, problem statements, and rulebooks belong to HackerRank.** Each project's
  README says what's included and what isn't.
- **License.** My code is released under the [MIT License](./LICENSE). That covers my code only, not
  HackerRank's problem statements or any bundled help-center data, which stay their owners' property.
