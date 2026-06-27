# Multi-Modal Damage-Claim Verification

This project looks at the photos attached to a damage claim (a dented car, a cracked laptop, a
crushed package) and decides whether those photos actually back up what the person is claiming. For
each claim it answers three things: do the photos **support** the claim, do they **contradict** it,
or is there simply **not enough information** to tell? It then fills in 14 structured columns
explaining the call, so a human can see exactly why each decision was made.

I built it for the **HackerRank Orchestrate** hackathon in June 2026, a 24-hour event to design and
ship an AI agent. This is the cleaned-up public version. I'm sharing it so you can see how it's put
together and borrow the parts that are useful to you.

---

## Certificate

HackerRank **Certificate of Excellence** for placing **3rd of 1,773** in HackerRank Orchestrate (June 2026):

![HackerRank Orchestrate June 2026 Certificate of Excellence (3rd place), Swayam Mishra](./certificate.gif)

## Score breakdown

For context on the field: HackerRank Orchestrate (June 2026) had **15,295 people register**, **2,039
ship a working agent**, and **1,773 finish the AI interview** (that last group is the field everyone
was scored against). I finished **3rd of those 1,773**, with a total of **70.1 out of 100**.

The score came from four stages. I landed **2nd in the whole field on two of them**, Code and Chat
Transcript, and those two are what carried the overall result:

| Stage | My score | Best in the field | Where I ranked |
|---|---|---|---|
| Technical Review & Final Results (the code) | **27.6 / 30** | 27.9 | 🥈 **2nd of 1,773** |
| Chat Transcript Evaluation | **9.8 / 10** | 9.9 | 🥈 **2nd of 1,773** (tied) |
| Interview Evaluation | 21.3 / 30 | 26.7 | 120th |
| Output Evaluation | 11.4 / 30 | 18.6 | 511th |
| **Total** | **70.1 / 100** | | 🥉 **3rd of 1,773** |

The funnel numbers come from HackerRank's recap; the per-stage scores come from
[`leaderboard-june.json`](./leaderboard-june.json).

## The problem

For a single damage claim, a person gives you:

