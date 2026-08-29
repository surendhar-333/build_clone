# 4-week DE interview prep — Surendhar

**Budget:** 4 weeks, 3–4 h/day ≈ **100 hours.**
**Starting point:** strong Oracle SQL/PL-SQL (2+ yrs), Databricks DE Associate cert, big-data theory only, a few days of Spark practice, no production big-data hands-on.
**Target:** DE roles, 2–3 yrs band, Chennai / Bengaluru / Hyderabad.

## The one thing that matters most

Your CV names a Payment Settlement & Reconciliation Lakehouse. **You did not write it and have not run it.** Right now that project is a *liability*, not an asset — any competent interviewer will spend 15 minutes there and find the floor.

Week 1 exists to flip that. Everything else is secondary, because a candidate who can defend one real project beats a candidate who can recite Spark trivia.

## Non-negotiable daily habits (inside the 3–4 h)

- **30 min SQL**, every single day. No exceptions. This is your moat — you are competing against people who are weak here.
- **Write your own answers file.** `docs/answers/` — after learning anything, write the explanation in *your own words*, out loud, then type it. If you can't write it, you don't know it. This becomes your week-4 revision material.
- **Say it out loud.** Interviews are spoken. Silent reading creates false confidence.

---

# WEEK 1 — Own the project + weaponise SQL (≈24 h)

## Day 1–2: Run it end to end (7 h)

Free Edition, `workspace.settlement_recon`. Pull the repo into a Databricks Git folder, run notebooks **01 → 06 in order**.

- Record **row counts at every stage** in `docs/RUN_LOG.md` — bronze in, silver clean, silver rejects, matched, mismatched, unmatched, cases raised.
- Every error you hit: write down the error, the cause, the fix. These become your best interview stories. "It failed with X because Y" is worth more than any polished description.

Goal: you can say **"I ran this, here are the numbers"** — truthfully.

## Day 3: Rewrite the reconciliation engine from scratch (4 h)

Open a blank notebook. **Without looking at Phase 4**, implement the recon yourself: join the two sides, classify each record, raise exception cases.

Then diff yours against the original. Where they differ, work out who is right.

This is the single highest-value exercise in the plan — it converts "generated code" into *your* design, and it is exactly what you'll be asked to whiteboard.

## Day 4: Find and fix the real defects (4 h)

Read `docs/PROJECT_STATE.md`. The code has **genuine bugs**, and fixing them is both the fastest way to learn this codebase and a much stronger interview story than a suspiciously clean project.

Run TASK-02 and TASK-03 (in `prompts/`) through Gemini/ox-alpha, then **review the diffs yourself** — reviewing is where the learning happens. The headline defects:

- `MISMATCH_BOTH` and `UNMATCHED_NETWORK` are **unreachable** — two of six classification branches have never executed
- `AUTO` disposition **never fires** (10% inflation vs a ₹1 tolerance)
- `case_id` uses `row_number()`, so it is **not stable across runs** — while a comment in the code claims it is
- a NULL status is silently classified as **MATCHED**
- re-running Phase 1 **doubles** the Bronze tables
- reject tables are always empty, so the DQ path has never run

Then break it deliberately and log the behaviour:

- duplicate primary keys on one side
- a malformed row / wrong data type
- a schema change (extra column, renamed column)
- re-run the same notebook twice — is it idempotent? prove it

"I found that two of my six match statuses were unreachable because the injection bands were disjoint, so I re-banded the generator" is a *far* better answer than any polished description. Interviewers reward candidates who audit their own work.

## Day 5–7: SQL as your weapon (9 h)

You're strong here — get *sharp*, in DE idiom rather than PL/SQL idiom.

- Window functions until automatic: `ROW_NUMBER / RANK / DENSE_RANK`, `LAG / LEAD`, running totals, `FIRST_VALUE / LAST_VALUE`, frames (`ROWS BETWEEN` vs `RANGE BETWEEN`)
- Dedup: keep-latest-per-key with `ROW_NUMBER`
- Gaps and islands (sessionisation) — very common
- Self-join vs window for "previous row" problems
- **SCD Type 2 written as SQL** — and as a Delta `MERGE`
- Reconciliation in pure SQL (full outer join + classification) — your project, expressed as SQL
- CTEs, `QUALIFY`, recursive CTE for hierarchies
- Be able to state the difference: `WHERE` vs `HAVING` vs `QUALIFY`

