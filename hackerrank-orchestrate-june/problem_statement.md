# Problem Statement: Multi-Modal Evidence Review

> The original problem statement and instructions for the **HackerRank Orchestrate** hackathon
> (June 2026), kept here for context. The wording is HackerRank's; I only cleaned up the formatting.
> The exact 14-column output schema is written up in the project [README](./README.md#data-format).

## Overview

Build a system that verifies damage claims using images, a short claim conversation, user history,
and minimum evidence requirements.

Each claim is about one of three object types: **car**, **laptop**, or **package**.

Your system must decide whether the submitted images **support** the user's claim, **contradict** it,
or **do not provide enough information**.

The images are the primary source of truth. The user conversation defines what needs to be checked.
User history can add risk context, but should not override clear visual evidence by itself.

## What the system should do

For each claim, your system should:

- extract the actual damage claim from the conversation,
- inspect one or more submitted images,
- decide whether the image evidence is sufficient,
- identify the visible issue type,
- identify the relevant object part,
- decide whether the claim is supported, contradicted, or lacks enough information,
- select the image IDs that support the decision,
- flag image quality, mismatch, authenticity, or user-history risks,
- estimate severity,
- produce short justifications grounded in the images.

## Claims

A set of claims is provided (as `claims.zip`). Some include the expected output for validation;
others do not. Run every claim through your agent and submit the results as a CSV.

## Requirements

- Must read the provided CSV files and local images.
- Must produce `output.csv` with the exact schema in the problem statement.
- Must include an evaluation workflow.
- Must avoid hardcoded test labels or file-specific answers.

Beyond that you are free to bring your own approach: vision models, language models, structured
prompting, evaluation pipelines, model comparison, or anything else.

## Submissions

Clone the repo from GitHub to get started. You upload three files on the HackerRank platform:

- **Code zip:** your `code/` directory, zipped. Exclude virtual environments, `node_modules`, build
  artifacts, the `data/` corpus, and the `dataset/` folder.
- **Predictions CSV:** your agent's output for `dataset/test.csv` (the filled-in `output.csv`).
- **Chat transcript:** the `log.txt` produced by the chat-transcript logging.

## AI Judge interview

After a successful submission, an AI Judge interview opens and stays available for the next 12 hours.
The AI Judge can see your submission and may ask about your approach, your decisions, and how you
used AI while building the solution. It runs for 30 minutes, and keeping your camera on is required.

Results were announced on June 29, 2026.