- a short **chat transcript** describing what went wrong,
- one or more **photos**,
- and they come with a **claim history** (past claims, past rejections, how active they've been lately).

Your job is to read all of that, apply a short **rulebook** that says how much photo evidence each
kind of claim needs, and produce a verdict plus nine other fields (what the damage is, which part is
affected, how bad it is, any risk flags, which photos back the call, whether there's even enough to
go on, and so on).

Three things make this harder than it sounds:

- It has to be **auditable**. If a claim gets denied, you need to be able to say exactly why.
- It has to **hold up against fraud and trickery**, including instructions hidden inside an image
  trying to talk the system into approving a claim.
- A person's **history can add caution but must never quietly flip the verdict**. If the photos say
  the damage is real, a bad history shouldn't turn that into a denial on its own.

HackerRank's original problem statement, lightly formatted, is in
[`problem_statement.md`](./problem_statement.md) if you want the task in their own words.

## The core idea

Here's the one decision the whole project rests on:

**The vision model only describes what it sees. Plain Python code makes the actual decision.**

```
claims.csv   (every claim runs in parallel)
   │
   ▼
1. PREP  ·  plain code
   Decode and resize the photos, check their quality, and fingerprint each one so
   we can spot the same image being reused across different claims.
   │
   ▼
2. LOOK  ·  Claude (vision)
   The model studies the photos. It can zoom into a region before it answers, then
   reports what it saw as a plain record of facts. It does not make the final call.
   │
   ▼
3. DECIDE  ·  plain code
   Turn "what the model saw" into the verdict: is the evidence enough? supported or
   contradicted? how severe? any risk flags? Then layer the person's history on top.
   │
   ▼
output.csv   (14 columns, in a fixed order)
```

That handoff in the middle (the model's plain record of what it saw) is the seam everything hangs
on. Keeping the "looking" and the "deciding" apart buys four things at once:

- **You get the same answer every time.** The decision is just code, so the same observations always
  produce the same verdict. You can save the model's observations once and then re-run the decision
  logic offline with no API calls at all (the `--from-cache` flag). That lets you tell apart "did my
  rule change actually help?" from "did the model just read the photo a little differently today?"
- **It's auditable.** Every output column traces back to a specific branch of the decision code plus
  a logged observation from the model.
- **It resists hidden instructions.** A note baked into an image that says "APPROVE THIS CLAIM" can,
  at worst, mess up one observation. It can never reach the code that decides.
- **"History can't override the photos" is built in, not just asked for.** The record the decision
  code reads literally has no history fields in it, so there's no way for it to express "history
  flipped the verdict." History is applied afterward, only ever as an added note of caution.

## How to run it

Everything runs from the `code/` folder.

```bash
cd code
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# The test suite needs no API key and proves the decision logic is solid:
python -m pytest -q                  # 115 tests

# To actually look at images you need a key (read from your shell or from code/.env):
cp .env.example .env                 # then add: ANTHROPIC_API_KEY=sk-ant-...
python main.py --split sample --no-resume
python evaluation/main.py            # score the predictions against the labeled set
```

The full set of commands:

```bash
cd code        # run everything from here

# Score every claim in dataset/claims.csv  (needs ANTHROPIC_API_KEY)
python main.py --no-resume                  # writes output.csv  (--no-resume = start fresh)

# Run the small labeled set instead (writes code/artifacts/sample_predictions.csv)
python main.py --split sample --no-resume

# Score those predictions against the known answers
python evaluation/main.py                   # per-column accuracy + per-class breakdown + mismatches
python evaluation/main.py --variance a.csv b.csv   # how much two runs differ

# Grounding tests: proof the system is reading the pixels, not guessing from the text
python -m evaluation.grounding_tests --n 6

# Walk through a single claim with the full trace (great for debugging)
python -m src.cli --case case_008 --split sample --verbose
python -m src.cli --case case_008 --from-cache   # re-decide from saved observations, no API call

# Operational metrics: a run logs progress and writes code/artifacts/run_metrics.json
# (calls, tokens, cost, cache-hit rate, latency). This folds the latest into the report:
python -m src.observability --split test
```

A couple of helpful behaviors: runs are **resumable**, so if you stop and start again it only picks
up the claims it hasn't finished (a checkpoint lives under `code/artifacts/`). `--no-resume` forces a
clean run. The default model is `claude-opus-4-8`; you can A/B against another with
`--model claude-sonnet-4-6` (or set `ANTHROPIC_MODEL`).

## How the code is laid out

At the top level:

```
.
├── README.md             you are here
├── problem_statement.md  the original task (HackerRank's wording), formatted
├── dataset/              small fake demo data (see "About the data" below)
│   ├── claims.csv              inputs only (the "test" split)
│   ├── sample_claims.csv       inputs plus the right answers (the "dev" split)
│   ├── user_history.csv        each user's past-claim history
│   ├── evidence_requirements.csv   the minimum-evidence rulebook
│   └── images/{sample,test}/case_XXX/img_N.jpg
└── code/
    ├── src/         the actual solution
    ├── evaluation/  scoring, grounding tests, and the operational report
    ├── tests/       115 tests for the decision logic (no API key needed)
    ├── prompts/     the system prompt, kept under version control
    └── docs/        11 design write-ups: the reasoning behind every choice
```

The `tests/`, `prompts/`, and `docs/` folders round it out: `tests/` holds the unit tests for the
pure layers (the 115-test suite), `prompts/` keeps the system prompt under version control
(`system_v1.md` through `system_v4.md`, with v4 active), and `docs/` has the design write-ups.

## A guide to the files

The agent lives in `code/src/`, split into small pieces that each do one job. Here's what every file
is for.

**Entry point and package top level**

| File | What it does |
|---|---|
| [main.py](code/main.py) | The command you run. It puts `code/` on the path and hands off to `src/main.py`. |
| [src/main.py](code/src/main.py) | The batch runner: read `claims.csv`, write `output.csv` (14 columns, in the exact order). |
| [src/cli.py](code/src/cli.py) | The single-claim debugger, the main tool for error analysis (the `--case` and `--from-cache` commands). |
| [src/config.py](code/src/config.py) | Every tunable in one place: model id, paths, image sizes, caps, and thresholds. |
| [src/schema.py](code/src/schema.py) | The single source of truth: the 14-column output row, the record of observations (the seam between looking and deciding), the allowed values, and the rules that must always hold. |
| [src/prompts.py](code/src/prompts.py) | Assembles the prompt: the stable, cached system prompt (from `prompts/`) plus the per-claim message. |
| [src/agent.py](code/src/agent.py) | The Claude tool-use loop. The only place the raw Anthropic SDK lives; it collects the model's observations. |
| [src/anthropic_client.py](code/src/anthropic_client.py) | A thin factory for the Anthropic client, imported lazily so nothing else has to depend on the SDK. |
| [src/pipeline.py](code/src/pipeline.py) | Per-claim orchestration: pre-checks, the perception loop, the deterministic decision, parallelism, checkpointing, and the audit log. |
| [src/observability.py](code/src/observability.py) | Lightweight run metrics (calls, tokens, cost, cache hits, latency), kept out of the pure layers. |
| [src/errors.py](code/src/errors.py) | A small structured error taxonomy, so a failure on one claim is classified rather than swallowed. |

**`src/perception/`: turning raw photos into clean inputs**

| File | What it does |
|---|---|
| [perception/ingest.py](code/src/perception/ingest.py) | Find the image files, decode them (Pillow), resize them for the model, and drop duplicates. |
| [perception/quality_gate.py](code/src/perception/quality_gate.py) | Cheap, deterministic quality checks (blur, lighting) that corroborate what the model reports. |
| [perception/authenticity_prior.py](code/src/perception/authenticity_prior.py) | A cheap deterministic check that backs up the `possible_manipulation` flag. |
| [perception/fingerprint_store.py](code/src/perception/fingerprint_store.py) | A durable image-fingerprint store (SQLite) for spotting the same photo reused across claims. |

**`src/tools/`: what Claude can call while it looks**

| File | What it does |
|---|---|
| [tools/inspect_image.py](code/src/tools/inspect_image.py) | The zoom tool: a deterministic crop of the original full-resolution image so the model can look closer. |
| [tools/lookup_evidence_requirement.py](code/src/tools/lookup_evidence_requirement.py) | Evidence-rulebook helpers that ground how much evidence each kind of claim needs. |

**`src/decision/`: the decision logic (all pure code)**

| File | What it does |
|---|---|
| [decision/tree.py](code/src/decision/tree.py) | The deterministic decision tree. The first matching branch wins. |
| [decision/evidence.py](code/src/decision/evidence.py) | Is the evidence sufficient? This is the gate that decides "not enough information." |
| [decision/consistency.py](code/src/decision/consistency.py) | Derives contradiction signals (does the object and part match the claim?) from the observations. |
| [decision/aggregate.py](code/src/decision/aggregate.py) | Combines the signals across multiple images (validity, quality and authenticity flags, and so on). |
| [decision/severity.py](code/src/decision/severity.py) | Applies sanity rules to the model's severity estimate (for example, "not enough information" forces "unknown"). |
| [decision/assemble.py](code/src/decision/assemble.py) | Builds the final 14-column row from the observations plus the history overlay. |
| [decision/explain.py](code/src/decision/explain.py) | Writes the short, image-grounded justifications from the logged facts and the decision. |

**`src/risk/`: overlays that only ever add caution**

| File | What it does |
|---|---|
| [risk/history.py](code/src/risk/history.py) | The user-history overlay. Additive only: it can add caution flags but never changes the verdict. |
| [risk/injection.py](code/src/risk/injection.py) | A deterministic screen for instructions hidden in the claim text or in text read off an image. |

**`src/io/`: read the inputs, write the columns**

| File | What it does |
|---|---|
| [io/reader.py](code/src/io/reader.py) | Reads the input CSVs (using the standard library) so the fields round-trip cleanly. |
| [io/writer.py](code/src/io/writer.py) | Writes the 14 output columns in the exact order, as UTF-8. |

**`code/evaluation/`: scoring and grounding tests**

| File | What it does |
|---|---|
| [evaluation/main.py](code/evaluation/main.py) | The scoring entry point. |
| [evaluation/run_eval.py](code/evaluation/run_eval.py) | The metrics for the labeled sample (20 claims): per-column accuracy, per-class recall, and mismatches. |
| [evaluation/grounding_tests.py](code/evaluation/grounding_tests.py) | The grounding tests that prove the system reads the pixels, not the surrounding text. |
| [evaluation/evaluation_report.md](code/evaluation/evaluation_report.md) | The operational write-up: cost, latency, and the manual-review breakdown. |

The files under `decision/` and `risk/` are deliberately boring: plain functions that take values in
and return values out, with no network calls, no file reading, and no shared state. That's why they
can be unit-tested and re-run on saved observations with zero API calls. And because the record they
read carries no history at all, there's no path for history to override what the photos showed.

## A few decisions worth noticing

The longer reasoning for each of these lives in
[`code/docs/DESIGN_REVIEW.md`](code/docs/DESIGN_REVIEW.md). The short version:

- **What triggers "not enough information."** The natural-seeming rule is "if the image isn't valid,
  give up." But one labeled example (`case_008`) had an invalid image and was still a clear
  contradiction. So the system instead asks "is the evidence sufficient to judge this?" and only bails
  out when the answer is no. That version matched all 20 labeled cases.
- **History is only ever an overlay.** It's applied in code after the verdict, and it can add flags
  like "this user's history is risky" or "send this to a human," but it cannot change a supported
  claim into a denial.
- **Photos come first.** To call a claim "supported," the model has to point to a specific, locatable
  visual cue. If it isn't confident, it backs off to "unknown," which becomes "not enough information."
- **The rulebook lives inside the prompt.** Putting the minimum-evidence rules into the (cached)
  system prompt means the model already knows them while it looks, so the only live tools it needs are
  "zoom into the image" and "submit your answer." The model never sees the user's history.
- **The safety layers only ever add caution, never flip a good answer.** For borderline reads, the
  model is asked a few times and the majority wins (and if they disagree, the claim is flagged for a
  human). A simple text screen catches injection attempts in the claim and in any text read off the
  image. Image fingerprints catch the same photo being reused across claims. And every "send to a
  human" decision records exactly why, which is a useful signal for "how much of this could be
  automated."

## Speed, cost, and repeatability

- **Repeatability.** The decision tree, the evidence and severity logic, the risk scoring, and the
  output checks are all plain code, so they're fully repeatable. The only non-repeatable part is the
  model looking at the photo. Since the model's observations are saved, any change to the decision
  logic can be re-checked offline and will give the same result every time.
- **Cost.** The big lever is caching the stable part of the prompt (the instructions, tools, and
  rulebook) so it isn't re-billed on every call. Other levers: resizing images down to the size the
  model actually uses, capping how many times the model can loop, and processing claims in parallel.
  Note that the 50%-off batch pricing only applies to one-shot calls, not to a back-and-forth loop
  like this one. The full cost breakdown is in
  [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md).

## Testing

```bash
cd code && python -m pytest -q      # 115 tests over the decision logic, no API key needed
```

## About the data

This repo ships a **tiny made-up demo dataset** (a handful of fake rows and plain colored placeholder
images) so the project is self-contained and the tests run. **The real challenge dataset is not
here.** It's HackerRank's, and it isn't mine to hand out. With the placeholder images a live run will
mostly answer "not enough information," which is expected: the demo is there to show the input/output
format and to smoke-test the pipeline, not to produce meaningful accuracy. To run it for real, drop
your own data into the same folders and the same format described below.

### Data format

**Inputs**

| File | Columns |
|---|---|
| `claims.csv` | `user_id`, `image_paths` (separated by `;`), `user_claim` (the transcript, turns joined by `\|`), `claim_object` (one of `car`, `laptop`, `package`) |
| `sample_claims.csv` | the 4 input columns plus the 10 answer columns below (the known answers, for scoring) |
| `user_history.csv` | `user_id`, claim counts, `history_flags`, `history_summary` |
| `evidence_requirements.csv` | `requirement_id`, `claim_object`, `applies_to`, `minimum_image_evidence` |

**Output (`output.csv`), 14 columns in a fixed order:** the 4 inputs copied through, then:

| Column | Values |
|---|---|
| `evidence_standard_met` | `true` / `false`. Are the photos good enough to actually judge the claim? This is what drives the "not enough information" verdict. |
| `evidence_standard_met_reason` | free text |
| `risk_flags` | a `;`-joined subset of: `none, blurry_image, cropped_or_obstructed, low_light_or_glare, wrong_angle, wrong_object, wrong_object_part, damage_not_visible, claim_mismatch, possible_manipulation, non_original_image, text_instruction_present, user_history_risk, manual_review_required` |
| `issue_type` | `dent, scratch, crack, glass_shatter, broken_part, missing_part, torn_packaging, crushed_packaging, water_damage, stain, none, unknown` |
| `object_part` | depends on the object: car (`front_bumper, rear_bumper, door, hood, windshield, side_mirror, headlight, taillight, fender, quarter_panel, body`), laptop (`screen, keyboard, trackpad, hinge, lid, corner, port, base, body`), package (`box, package_corner, package_side, seal, label, contents, item`), or `unknown` |
| `claim_status` | `supported` / `contradicted` / `not_enough_information` |
| `claim_status_justification` | free text, grounded in what the photos showed |
| `supporting_image_ids` | the image ids that back the call, joined by `;`, or `none` |
| `valid_image` | `true` / `false`. Are the photos usable for an automated review at all? (This is separate from whether they're sufficient.) |
| `severity` | `none, low, medium, high, unknown` |

## Results

These are measured on the **original challenge dev sample (20 claims)** across two separate live
runs. With only 20 cases these point in the right direction but aren't statistically tight, so read
them as directional:

| Column | Score | Run-to-run stability |
|---|---|---|
| **claim_status** (the main verdict) | 90-95% | 95% |
| **catching contradictions** (the expensive mistake to miss) | 80% (4 of 5), both runs | stable |
| evidence sufficiency / image validity / affected part | 100% / 90% / 90% | 100% |
| issue type / severity | 70-75% | 95% |

A run costs about **$0.10 per claim** with the extra checks turned on, helped a lot by the prompt
cache (around 85% of input tokens came from cache rather than being re-billed). The full per-class
numbers, run-to-run variance, and the cost-and-latency analysis are in
[`code/evaluation/`](code/evaluation/).

## What I'd pass on to you

- **Let the model do what it's good at, and let code do what it's strict about.** Having the model
  only describe and having code decide gave repeatability, auditability, and resistance to hidden
  instructions almost for free, and it turned "history can't override the photos" into something the
  structure guarantees rather than something I had to politely ask for.
- **Caching the model's raw observations was the biggest speed-up for iterating.** Re-running the
  decision logic offline meant I could change a rule and instantly see the effect, without the model's
  day-to-day noise muddying the result.
- **Prove the model is actually looking.** I dropped the image or swapped it and checked that the
  output collapsed or changed. That catches the embarrassing case where a system is secretly reading
  the text and ignoring the photo entirely.
- **Be honest about what didn't move the needle.** Prompt and example tweaks didn't measurably improve
  issue type or severity at this sample size. That's a limit of the data, not the wording, and saying
  so plainly is more useful than picking a flattering number.
- **At 20 cases, every number is roughly give-or-take 5%.** I leaned on the rule layers (which
  generalize by design) and the grounding tests rather than on a headline accuracy figure.
- **The prompt cache is where the cost is won.** Asking the model a few times for borderline cases
  costs about 38% more, but it lifted contradiction-catching from 60% to 80%, which is worth it when a
  wrongly approved claim is the costly mistake.

There are deeper write-ups (system design, the decision engine, the threat model, failure modes, and
the evaluation strategy) in [`code/docs/`](code/docs/).

## A note on reuse

My code here is released under the **MIT License** (see the [`LICENSE`](../LICENSE) file at the repo
root), so you're free to read it, reuse it, and build on it. That license covers my code only.
HackerRank's original problem statement is included for context in
[`problem_statement.md`](./problem_statement.md), and the challenge dataset and the evidence rulebook
belong to HackerRank, so those are deliberately left out and are not mine to license.