**Leverage your PL/SQL past deliberately.** Prepare crisp answers for:

- "Why move from PL/SQL to data engineering?"
- "Difference between a cursor loop and a set-based operation?" — you actually know this; most candidates don't
- "How did you tune a slow query?" — execution plans, indexes, partition pruning. This transfers directly to Spark and you should say so.

---

# WEEK 2 — Spark internals (≈24 h)

Where you'll be grilled hardest and are currently weakest. Theory first, then prove it on your own pipeline.

## Core model (Day 8–9)

- Driver vs executor vs core/slot — who does what
- **Job → Stage → Task.** What creates a new stage? (A shuffle.)
- Narrow vs wide transformations — this distinction is the whole game
- Lazy evaluation, DAG, actions vs transformations
- Catalyst: parse → analyse → optimise → physical plan. Read `df.explain(True)` and narrate it.
- **AQE**: coalescing partitions, skew join handling, switching join strategy at runtime

## Joins and shuffle (Day 10–11)

- Broadcast hash join, sort-merge join, shuffle hash join — how Spark chooses, and `spark.sql.autoBroadcastJoinThreshold`
- When you'd force a broadcast, and when broadcasting blows up the driver
- **Skew**: how you *detect* it (Spark UI — one task far slower) and fixes: salting, AQE skew join, splitting hot keys
- `spark.sql.shuffle.partitions` — why the default 200 is often wrong
- `repartition` vs `coalesce`, and why `coalesce` can starve parallelism
- Spill (memory → disk) and what causes it

## Memory, caching, files (Day 12)

- Executor memory layout; why OOM happens
- `cache` vs `persist`, storage levels, when caching *hurts*
- Small files problem, target file sizes, why 10,000 tiny files is a disaster
- Parquet: columnar, predicate pushdown, column pruning, row groups. Parquet vs ORC vs Avro vs CSV — and *why* Parquet for analytics.

## Prove it (Day 13–14)

Run **Phase 6's scale lab** — it exists in the repo and has never been executed. 1M rows, then 10M.

- Open the **Spark UI**. Find the stages, the shuffle, the slowest task.
- Change `shuffle.partitions`, re-run, record the difference.
- Run `OPTIMIZE` / Z-ORDER and measure before/after.
- Log everything in `docs/RUN_LOG.md`.

**This gives you real numbers.** "I ran 10M rows and cut runtime from X to Y by doing Z" separates you from every other 2-year candidate. Your CV currently claims no numbers on purpose — this is how you earn them.

---

# WEEK 3 — Delta, Databricks, modelling (≈24 h)

## Delta Lake (Day 15–17)

- `_delta_log`: JSON commits + checkpoints. **Open it and look at it.**
- How ACID works without a database — optimistic concurrency, atomic commit
- Time travel (`VERSION AS OF`) and its limits
- `MERGE`: syntax, and what it does mechanically (join + rewrite files)
- `OPTIMIZE`, bin-packing, **Z-ORDER**, liquid clustering
- `VACUUM` and the 7-day retention trap
- Schema evolution vs enforcement, `mergeSchema`, `overwriteSchema`
- Deletion vectors; copy-on-write vs merge-on-read
- Partitioning a Delta table — and why over-partitioning is a common mistake

## Databricks platform (Day 18–19)

- **Auto Loader**: `cloudFiles`, schema inference + evolution, `_rescued_data`, checkpoints, `availableNow` vs continuous. You use all of this in Phase 2 — be able to explain every option you pass.
- Structured Streaming: micro-batch, triggers, checkpointing, watermarks, exactly-once, idempotent sinks
- Medallion architecture — and *why* Silver is the type-enforcement boundary. That's your design decision; own it.
- Unity Catalog: 3-level namespace, volumes vs external locations, lineage
- Jobs/Workflows: tasks, dependencies, retries, parameters
- DLT vs plain notebooks — know what DLT gives you even if you haven't used it

