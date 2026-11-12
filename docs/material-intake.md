# Material intake

You can bring your own material into a case: an interview, a source's email, field notes, a document you were handed, a dataset, a photo or a video. Spotlight keeps the original, records where it came from, takes exact passages from it, and lets you decide which claims drawn from those passages go into your knowledge. This is an optional step. It is not a phase and it is not a gate, and it involves no investigation and no fact-check.

The rules the code follows are in `AGENTS.md` ("Material intake" and "Protected cases"). This page is the user's view.

## Add material

Put a file in the case, or let Spotlight copy it:

```sh
scripts/spotlight-python scripts/intake.py CASE_DIR add --file ~/Desktop/interview-notes.txt
scripts/spotlight-python scripts/intake.py CASE_DIR scan
```

`add` copies one file into `CASE_DIR/intake/` and drafts its record. `scan` drafts a record for every file you already dropped under `intake/`. Each kind has its own folder (`interviews/`, `emails/`, `notes/`, `documents/`, `datasets/`, `media/`, `other/`). A file whose kind its name does not give, such as a PDF or an audio file, waits in `intake/unsorted/` until you name its kind. A recording or a scan holds no text to quote, so a text version (a transcript) is added next to it with `intake.py derive`. The original is never changed.

## What the Intake page asks

Open the Intake page from the review server's "Material intake" button, or, in a chat app with the Spotlight review MCP, ask the agent to open it. For each item it asks you to:

1. Check and confirm the record: its kind, who captured it, who the source is (a name or a pseudonym), who holds the rights, and how sensitive the original is. A new record starts as restrictive as possible (`case_only`), so anything you leave unsaid stays unpublishable. A confirmed record does not change.
2. Look at each claim next to the passage it came from, with a few lines of the original around it, and keep, edit or drop it.
3. Set each passage's attribution (on the record, background, not for attribution, or off the record), consent, and sensitivity. You can set defaults once for the whole item.
4. Approve the kept claims of that item once none is left undecided. Changing the item's passages or claims afterwards removes the approval.

A passage is accepted only when its text appears in the original word for word, at one place. An approved claim enters your knowledge as what the source said. It carries no fact-check verdict.

## When there is no investigation

A case that holds approved material and no brief goes from your approval straight to the Ingest decision. A case with an investigation shows the approved claims in the same Ingest batch as its findings.

## What leaves your computer

Spotlight sends nothing. The original stays in the case folder. In the solo edition, approved claims and their passages are written to your wiki when you approve Ingest (`docs/knowledge-destination.md`). In the newsroom edition the material is held in the case: the newsroom's service does not accept it yet.

If you ask a model to propose passages, that model reads the material. When that must not reach a cloud model, use a protected case.

## Protected cases

A cases folder named `cases_protected`, or one that contains a file named `.spotlight-protected`, is protected. The Pi harness runs every case in it with no web, no cloud model and no research swarm, and writes it to that folder's own wiki (`cases_protected/wiki`), never to your main wiki. Material marked `case_only` can be written to a wiki only from a protected case.

This is enforced in the Pi harness. A runtime that can run shell commands can still read the folder, and Spotlight does not encrypt it. For a stronger boundary, see [sensitivity.md](sensitivity.md).
