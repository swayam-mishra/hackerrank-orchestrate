# Problem Statement: Multi-Domain Support Triage Challenge

> The original problem statement and instructions for the **HackerRank Orchestrate** hackathon
> (May 2026), kept here for context. The wording is HackerRank's; I only cleaned up the formatting.
> One naming note: HackerRank's text refers to a `support_issues/` folder, while this project uses
> `support_tickets/` for the same files.

## Challenge overview

Build a terminal-based support triage agent that can handle support tickets across three ecosystems:

- **HackerRank Support:** https://support.hackerrank.com/
- **Claude Help Center:** https://support.claude.com/en/
- **Visa Support:** https://www.visa.co.in/support.html

Your agent must use only the provided support corpus to understand the issue, decide whether it can
be answered safely, and determine when it should be escalated to a human.

For each issue, the agent should:

- identify the request type,
- classify the issue into a product area,
- decide whether to reply or escalate,
- retrieve the most relevant support documentation,
- generate a safe, grounded response.

Some cases are simple FAQs. Others involve billing, bugs, fraud, permissions, account access,
assessments, or other sensitive situations that need careful routing.

## Support tickets

A set of support tickets is provided (as `support_tickets.zip`). Some include the expected output for
validation; others do not. Run every ticket through your agent and submit the results as a CSV.

## Requirements

Your solution must:

- be terminal-based,
- use only the provided support corpus,
- avoid unsupported claims or hallucinated policies,
- escalate high-risk, sensitive, or unsupported cases when appropriate.

Those are the must-haves. Beyond them, you're encouraged to add improvements of your own, such as
better retrieval, stronger safety checks, or clearer reasoning.

## Submissions

Clone the repo from GitHub to get started. You upload three files on the HackerRank platform:

- **Code zip:** your `code/` directory, zipped. Exclude virtual environments, `node_modules`, build
  artifacts, the `data/` corpus, and the `support_issues/` CSVs.
- **Predictions CSV:** your agent's output for `support_issues/support_issues.csv` (the filled-in `output.csv`).
- **Chat transcript:** the `log.txt` produced by the chat-transcript logging.

## AI Judge interview

After a successful submission, an AI Judge interview happens within a few hours of the hackathon
ending and stays open for the next 4 hours. The AI Judge can see your submission and may ask about
your approach, your decisions, and how you used AI while building the solution. It runs for 30
minutes, and keeping your camera on is required.

Results were announced on May 15, 2026.