## Modelling + warehousing (Day 20–21)

- Star vs snowflake; fact vs dimension; **grain** — be able to state the grain of each gold table
- SCD 1 / 2 / 3; surrogate keys; late-arriving dimensions
- OLTP vs OLAP; normalisation vs denormalisation for analytics
- Idempotency and reprocessing; watermark vs high-water-mark loads
- Data quality: what you check and what you do with failures. You have reject tables — that's your answer.

---

# WEEK 4 — Cloud, orchestration, drilling (≈24 h)

## AWS crash + one real pipeline (Day 22–24)

Use your credits. Keep it small and *finish* it.

- S3: buckets, prefixes, storage classes, partitioned layout
- Glue Data Catalog; Glue ETL vs **EMR Serverless**; Athena
- Build **one** pipeline: raw CSV in S3 → transform → partitioned Parquet in S3 → query via Athena. That's it. Small and complete beats big and half-done.

Then you can honestly say: S3, Glue Catalog, Athena, partitioned Parquet.

## Orchestration (Day 25)

- Airflow: DAG, task, operator, scheduler, executor
- Schedule vs data interval, **backfill**, catchup
- Retries, SLAs, sensors, idempotent task design
- Write one trivial DAG so you've actually touched it
- Compare with Databricks Workflows, which you *have* used

## Behavioural + narrative (Day 26)

- **Why are you leaving Cognizant?** Forward-looking, never bitter.
- Why data engineering, coming from PL/SQL
- Hardest bug you debugged — use a real one from Week 1 Day 4
- Salary: you're at ₹4.8L asking ₹8L. Rehearse saying the number without flinching, with your justification ready (cert, project, DE skill set).
- Questions *you* ask them — signals seniority

## Mock interviews (Day 27–28)

6–8 timed sessions, spoken aloud, recorded. Structure:

1. 90-second intro
2. 5-minute project pitch
3. 20 min live SQL
4. 20 min Spark/Delta grilling
5. Project deep-dive with follow-ups

Use another model as the interviewer — see `prompts/MOCK-INTERVIEW.md`.

---

# The project pitch — memorise the shape, not the words

**90 seconds:** "I built a payment settlement and reconciliation lakehouse on Databricks. It takes an internal settlement feed and a network/bank feed and works out where they disagree — missing records, amount mismatches, status mismatches. It's medallion: Auto Loader lands files into Bronze Delta tables, Silver is where I enforce types and quarantine bad rows, and Gold is a full-outer-join reconciliation engine that classifies every record and raises exception cases with a tolerance rule, so small differences auto-clear and big ones go to manual review."

Then stop. Let them ask.

## Questions you WILL be asked — have real answers

1. Why a full outer join and not a left join?
2. What's your join key, and what if it isn't unique?
3. What happens if the same file lands twice?
4. Why enforce types in Silver and not Bronze?
5. What's in your reject table, and who looks at it?
6. What is the tolerance rule, and who decided the threshold?
7. How are case IDs generated — are they stable across re-runs?
8. How would this handle late-arriving data?
9. How far does it scale? What broke first?
10. What would you do differently now?

**If you can't answer these, you're not ready to have this project on your CV.**

---

# What will still be weak at week 4 — steer around it

Four weeks cannot manufacture production experience. You'll still be thin on:

- **Real scale** — 10M synthetic rows, not TB of production data
- **Kafka / real streaming** at scale
- **dbt, Snowflake, Airflow in anger** — concepts, not scars
- **CI/CD for data**, testing frameworks, data contracts
- **Cost optimisation** war stories

Handle it by saying you haven't, then redirecting: "I haven't run Kafka in production — my streaming exposure is Auto Loader and Structured Streaming on Databricks. What I have done is X." Interviewers respect that far more than bluffing, and bluffing here gets caught in one follow-up.

Also: **never claim Azure.** `settlement-platform` is a design document with no code behind it. If asked about cloud, the honest answer is Databricks (managed) plus the AWS pipeline you build in week 4.
